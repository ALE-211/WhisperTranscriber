"""运行时环境检查与修复。

这里只关心三件事：
1. ctranslate2 装在哪个目录；
2. NVIDIA pip 包（cublas / cuda-runtime / cudnn）里的 DLL 能否被找到；
3. 最终这台机器能用 GPU（cuda + float16），还是只能退回 CPU（int8）。

设计原则：任何一步失败都不要抛异常打断启动，只记录到报告的 notes 里，
由上层决定提示用户还是自动降级。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import site
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.paths import app_dir, is_frozen

Log = Callable[[str], None]

PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
CUDA_PIP_PACKAGES = (
    "nvidia-cublas-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cudnn-cu12",
)

# ctranslate2 4.x 需要 cuBLAS 12 / cuDNN 9
CORE_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll")
CUDNN_DLL_GLOB = "cudnn*64_9.dll"

# add_dll_directory 返回的句柄必须一直持有，否则 Windows 会撤掉搜索路径
_DLL_HANDLES: List[object] = []


def _print_log(message: str) -> None:
    print(message, flush=True)


def host_python_site_dirs() -> List[Path]:
    """打包成 exe 后，仍然可以借用本机 Python 里装好的 CUDA 运行库。"""
    patterns: List[Path] = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if appdata:
        patterns.append(Path(appdata) / "Python")
    if localappdata:
        patterns.append(Path(localappdata) / "Programs" / "Python")
    if program_files:
        patterns.append(Path(program_files))
    if program_files_x86:
        patterns.append(Path(program_files_x86))

    result: List[Path] = []
    for base in patterns:
        if not base.is_dir():
            continue
        try:
            python_dirs = sorted(base.glob("Python3*"))
        except OSError:
            continue
        for python_dir in python_dirs:
            for candidate in (
                python_dir / "site-packages",
                python_dir / "Lib" / "site-packages",
            ):
                if candidate.is_dir():
                    result.append(candidate)
    return result


def site_package_dirs() -> List[Path]:
    """收集所有可能的包目录（含 Python 前缀目录、用户目录、ctranslate2 所在目录）。"""
    candidates: List[Path] = []
    try:
        candidates.extend(Path(p) for p in site.getsitepackages())
    except Exception:
        pass
    try:
        candidates.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    candidates.extend(
        [
            Path(sys.prefix),
            Path(sys.prefix) / "Lib" / "site-packages",
            Path(sys.exec_prefix),
        ]
    )

    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass))
        candidates.append(app_dir())
        candidates.extend(host_python_site_dirs())

    spec = importlib.util.find_spec("ctranslate2")
    if spec and spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            path = Path(location)
            candidates.extend([path, path.parent])

    result: List[Path] = []
    seen = set()
    for path in candidates:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        result.append(path)
    return result


def find_ctranslate2_dir() -> Optional[Path]:
    """定位 ctranslate2 包目录（DLL 拷贝的目标目录）。"""
    spec = importlib.util.find_spec("ctranslate2")
    if spec and spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            if Path(location).is_dir():
                return Path(location)
    for base in site_package_dirs():
        candidate = base / "ctranslate2"
        if candidate.is_dir():
            return candidate
    return None


def find_nvidia_dll_dirs() -> List[Path]:
    """找出 site-packages/nvidia/*/bin 这类包含 DLL 的目录。"""
    found: List[Path] = []
    for base in site_package_dirs():
        nvidia = base / "nvidia"
        if not nvidia.is_dir():
            continue
        try:
            children = sorted(nvidia.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            for relative in ("bin", "lib/x64", "lib"):
                candidate = child / relative
                if candidate.is_dir() and any(candidate.glob("*.dll")):
                    found.append(candidate)

    result: List[Path] = []
    seen = set()
    for path in found:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def register_dll_dirs(dll_dirs: List[Path], log: Log = _print_log) -> None:
    """把 NVIDIA 的 bin 目录挂进 DLL 搜索路径（进程级，不修改系统环境变量）。"""
    for directory in dll_dirs:
        directory = Path(directory)
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
            except OSError as exc:  # pragma: no cover - 极端权限问题
                log(f"[环境] 无法注册 DLL 目录 {directory}: {exc}")


def detect_cuda_support() -> bool:
    """尝试让 ctranslate2 列出 CUDA 支持的精度；失败即视为不可用。"""
    try:
        import ctranslate2

        compute_types = ctranslate2.get_supported_compute_types("cuda")
    except Exception:
        return False
    return "float16" in compute_types or "int8_float16" in compute_types


def detect_device(log: Log = _print_log) -> tuple:
    """返回 (device, compute_type)。GPU 可用就用 cuda/float16。"""
    if detect_cuda_support():
        return "cuda", "float16"
    log("[环境] 未检测到可用的 CUDA 运行库，将使用 CPU（速度会明显变慢）")
    return "cpu", "int8"


def _copy_dlls(ct2_dir: Path, dll_dirs: List[Path], log: Log) -> List[str]:
    """把 cublas / cudart / cudnn 的核心 DLL 复制到 ctranslate2 目录（DLL 兜底方案）。"""
    copied: List[str] = []
    wanted: List[str] = list(CORE_DLLS)
    for directory in dll_dirs:
        wanted.extend(path.name for path in directory.glob(CUDNN_DLL_GLOB))

    seen = set()
    for name in wanted:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        destination = ct2_dir / name
        if destination.exists():
            continue
        source = None
        for directory in dll_dirs:
            candidate = directory / name
            if candidate.is_file():
                source = candidate
                break
        if source is None:
            continue
        try:
            shutil.copy2(source, destination)
            copied.append(name)
            log(f"[环境] 已复制 {name} -> ctranslate2")
        except OSError as exc:
            log(f"[环境] 复制 {name} 失败（可尝试用管理员身份运行）：{exc}")
    return copied


def _pip_install(packages: tuple, log: Log) -> bool:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        *packages,
        "-i",
        PIP_MIRROR,
        "--disable-pip-version-check",
    ]
    log("[环境] 正在安装 CUDA 运行库：" + " ".join(packages))
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=1800
        )
    except Exception as exc:
        log(f"[环境] 安装 CUDA 运行库失败：{exc}")
        return False
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-5:]
        log("[环境] pip 安装失败：" + " | ".join(tail))
        return False
    log("[环境] CUDA 运行库安装完成")
    return True


def ensure_runtime(log: Log = _print_log, auto_install: bool = True) -> Dict[str, object]:
    """启动时调用一次：注册 DLL 路径，必要时静默修复，最后给出可用设备。"""
    report: Dict[str, object] = {
        "cuda_ok": False,
        "device": "cpu",
        "compute_type": "int8",
        "ctranslate2_dir": None,
        "nvidia_dll_dirs": [],
        "copied": [],
        "notes": [],
    }
    notes: List[str] = report["notes"]  # type: ignore[assignment]

    ct2_dir = find_ctranslate2_dir()
    report["ctranslate2_dir"] = str(ct2_dir) if ct2_dir else None
    if ct2_dir is None:
        notes.append("未找到 ctranslate2，请先执行 pip install faster-whisper")
        return report

    dll_dirs = find_nvidia_dll_dirs()
    register_dll_dirs(dll_dirs, log)
    report["nvidia_dll_dirs"] = [str(p) for p in dll_dirs]

    if detect_cuda_support():
        report.update(cuda_ok=True, device="cuda", compute_type="float16")
        log("[环境] CUDA 运行库就绪，将使用 GPU 转录")
        return report

    if not dll_dirs and auto_install and not is_frozen():
        log("[环境] 未检测到 NVIDIA CUDA 运行库，尝试自动安装（首次约需下载 1-2 GB）...")
        if _pip_install(CUDA_PIP_PACKAGES, log):
            dll_dirs = find_nvidia_dll_dirs()
            register_dll_dirs(dll_dirs, log)
            report["nvidia_dll_dirs"] = [str(p) for p in dll_dirs]

    if dll_dirs:
        copied = _copy_dlls(ct2_dir, dll_dirs, log)
        report["copied"] = copied
        if copied:
            log("[环境] 已补齐 ctranslate2 缺失的 DLL")

    if detect_cuda_support():
        report.update(cuda_ok=True, device="cuda", compute_type="float16")
        log("[环境] CUDA 修复成功，将使用 GPU 转录")
    else:
        if is_frozen():
            notes.append(
                "CUDA 不可用，已回退到 CPU。若需 GPU 加速，请安装 nvidia-cublas-cu12 / "
                "nvidia-cuda-runtime-cu12 / nvidia-cudnn-cu12，或把 nvidia 文件夹放到程序目录。"
            )
        else:
            notes.append(
                "CUDA 不可用，已回退到 CPU。若需 GPU 加速，请确认显卡驱动正常，并执行："
                "pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12"
            )
        log("[环境] " + notes[-1])
    return report


def format_report(report: Dict[str, object]) -> str:
    lines = [
        "== 运行环境检查 ==",
        f"运行形态    : {'打包 exe' if is_frozen() else 'Python 源码'}",
        f"Python      : {sys.version.split()[0]} ({sys.executable})",
        f"FFmpeg      : {shutil.which('ffmpeg') or '未找到'}",
        f"FFprobe     : {shutil.which('ffprobe') or '未找到'}",
        f"ctranslate2 : {report.get('ctranslate2_dir') or '未找到'}",
        f"CUDA 可用   : {report.get('cuda_ok')}",
        f"运行设备    : {report.get('device')} / {report.get('compute_type')}",
    ]
    dll_dirs = report.get("nvidia_dll_dirs") or []
    if dll_dirs:
        lines.append("NVIDIA DLL  : " + "; ".join(str(p) for p in dll_dirs))
    copied = report.get("copied") or []
    if copied:
        lines.append("已复制 DLL  : " + ", ".join(str(p) for p in copied))
    for note in report.get("notes") or []:
        lines.append("提示        : " + str(note))
    return "\n".join(lines)
