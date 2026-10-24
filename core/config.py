"""用户配置（导入的模型、上次选择、窗口偏好）。

配置放在用户数据目录，不随程序目录分发，避免别人拿到你的本地路径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from core.models import DEFAULT_SELECTION, is_model_dir
from core.paths import app_data_dir

CONFIG_PATH = app_data_dir() / "config.json"


def _default() -> Dict[str, Any]:
    return {"imported_models": [], "selected_model": DEFAULT_SELECTION}


def load_config() -> Dict[str, Any]:
    data = _default()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data

    imported: List[str] = []
    for item in raw.get("imported_models") or []:
        if isinstance(item, str) and item.strip():
            imported.append(str(Path(item).expanduser()))
    data["imported_models"] = imported

    selected = raw.get("selected_model")
    if isinstance(selected, str) and selected:
        data["selected_model"] = selected
    return data


def save_config(config: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "imported_models": list(config.get("imported_models") or []),
        "selected_model": str(config.get("selected_model") or DEFAULT_SELECTION),
    }
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_imported_model(config: Dict[str, Any], path: Path) -> bool:
    """记录一个导入的模型目录；已存在则返回 False。"""
    resolved = str(Path(path).resolve())
    imported = list(config.get("imported_models") or [])
    if resolved in imported:
        return False
    imported.append(resolved)
    config["imported_models"] = imported
    return True


def remove_imported_model(config: Dict[str, Any], path: str) -> bool:
    imported = [item for item in (config.get("imported_models") or []) if item != path]
    changed = len(imported) != len(config.get("imported_models") or [])
    config["imported_models"] = imported
    return changed


def imported_model_entries(config: Dict[str, Any]) -> List[Path]:
    """导入列表里仍然有效的模型目录（失效的也返回给界面显示状态）。"""
    return [Path(item) for item in (config.get("imported_models") or [])]


def valid_imported_models(config: Dict[str, Any]) -> List[Path]:
    return [Path(item) for item in (config.get("imported_models") or []) if is_model_dir(Path(item))]
