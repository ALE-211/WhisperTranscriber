"""小工具：体积 / 时间的可读化。"""

from __future__ import annotations

from typing import Optional


def human_size(num_bytes: float) -> str:
    value = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{value:.0f} B"
            if unit == "GB":
                return f"{value:.2f} GB"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} GB"


def human_eta(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0 or seconds > 86400:
        return ""
    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"
