"""从 ModelScope 下载 faster-whisper（CT2）模型。

每个模型有独立缓存目录（models/<key>），多个模型可以共存。
只使用 ModelScope：HuggingFace / hf-mirror 在本项目里被验证为不稳定（401/404/残缺文件）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from core.models import MODEL_REVISION, ModelSpec, find_model_in, is_model_dir
from core.utils import human_eta, human_size

Log = Callable[[str], None]
Progress = Callable[[int, str], None]


class DownloadError(RuntimeError):
    """模型下载失败。"""


class Downloader:
    def __init__(
        self,
        spec: ModelSpec,
        log: Optional[Log] = None,
        on_progress: Optional[Progress] = None,
    ) -> None:
        self.spec = spec
        self.cache_dir = spec.cache_dir
        self.log = log or (lambda message: None)
        self.on_progress = on_progress

    # ---------------- 对外入口 ----------------
    def download(self) -> str:
        existing = find_model_in(self.cache_dir)
        if existing is not None:
            self.log(f"[模型] 已存在可用模型：{existing}")
            self._emit_progress(100, "模型已就绪")
            return str(existing)

        errors = []
        for repo in self.spec.repo_ids:
            self.log(f"[模型] 正在从 {repo} 下载（约 {self.spec.size_text}）")
            try:
                path = self._download_repo(repo)
            except Exception as exc:  # 换下一个镜像源
                errors.append(f"{repo}: {exc}")
                self.log(f"[模型] {repo} 下载失败：{exc}")
                continue
            self._emit_progress(100, "下载完成")
            self.log(f"[模型] 下载完成：{path}")
            return str(path)

        raise DownloadError("模型下载失败：\n" + "\n".join(errors))

    # ---------------- 单个仓库 ----------------
    def _download_repo(self, repo: str) -> Path:
        from modelscope import snapshot_download

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        total = self._remote_total(repo)
        if total > 0:
            self.log(f"[模型] 远端大小约 {human_size(total)}")

        snapshot_dir = self._snapshot_dir(repo)
        stop_event = threading.Event()
        poller = threading.Thread(
            target=self._poll_progress,
            args=(stop_event, total, snapshot_dir),
            daemon=True,
        )
        poller.start()
        try:
            model_path = snapshot_download(
                repo, revision=MODEL_REVISION, cache_dir=str(self.cache_dir)
            )
        finally:
            stop_event.set()
            poller.join(timeout=2)

        resolved = Path(model_path).resolve()
        if not is_model_dir(resolved):
            raise DownloadError(f"下载结果不完整：{resolved}（请删除该目录后重试）")
        return resolved

    def _snapshot_dir(self, repo: str) -> Path:
        owner, name = repo.split("/", 1)
        return (
            self.cache_dir
            / "models"
            / f"{owner}--{name}"
            / "snapshots"
            / MODEL_REVISION
        )

    def _remote_total(self, repo: str) -> int:
        try:
            from modelscope.hub.api import HubApi

            files = HubApi().get_model_files(repo, revision=MODEL_REVISION)
            return sum(int(item.get("Size") or 0) for item in files)
        except Exception:
            return 0

    # ---------------- 进度 ----------------
    def _poll_progress(self, stop_event: threading.Event, total: int, snapshot_dir: Path) -> None:
        last_size = 0
        last_time = time.time()
        while not stop_event.wait(0.5):
            root = snapshot_dir if snapshot_dir.exists() else self.cache_dir
            size = self._dir_size(root)
            now = time.time()
            elapsed = now - last_time
            speed = 0.0
            if elapsed > 0 and size >= last_size:
                speed = (size - last_size) / elapsed
            last_size, last_time = size, now

            if total > 0:
                percent = min(99, int(size * 100 / total))
                detail = f"{human_size(size)} / {human_size(total)}"
                if speed > 1024 * 1024:
                    detail += f"  {speed / 1024 / 1024:.1f} MB/s"
                    eta = human_eta((total - size) / speed)
                    if eta:
                        detail += f"  剩余 {eta}"
            else:
                percent = -1
                detail = f"已下载 {human_size(size)}"
            self._emit_progress(percent, detail)

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        if not path.exists():
            return 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    def _emit_progress(self, percent: int, detail: str) -> None:
        if self.on_progress is not None:
            try:
                self.on_progress(percent, detail)
            except Exception:
                pass
