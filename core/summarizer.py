"""预留：转录结果的后处理 / 大模型总结。

当前只是接口壳子，不产生任何网络请求。后续可以接 DeepSeek API、OpenAI
兼容接口或本地 Ollama：读取转写后的 .md/.txt，生成会议纪要、摘要、待办事项。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_PROMPT = (
    "下面是一段音视频的转录文本。请忽略口语赘词，输出：\n"
    "1. 三句话以内的整体摘要；\n"
    "2. 分点列出关键信息；\n"
    "3. 如果出现待办事项，单独列出。"
)


@dataclass
class SummaryOptions:
    provider: str = "deepseek"  # deepseek / openai / ollama
    model: str = "deepseek-chat"
    temperature: float = 0.3
    max_chars: int = 60000


def read_transcript(path: Path, max_chars: int = 60000) -> str:
    """读取转录文本，过长的部分截断（后续可改为分块总结）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return text[:max_chars]


def summarize(
    transcript_path: Path,
    options: Optional[SummaryOptions] = None,
    prompt: str = DEFAULT_PROMPT,
) -> str:
    """生成摘要。当前版本尚未接入任何模型服务。"""
    options = options or SummaryOptions()
    raise NotImplementedError(
        f"总结功能尚未实现（provider={options.provider}, model={options.model}）。"
        "可在 core/summarizer.py 中接入 DeepSeek API 或本地 Ollama 后启用。"
    )
