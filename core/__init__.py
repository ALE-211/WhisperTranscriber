"""核心业务逻辑（不依赖任何 UI 框架）。

模块划分：
- env_checker : 检查 Python/CUDA/DLL 环境并静默修复
- downloader  : 从 ModelScope 下载 faster-whisper 模型
- transcriber : FFmpeg 提取音频 + faster-whisper 转录 + 结果导出
- summarizer  : 预留的大模型总结接口
"""

__version__ = "1.0.0"
