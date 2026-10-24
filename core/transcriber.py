"""FFmpeg 音频提取 + faster-whisper 转录 + 结果导出。

约定：
- 本模块不 import 任何 UI 库，可单独在脚本/命令行里使用；
- 进度 = segment.end / 音频总时长，配合 ffprobe 取得总时长；
- 取消通过 should_cancel 回调（返回 True 即停止），不影响已产出的日志。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

Log = Callable[[str], None]
Progress = Callable[[float, Optional[float], str], None]

FORMAT_SUFFIX: Dict[str, str] = {"txt": ".txt", "srt": ".srt", "md": ".md"}
FORMAT_LABELS: Dict[str, str] = {
    "txt": "纯文本 (.txt)",
    "srt": "字幕 (.srt)",
    "md": "Markdown (.md)",
}


class CancelledError(RuntimeError):
    """用户中止了任务。"""


class FFmpegNotFound(RuntimeError):
    """系统里没有可用的 ffmpeg。"""


@dataclass
class TranscriptResult:
    output_path: Path
    segment_count: int
    duration: float
    elapsed: float
    language: str
    language_probability: float
    device: str
    compute_type: str


# --------------------------------------------------------------------------
# 时间与文本格式
# --------------------------------------------------------------------------
def format_timestamp(seconds: float, separator: str = ",") -> str:
    """秒 -> HH:MM:SS,mmm（SRT 用逗号，其余用点）。"""
    total_ms = int(round(max(0.0, float(seconds)) * 1000))
    hours, remainder = divmod(total_ms, 3600 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def format_clock(seconds: float) -> str:
    """秒 -> HH:MM:SS（Markdown 里用，阅读更清爽）。"""
    total = int(max(0, float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def human_duration(seconds: float) -> str:
    if not seconds or seconds <= 0:
        return "未知"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


# --------------------------------------------------------------------------
# FFmpeg
# --------------------------------------------------------------------------
def find_executable(name: str) -> Optional[str]:
    found = shutil.which(name)
    if found:
        return found
    # 兼容：ffmpeg 放在项目根目录或 bin/ 下的情况
    local = Path(__file__).resolve().parent.parent
    for candidate in (local / f"{name}.exe", local / "bin" / f"{name}.exe", local / name):
        if candidate.is_file():
            return str(candidate)
    return None


def ffmpeg_available() -> bool:
    return find_executable("ffmpeg") is not None and find_executable("ffprobe") is not None


def _startup_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _capture_with_flags(command: list, timeout: int, extra: dict) -> tuple:
    with tempfile.TemporaryDirectory(prefix="whisper_ff_") as tmp_dir:
        stdout_path = Path(tmp_dir) / "stdout.txt"
        stderr_path = Path(tmp_dir) / "stderr.txt"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            completed = subprocess.run(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                **extra,
            )
        stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    return completed.returncode, stdout, stderr


def _run_capture(command: list, timeout: int) -> tuple:
    """执行外部命令并用临时文件收集输出。

    不用 subprocess 的管道：部分受限环境/杀毒软件会拒绝 CreatePipe，
    而且管道缓冲区写满还可能把 ffmpeg 卡死。临时文件最稳。
    """
    returncode, stdout, stderr = _capture_with_flags(
        command, timeout, _startup_kwargs()
    )
    if returncode != 0 and not stderr.strip():
        # 极少数受限桌面 / 安全软件环境下，CREATE_NO_WINDOW 会让子进程
        # 以 0xC0000142（DLL 初始化失败）退出，去掉标志再试一次。
        returncode, stdout, stderr = _capture_with_flags(command, timeout, {})
    return returncode, stdout, stderr


def probe_duration(media_path: Path) -> float:
    """用 ffprobe 读取秒数；失败返回 0（界面退化为不确定进度）。"""
    ffprobe = find_executable("ffprobe")
    if not ffprobe:
        return 0.0
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    try:
        returncode, stdout, _stderr = _run_capture(command, timeout=60)
        if returncode != 0:
            return 0.0
        values = [line for line in stdout.strip().splitlines() if line.strip()]
        if not values:
            return 0.0
        return max(0.0, float(values[-1]))
    except Exception:
        return 0.0


def extract_audio(media_path: Path, wav_path: Path, log: Log = print) -> Path:
    """抽取 16kHz 单声道 wav（Whisper 的标准输入）。"""
    ffmpeg = find_executable("ffmpeg")
    if not ffmpeg:
        raise FFmpegNotFound("未找到 ffmpeg，请安装后把它加入 PATH")
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    returncode, _stdout, stderr = _run_capture(command, timeout=3600)
    if returncode != 0 or not wav_path.is_file():
        lines = stderr.strip().splitlines()
        detail = " | ".join(lines[-3:]) if lines else "未知错误"
        raise RuntimeError(f"FFmpeg 提取音频失败：{detail}")
    return wav_path


# --------------------------------------------------------------------------
# 结果写入（流式，长音频也不会把整段文本堆在内存里）
# --------------------------------------------------------------------------
class _BaseWriter:
    def __init__(self, path: Path, meta: Dict[str, str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        self._closed = False
        self._write_header(meta)

    def _write_header(self, meta: Dict[str, str]) -> None:
        return None

    def write(self, index: int, start: float, end: float, text: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        if not self._closed:
            try:
                self._handle.flush()
                self._handle.close()
            finally:
                self._closed = True


class _TxtWriter(_BaseWriter):
    def _write_header(self, meta: Dict[str, str]) -> None:
        return None  # 纯文本保持干净，方便后续喂给大模型

    def write(self, index: int, start: float, end: float, text: str) -> None:
        self._handle.write(text + "\n")


class _SrtWriter(_BaseWriter):
    def write(self, index: int, start: float, end: float, text: str) -> None:
        self._handle.write(f"{index}\n")
        self._handle.write(
            f"{format_timestamp(start)} --> {format_timestamp(end)}\n"
        )
        self._handle.write(text + "\n\n")


class _MdWriter(_BaseWriter):
    def _write_header(self, meta: Dict[str, str]) -> None:
        title = meta.get("title") or "转录结果"
        self._handle.write(f"# {title}\n\n")
        self._handle.write(f"- 源文件：{meta.get('source', '')}\n")
        self._handle.write(f"- 时长：{meta.get('duration', '未知')}\n")
        self._handle.write(f"- 识别语言：{meta.get('language', '未知')}\n")
        self._handle.write(f"- 生成时间：{meta.get('created', '')}\n\n")
        self._handle.write("---\n\n")

    def write(self, index: int, start: float, end: float, text: str) -> None:
        self._handle.write(f"**[{format_clock(start)}]** {text}\n\n")


def create_writer(fmt: str, path: Path, meta: Dict[str, str]) -> _BaseWriter:
    fmt = (fmt or "txt").lower()
    if fmt == "srt":
        return _SrtWriter(path, meta)
    if fmt == "md":
        return _MdWriter(path, meta)
    return _TxtWriter(path, meta)


# --------------------------------------------------------------------------
# 转录器
# --------------------------------------------------------------------------
class Transcriber:
    def __init__(
        self,
        model_dir: Path,
        device: str = "auto",
        compute_type: Optional[str] = None,
        language: str = "zh",
        beam_size: int = 5,
        vad_filter: bool = False,
        log: Optional[Log] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.device = device or "auto"
        self.compute_type = compute_type
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.log = log or (lambda message: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.model = None  # type: ignore[assignment]
        self.active_device = "cpu"
        self.active_compute_type = "int8"

    # ---------- 取消 ----------
    def _check_cancel(self) -> None:
        if self.should_cancel():
            raise CancelledError("任务已取消")

    # ---------- 模型 ----------
    def load_model(self) -> tuple:
        from faster_whisper import WhisperModel

        if self.model is not None:
            return self.active_device, self.active_compute_type

        device = self.device
        compute_type = self.compute_type
        if device == "auto":
            from core.env_checker import detect_device

            device, detected_type = detect_device(self.log)
            compute_type = compute_type or detected_type
        compute_type = compute_type or ("float16" if device == "cuda" else "int8")

        attempts: List[tuple] = [(device, compute_type)]
        if device == "cuda":
            attempts.append(("cpu", "int8"))  # GPU 初始化失败时自动降级

        last_error: Optional[Exception] = None
        for attempt_device, attempt_type in attempts:
            self._check_cancel()
            self.log(f"[模型] 加载模型（{attempt_device} / {attempt_type}）：{self.model_dir}")
            try:
                model = WhisperModel(
                    str(self.model_dir),
                    device=attempt_device,
                    compute_type=attempt_type,
                )
            except Exception as exc:
                last_error = exc
                self.log(f"[模型] {attempt_device} 加载失败：{exc}")
                if attempt_device == "cuda":
                    self.log("[模型] 将回退到 CPU 继续（速度较慢）")
                continue
            self.model = model
            self.active_device = attempt_device
            self.active_compute_type = attempt_type
            self.log("[模型] 模型加载完成")
            return attempt_device, attempt_type

        hint = ""
        if last_error is not None and "cublas" in str(last_error).lower():
            hint = "（提示：缺少 CUDA 库，请运行 pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12）"
        raise RuntimeError(f"模型加载失败：{last_error}{hint}")

    # ---------- 打开转录流 ----------
    def _open_segments(self, audio_path: Path):
        self.load_model()
        kwargs = {
            "language": self.language or None,
            "task": "transcribe",
            "beam_size": self.beam_size,
            "condition_on_previous_text": False,
            "vad_filter": self.vad_filter,
            "multilingual": True,
        }
        try:
            return self.model.transcribe(str(audio_path), **kwargs)
        except TypeError:
            # 兼容不同版本的 faster-whisper：去掉可能存在的新参数再试
            kwargs.pop("multilingual", None)
            kwargs.pop("vad_filter", None)
            return self.model.transcribe(str(audio_path), **kwargs)

    # ---------- 主流程 ----------
    def transcribe(
        self,
        input_path: Path,
        output_path: Path,
        fmt: str = "txt",
        on_progress: Optional[Progress] = None,
    ) -> TranscriptResult:
        input_path = Path(input_path)
        output_path = Path(output_path)
        if not input_path.is_file():
            raise FileNotFoundError(f"找不到输入文件：{input_path}")

        started = time.time()
        cancelled = False
        writer: Optional[_BaseWriter] = None
        temp_dir = Path(tempfile.mkdtemp(prefix="whisper_tx_"))

        try:
            self._check_cancel()
            duration = probe_duration(input_path)
            self.log(f"[音频] 总时长：{human_duration(duration)}")
            if duration <= 0:
                self.log("[音频] 无法读取时长，进度条将显示为不确定状态")

            audio_path = input_path
            if ffmpeg_available():
                self.log("[音频] 正在用 FFmpeg 提取 16kHz 单声道音频 ...")
                wav_path = temp_dir / "audio.wav"
                extract_audio(input_path, wav_path)
                audio_path = wav_path
                self.log("[音频] 音频准备完成")
            else:
                self.log("[音频] 未找到 FFmpeg，直接交给 faster-whisper 解码（部分格式可能失败）")

            self._check_cancel()
            device, compute_type = self.load_model()
            self.log("[转录] 开始识别 ...")
            transcribe_started = time.time()

            segments, info = self._open_segments(audio_path)
            language = str(getattr(info, "language", "") or self.language or "")
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
            if language:
                self.log(f"[转录] 识别语言：{language}（置信度 {probability:.2f}）")

            meta = {
                "title": input_path.stem,
                "source": str(input_path),
                "duration": human_duration(duration),
                "language": language or "自动",
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            writer = create_writer(fmt, output_path, meta)

            total = duration
            count = 0
            for segment in segments:  # 生成器：边识别边写文件
                self._check_cancel()
                text = (segment.text or "").strip()
                start, end = float(segment.start), float(segment.end)
                if text:
                    count += 1
                    writer.write(count, start, end, text)

                elapsed = time.time() - transcribe_started
                if total > 0:
                    percent = min(100.0, max(0.0, end / total * 100.0))
                    eta = None
                    if percent >= 1.0 and elapsed > 0:
                        eta = max(0.0, elapsed / (percent / 100.0) - elapsed)
                else:
                    percent = -1.0
                    eta = None
                if on_progress is not None:
                    on_progress(percent, eta, text[:40])

            writer.close()
            writer = None

            if count == 0:
                self.log("[转录] 警告：没有识别到任何语音内容，输出文件为空")

            elapsed_total = time.time() - started
            self.log(
                f"[转录] 完成：{count} 段，耗时 {human_duration(elapsed_total)} -> {output_path}"
            )
            return TranscriptResult(
                output_path=output_path,
                segment_count=count,
                duration=duration,
                elapsed=elapsed_total,
                language=language,
                language_probability=probability,
                device=device,
                compute_type=compute_type,
            )
        except CancelledError:
            cancelled = True
            raise
        finally:
            if writer is not None:
                writer.close()
            if cancelled:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)
