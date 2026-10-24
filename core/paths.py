"""路径与运行形态（源码运行 / 打包为 exe）的统一入口。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

_APP_DATA_CACHE: Optional[Path] = None


def project_root() -> Path:
    """项目目录（源码运行时的根目录）。"""
    return Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """exe 所在目录（源码运行时等于项目根目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return project_root()


def resource_path(relative: str) -> Path:
    """读取随程序分发的资源（图标等）。"""
    base = Path(getattr(sys, "_MEIPASS", app_dir())) if is_frozen() else project_root()
    return base / relative


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def app_data_dir() -> Path:
    """用户级数据目录：配置、日志、模型。

    优先 %LOCALAPPDATA%\\WhisperTranscriber；如果该目录不可写（受限账户、
    只读环境等），依次退回 exe 旁边的 data/ 和临时目录，保证程序仍能启动。
    """
    global _APP_DATA_CACHE
    if _APP_DATA_CACHE is not None:
        return _APP_DATA_CACHE

    candidates = []
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        candidates.append(Path(base) / "WhisperTranscriber")
    candidates.append(app_dir() / "data")
    candidates.append(Path(tempfile.gettempdir()) / "WhisperTranscriber")

    for candidate in candidates:
        if _is_writable(candidate):
            _APP_DATA_CACHE = candidate
            return candidate
    _APP_DATA_CACHE = Path(tempfile.gettempdir()) / "WhisperTranscriber"
    return _APP_DATA_CACHE


def default_models_dir() -> Path:
    """模型缓存根目录。

    可用环境变量 WHISPER_MODEL_DIR 覆盖（显式指定，不做任何自动猜测）。
    exe 模式放到用户数据目录，避免安装目录没有写权限。
    """
    override = os.environ.get("WHISPER_MODEL_DIR")
    if override:
        return Path(override).expanduser()
    if is_frozen():
        return app_data_dir() / "models"
    return project_root() / "models"


def default_output_dir() -> Path:
    return Path.home() / "Videos"
