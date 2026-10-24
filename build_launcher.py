"""生成轻量 exe 启动器（约 80 KB，不打包 Python 依赖）。

原理：用 Windows 自带的 C# 编译器编译 launcher/Launcher.cs，
生成一个只负责“找到 pythonw.exe -> 运行 main.py”的小程序，同时生成一个快捷方式。

用法：
    python build_launcher.py

产物：WhisperTranscriber.exe + WhisperTranscriber.lnk
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "launcher" / "Launcher.cs"
ICON = ROOT / "assets" / "icon.ico"
OUTPUT = ROOT / "WhisperTranscriber.exe"

WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
CSC_CANDIDATES = (
    WINDIR / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
    WINDIR / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
)


def find_compiler() -> Path:
    for candidate in CSC_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("找不到 csc.exe（需要 .NET Framework 4.x，Windows 10/11 自带）")


def ensure_icon() -> Path:
    if ICON.is_file():
        return ICON
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("缺少 assets/icon.ico，且没有安装 Pillow（pip install pillow）")
    source = ROOT / "assets" / "logo256.png"
    if not source.is_file():
        raise SystemExit(f"缺少图标文件：{source}")
    image = Image.open(source).convert("RGBA")
    image.save(
        ICON,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return ICON


def make_shortcut() -> None:
    """生成指向已签名 pythonw.exe 的快捷方式（不触发发布者提示）。"""
    pythonw = shutil.which("pythonw.exe")
    if not pythonw:
        print("[launcher] 未找到 pythonw.exe，跳过快捷方式生成")
        return
    link = ROOT / "WhisperTranscriber.lnk"
    main_py = ROOT / "main.py"
    icon = ROOT / "assets" / "icon.ico"
    # PowerShell 里用 [char]34 拼双引号，避免 Python / cmd 的多层转义
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('{link}'); "
        "$s.TargetPath = '{pythonw}'; "
        "$s.Arguments = [char]34 + '{main_py}' + [char]34; "
        "$s.WorkingDirectory = '{root}'; "
        "$s.IconLocation = '{icon}'; "
        "$s.Save()"
    ).format(link=link, pythonw=pythonw, main_py=main_py, root=ROOT, icon=icon)
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print("[launcher] 快捷方式生成失败（不影响 exe）：" + completed.stderr.strip())
        return
    print(f"[launcher] 快捷方式：{link}")


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(f"缺少启动器源码：{SOURCE}")
    compiler = find_compiler()
    icon = ensure_icon()

    command = [
        str(compiler),
        "/nologo",
        "/target:winexe",
        "/optimize+",
        "/reference:System.dll",
        "/reference:System.Windows.Forms.dll",
        "/reference:System.Drawing.dll",
        f"/win32icon:{icon}",
        f"/out:{OUTPUT}",
        str(SOURCE),
    ]
    print("[launcher] 编译启动器 ...")
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr)
        raise SystemExit("编译失败")

    size_kb = OUTPUT.stat().st_size / 1024
    print(f"[launcher] 完成：{OUTPUT}（{size_kb:.0f} KB）")
    make_shortcut()
    print("[launcher] 双击 exe 或快捷方式即可启动；需要本机已安装 Python 3 和项目依赖")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
