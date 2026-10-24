"""WhisperTranscriber 唯一入口。

职责：
1. 检查并安装 Python 依赖（清华镜像加速；由 exe 启动器拉起时同样生效）；
2. 检查 CUDA / 修复 DLL；
3. 启动 PyQt5 界面。

用法：
    python main.py               正常启动
    python main.py --diagnose    打印环境/模型诊断信息（exe 模式下同时写入日志文件）
    python main.py --no-install  跳过自动安装依赖
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.paths import app_data_dir, is_frozen  # noqa: E402

APP_NAME = "WhisperTranscriber"
APP_VERSION = "1.1.0"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

REQUIREMENTS = (
    ("faster_whisper", "faster-whisper>=1.0.0"),
    ("modelscope", "modelscope>=1.9.0"),
    ("PyQt5", "PyQt5>=5.15.0"),
    ("tqdm", "tqdm>=4.65.0"),
    ("requests", "requests>=2.28.0"),
)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class _Tee:
    """把 print/异常输出同时写到原流和日志文件。"""

    def __init__(self, stream, handle) -> None:
        self._stream = stream
        self._handle = handle

    def write(self, data):
        if self._stream is not None:
            try:
                self._stream.write(data)
            except Exception:
                pass
        try:
            self._handle.write(data)
            self._handle.flush()
        except Exception:
            pass
        return len(data)

    def flush(self):
        for target in (self._stream, self._handle):
            if target is not None:
                try:
                    target.flush()
                except Exception:
                    pass

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"


def setup_logging() -> None:
    """没有控制台（exe 启动器 / pythonw）时，把输出写一份到用户数据目录。"""
    if not (is_frozen() or sys.stdout is None or sys.stderr is None):
        return
    try:
        log_dir = app_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)


def _message_box(title: str, message: str, error: bool = False) -> None:
    """没有控制台时的兜底提示：启动失败也能让用户看到原因。"""
    if os.name == "nt":
        try:
            import ctypes

            flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(None, message, title, flags)
            return
        except Exception:
            pass
    print(f"[{title}] {message}", flush=True)


def missing_requirements() -> list:
    return [
        package
        for module, package in REQUIREMENTS
        if importlib.util.find_spec(module) is None
    ]


def install_packages(packages: list) -> bool:
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
    no_console = sys.stdout is None or sys.stderr is None
    if no_console:
        _message_box(
            "首次运行：安装依赖",
            "第一次运行需要安装依赖（约 1-3 分钟）。\n"
            "点击确定后会打开一个命令行窗口显示安装进度，安装完成后会自动启动界面。",
        )
    print("[启动] 正在安装依赖：" + " ".join(packages), flush=True)
    kwargs = {}
    if os.name == "nt" and no_console:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.check_call(command, **kwargs)
    except subprocess.CalledProcessError as exc:
        print(f"[启动] 依赖安装失败（返回码 {exc.returncode}）", flush=True)
        return False
    importlib.invalidate_caches()
    return True


def ensure_dependencies() -> bool:
    missing = missing_requirements()
    if not missing:
        return True
    print("[启动] 检测到缺失依赖：" + ", ".join(missing), flush=True)
    if not install_packages(missing):
        print(
            "[启动] 请手动执行：pip install -r requirements.txt -i " + PIP_MIRROR,
            flush=True,
        )
        return False
    still_missing = missing_requirements()
    if still_missing:
        print("[启动] 仍然缺少：" + ", ".join(still_missing), flush=True)
        return False
    print("[启动] 依赖安装完成", flush=True)
    return True


def diagnose() -> int:
    from core import env_checker
    from core.config import load_config
    from core.models import BUILTIN_MODELS, builtin_model_dir

    lines = [env_checker.format_report(env_checker.ensure_runtime(auto_install=False)), ""]
    lines.append("== 内置模型 ==")
    for spec in BUILTIN_MODELS:
        found = builtin_model_dir(spec.key)
        lines.append(f"{spec.key:16s} {'已就绪 ' + str(found) if found else '未下载'}")
    config = load_config()
    lines.append("")
    lines.append("== 导入的模型 ==")
    imported = config.get("imported_models") or []
    if imported:
        lines.extend(f"{item}" for item in imported)
    else:
        lines.append("（无）")
    lines.append(f"当前选择：{config.get('selected_model')}")

    text = "\n".join(lines)
    print(text)

    try:
        log_dir = app_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        target = log_dir / "diagnose.txt"
        target.write_text(text, encoding="utf-8")
        print(f"\n诊断结果已写入：{target}")
    except OSError as exc:
        print(f"\n诊断结果写入失败：{exc}")
    return 0


def self_test() -> int:
    """检查打包后的关键依赖是否都能正常 import（exe 专用体检）。"""
    modules = (
        "faster_whisper",
        "ctranslate2",
        "av",
        "onnxruntime",
        "tokenizers",
        "modelscope",
        "PyQt5",
    )
    lines = ["== 依赖自检 =="]
    failed = 0
    for name in modules:
        try:
            module = importlib.import_module(name)
            version = getattr(module, "__version__", "?")
            lines.append(f"OK   {name} {version}")
        except Exception as exc:
            failed += 1
            lines.append(f"FAIL {name}: {exc}")

    try:
        from faster_whisper import WhisperModel  # noqa: F401

        lines.append("OK   faster_whisper.WhisperModel")
    except Exception as exc:
        failed += 1
        lines.append(f"FAIL faster_whisper.WhisperModel: {exc}")

    try:
        from modelscope import snapshot_download  # noqa: F401

        lines.append("OK   modelscope.snapshot_download")
    except Exception as exc:
        failed += 1
        lines.append(f"FAIL modelscope.snapshot_download: {exc}")

    text = "\n".join(lines)
    print(text)
    try:
        log_dir = app_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "selftest.txt").write_text(text, encoding="utf-8")
        print(f"\n自检结果已写入：{log_dir / 'selftest.txt'}")
    except OSError as exc:
        print(f"\n自检结果写入失败：{exc}")
    return 0 if failed == 0 else 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Whisper 桌面转录器")
    parser.add_argument("--diagnose", action="store_true", help="只做环境诊断，不启动界面")
    parser.add_argument("--self-test", action="store_true", help="检查依赖是否完整（打包后体检）")
    parser.add_argument("--no-install", action="store_true", help="跳过自动安装依赖")
    args = parser.parse_args(argv)

    configure_stdio()
    setup_logging()
    print(f"===== {APP_NAME} v{APP_VERSION} =====", flush=True)

    frozen = is_frozen()
    install_enabled = (not args.no_install) and (not frozen)
    if frozen:
        print("[启动] 运行在打包模式，跳过依赖安装", flush=True)

    if install_enabled and not ensure_dependencies():
        _message_box(
            "依赖安装失败",
            "依赖安装失败，请检查网络后重试。\n\n日志：\n"
            + str(app_data_dir() / "logs" / "app.log")
            + "\n\n也可以手动执行：\npip install -r requirements.txt -i "
            + PIP_MIRROR,
            error=True,
        )
        return 1

    if args.self_test:
        return self_test()

    if args.diagnose:
        return diagnose()

    from core import env_checker

    env_checker.ensure_runtime(auto_install=install_enabled)

    from ui.app import launch

    try:
        return launch()
    except Exception:
        traceback.print_exc()
        _message_box(
            "启动失败",
            "程序启动失败，请查看日志：\n" + str(app_data_dir() / "logs" / "app.log"),
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
