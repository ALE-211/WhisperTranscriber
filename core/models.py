"""模型清单与本地模型校验。

模型仓库全部使用 ModelScope（魔搭），已验证可直连、下载快、文件完整。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from core.paths import default_models_dir

MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
MIN_MODEL_BYTES = 50 * 1024 * 1024  # 小于这个体积说明没下完
MODEL_REVISION = "master"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    repo_ids: Tuple[str, ...]
    size_mb: int
    note: str = ""

    @property
    def size_text(self) -> str:
        if self.size_mb >= 1024:
            return f"{self.size_mb / 1024:.1f} GB"
        return f"{self.size_mb} MB"

    @property
    def cache_dir(self) -> Path:
        return default_models_dir() / self.key


BUILTIN_MODELS: Tuple[ModelSpec, ...] = (
    ModelSpec(
        key="large-v3-turbo",
        label="large-v3-turbo（推荐 · 快且中文准确）",
        repo_ids=("Tiandong/faster-whisper-large-v3-turbo-ct2",),
        size_mb=1543,
        note="显存约 4-6 GB，55 分钟视频 RTX 3080 约 10-20 分钟",
    ),
    ModelSpec(
        key="large-v3",
        label="large-v3（最准确 · 较慢）",
        repo_ids=(
            "Systran/faster-whisper-large-v3",
            "pengzhendong/faster-whisper-large-v3",
        ),
        size_mb=2944,
        note="显存约 8-10 GB，10 GB 显卡接近上限",
    ),
    ModelSpec(
        key="medium",
        label="medium（均衡）",
        repo_ids=(
            "Systran/faster-whisper-medium",
            "pengzhendong/faster-whisper-medium",
        ),
        size_mb=1457,
        note="精度略低于 large，速度明显更快",
    ),
    ModelSpec(
        key="small",
        label="small（轻量 · 适合 CPU / 低显存）",
        repo_ids=(
            "Systran/faster-whisper-small",
            "pengzhendong/faster-whisper-small",
        ),
        size_mb=461,
        note="速度快，适合先出草稿或显存较小的情况",
    ),
)

BUILTIN_BY_KEY: Dict[str, ModelSpec] = {spec.key: spec for spec in BUILTIN_MODELS}
DEFAULT_MODEL_KEY = "large-v3-turbo"
DEFAULT_SELECTION = f"builtin:{DEFAULT_MODEL_KEY}"


def get_spec(key: str) -> Optional[ModelSpec]:
    return BUILTIN_BY_KEY.get(key)


def is_model_dir(path: Path) -> bool:
    """目录里是否是可直接加载的 faster-whisper（CT2）模型。"""
    try:
        path = Path(path)
        if not path.is_dir():
            return False
        for name in MODEL_FILES:
            if not (path / name).is_file():
                return False
        return (path / "model.bin").stat().st_size > MIN_MODEL_BYTES
    except OSError:
        return False


def find_model_in(root: Path) -> Optional[Path]:
    """在目录（含子目录）中找第一个完整模型，优先最近修改的。"""
    try:
        root = Path(root)
        if is_model_dir(root):
            return root
        if not root.is_dir():
            return None
        candidates = [
            model_bin.parent for model_bin in root.rglob("model.bin") if is_model_dir(model_bin.parent)
        ]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def builtin_model_dir(key: str) -> Optional[Path]:
    spec = get_spec(key)
    if spec is None:
        return None
    return find_model_in(spec.cache_dir)


def selection_to_path(selection: str) -> Optional[Path]:
    """把 combo 里的 id（builtin:xxx / import:<path>）解析成模型目录。"""
    if not selection:
        return None
    if selection.startswith("builtin:"):
        return builtin_model_dir(selection.split(":", 1)[1])
    if selection.startswith("import:"):
        path = Path(selection.split(":", 1)[1])
        return path if is_model_dir(path) else None
    return None
