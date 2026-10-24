"""PyQt5 界面层：只负责交互与展示，业务逻辑全部在 core/ 里。

线程模型：
- 环境检查 / 模型下载 / 转录 各自跑在 QThread 里；
- 子线程只通过 pyqtSignal 回传日志、进度和结果，绝不在子线程里碰控件。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, QSettings, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import add_imported_model, load_config, remove_imported_model, save_config
from core.downloader import Downloader
from core.models import (
    BUILTIN_MODELS,
    DEFAULT_SELECTION,
    ModelSpec,
    find_model_in,
    get_spec,
    is_model_dir,
    selection_to_path,
)
from core.paths import default_models_dir, resource_path
from core.transcriber import FORMAT_LABELS, FORMAT_SUFFIX, CancelledError, Transcriber
from core.utils import human_eta

AUDIO_FILTER = (
    "音视频文件 (*.mp4 *.mkv *.mov *.avi *.flv *.wmv *.webm *.ts *.m4v "
    "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.wma);;所有文件 (*.*)"
)


def _open_path(path: Path) -> None:
    target = Path(path)
    if not target.exists():
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


# --------------------------------------------------------------------------
# 后台线程
# --------------------------------------------------------------------------
class EnvWorker(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(object)

    def run(self) -> None:
        try:
            from core.env_checker import ensure_runtime

            report = ensure_runtime(log=self.log.emit, auto_install=True)
        except Exception as exc:  # 环境检查失败不应阻止界面启动
            report = {
                "cuda_ok": False,
                "device": "cpu",
                "compute_type": "int8",
                "notes": [f"环境检查异常：{exc}"],
            }
        self.done.emit(report)


class DownloadWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, spec: ModelSpec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec

    def run(self) -> None:
        try:
            downloader = Downloader(
                self.spec,
                log=self.log.emit,
                on_progress=lambda percent, detail: self.progress.emit(percent, detail),
            )
            self.succeeded.emit(downloader.download())
        except Exception as exc:
            self.failed.emit(str(exc))


class TranscribeWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        fmt: str,
        model_dir: Path,
        language: str,
        vad_filter: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._input = Path(input_path)
        self._output = Path(output_path)
        self._fmt = fmt
        self._model_dir = Path(model_dir)
        self._language = language
        self._vad = vad_filter
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            transcriber = Transcriber(
                self._model_dir,
                language=self._language,
                vad_filter=self._vad,
                log=self.log.emit,
                should_cancel=self._cancel_event.is_set,
            )
            result = transcriber.transcribe(
                self._input, self._output, fmt=self._fmt, on_progress=self._emit_progress
            )
            self.succeeded.emit(str(result.output_path))
        except CancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))

    def _emit_progress(self, percent: float, eta: Optional[float], preview: str) -> None:
        detail = ""
        eta_text = human_eta(eta)
        if eta_text:
            detail = f"预计剩余 {eta_text}"
        if preview:
            detail = f"{detail} · {preview}" if detail else preview
        value = -1 if percent is None or percent < 0 else int(percent)
        self.progress.emit(value, detail)


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("WhisperTranscriber", "WhisperTranscriber")
        self.config = load_config()
        self.last_output: Optional[Path] = None
        self.env_worker: Optional[EnvWorker] = None
        self.download_worker: Optional[DownloadWorker] = None
        self.transcribe_worker: Optional[TranscribeWorker] = None
        self._env_ready = False
        self._auto_download_started = False

        self._build_ui()
        self._apply_window_icon()
        self._restore_settings()
        self.refresh_model_combo(self.config.get("selected_model"))
        QTimer.singleShot(300, self._start_env_check)

    # ------------------------------ UI ------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("Whisper 转录器 · faster-whisper")
        self.resize(1080, 820)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(10)

        layout.addWidget(self._build_model_box())
        layout.addWidget(self._build_task_box())
        layout.addLayout(self._build_action_row())
        layout.addLayout(self._build_progress_row())
        layout.addWidget(self._build_log_box(), 1)

        self.setStyleSheet(
            """
            QGroupBox {
                font-weight: 600;
                border: 1px solid #d7dce5;
                border-radius: 8px;
                margin-top: 10px;
                padding: 12px 10px 8px 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: #334155;
            }
            QPushButton { padding: 6px 12px; }
            QPushButton#primary {
                background: #2563eb; color: #ffffff; border: none;
                border-radius: 6px; padding: 9px 26px; font-weight: 600;
            }
            QPushButton#primary:hover { background: #1d4ed8; }
            QPushButton#primary:disabled { background: #a7bdec; }
            QPlainTextEdit {
                background: #0f172a; color: #dbe4f0;
                border: none; border-radius: 6px;
            }
            """
        )
        self.statusBar().showMessage("就绪")

    def _apply_window_icon(self) -> None:
        icon_file = resource_path("assets/logo256.png")
        if icon_file.is_file():
            self.setWindowIcon(QIcon(str(icon_file)))

    def _build_model_box(self) -> QGroupBox:
        box = QGroupBox("模型")
        layout = QVBoxLayout(box)

        first = QHBoxLayout()
        first.addWidget(QLabel("使用模型"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(460)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        first.addWidget(self.model_combo, 1)
        self.model_status = QLabel("")
        first.addWidget(self.model_status)
        self.device_label = QLabel("设备：检测中 ...")
        self.device_label.setStyleSheet("color: #64748b;")
        first.addWidget(self.device_label)
        layout.addLayout(first)

        second = QHBoxLayout()
        self.download_btn = QPushButton("下载所选模型")
        self.import_btn = QPushButton("导入本地模型...")
        self.remove_btn = QPushButton("移除导入")
        self.open_model_btn = QPushButton("打开模型目录")
        self.download_btn.clicked.connect(self.on_download)
        self.import_btn.clicked.connect(self.on_import)
        self.remove_btn.clicked.connect(self.on_remove_import)
        self.open_model_btn.clicked.connect(self.on_open_model_dir)
        second.addWidget(self.download_btn)
        second.addWidget(self.import_btn)
        second.addWidget(self.remove_btn)
        second.addStretch(1)
        second.addWidget(self.open_model_btn)
        layout.addLayout(second)
        return box

    def _build_task_box(self) -> QGroupBox:
        box = QGroupBox("转录任务")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(8)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择要转录的视频或音频文件 ...")
        browse_input = QPushButton("浏览...")
        browse_input.clicked.connect(self.on_browse_input)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(browse_input)
        form.addRow("输入文件", input_row)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("默认与输入文件同目录")
        browse_output = QPushButton("浏览...")
        browse_output.clicked.connect(self.on_browse_output)
        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_output)
        form.addRow("输出目录", output_row)

        self.format_combo = QComboBox()
        for key, label in FORMAT_LABELS.items():
            self.format_combo.addItem(label, key)
        form.addRow("输出格式", self.format_combo)

        self.language_combo = QComboBox()
        self.language_combo.addItem("中文（推荐，快且稳）", "zh")
        self.language_combo.addItem("自动检测", "")
        self.language_combo.addItem("英文", "en")
        form.addRow("识别语言", self.language_combo)

        self.vad_check = QCheckBox("过滤静音片段（VAD，可减少空白处的幻觉文本）")
        form.addRow("", self.vad_check)
        return box

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.start_btn = QPushButton("开始转录")
        self.start_btn.setObjectName("primary")
        self.start_btn.setMinimumWidth(130)
        self.start_btn.clicked.connect(self.on_start)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.on_stop)

        self.open_output_btn = QPushButton("打开输出目录")
        self.open_output_btn.setEnabled(False)
        self.open_output_btn.clicked.connect(self.on_open_output_dir)

        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        row.addWidget(self.open_output_btn)
        return row

    def _build_progress_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setMinimumHeight(22)
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color: #475569;")
        self.detail_label.setMinimumWidth(280)
        row.addWidget(self.progress, 1)
        row.addWidget(self.detail_label)
        return row

    def _build_log_box(self) -> QGroupBox:
        box = QGroupBox("运行日志")
        layout = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setPlaceholderText("日志会显示在这里 ...")
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.log_view.clear)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(clear_btn)
        layout.addWidget(self.log_view, 1)
        layout.addLayout(footer)
        return box

    # ------------------------------ 设置 ------------------------------
    def _restore_settings(self) -> None:
        self.input_edit.setText(self.settings.value("last_input", "", type=str) or "")
        self.output_edit.setText(self.settings.value("last_output_dir", "", type=str) or "")
        fmt = self.settings.value("format", "txt", type=str)
        index = self.format_combo.findData(fmt)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
        language = self.settings.value("language", "zh", type=str)
        index = self.language_combo.findData(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)

    def _save_settings(self) -> None:
        self.settings.setValue("last_input", self.input_edit.text().strip())
        self.settings.setValue("last_output_dir", self.output_edit.text().strip())
        self.settings.setValue("format", self.format_combo.currentData())
        self.settings.setValue("language", self.language_combo.currentData())

    # ------------------------------ 日志 ------------------------------
    def append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {message}")
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    # ------------------------------ 模型 ------------------------------
    def selected_model_id(self) -> str:
        return self.model_combo.currentData() or DEFAULT_SELECTION

    def selected_model_path(self) -> Optional[Path]:
        return selection_to_path(self.selected_model_id())

    def refresh_model_combo(self, preferred: Optional[str] = None) -> None:
        target = (
            preferred
            or self.model_combo.currentData()
            or self.config.get("selected_model")
            or DEFAULT_SELECTION
        )
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for spec in BUILTIN_MODELS:
            found = find_model_in(spec.cache_dir)
            status = "已就绪" if found else f"需下载 {spec.size_text}"
            self.model_combo.addItem(f"{spec.label} · {status}", f"builtin:{spec.key}")
        for item in self.config.get("imported_models") or []:
            path = Path(item)
            label = path.name or str(path)
            status = "" if is_model_dir(path) else "（路径失效）"
            self.model_combo.addItem(f"本地模型：{label}{status}", f"import:{item}")
        index = self.model_combo.findData(target)
        if index < 0:
            index = self.model_combo.findData(DEFAULT_SELECTION)
        if index < 0:
            index = 0
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._on_model_changed()

    def _on_model_changed(self, _index: int = 0) -> None:
        model_id = self.selected_model_id()
        self.config["selected_model"] = model_id
        try:
            save_config(self.config)
        except OSError:
            pass
        self._update_model_status()

    def _update_model_status(self) -> None:
        model_id = self.selected_model_id()
        if model_id.startswith("builtin:"):
            key = model_id.split(":", 1)[1]
            spec = get_spec(key)
            if spec is None:
                self.model_status.setText("模型：未知")
                return
            found = find_model_in(spec.cache_dir)
            if found is not None:
                self.model_status.setText("模型：已就绪")
                self.model_status.setStyleSheet("color: #15803d; font-weight: 600;")
                self.model_status.setToolTip(f"{found}\n{spec.note}")
                self.download_btn.setText("重新检查模型")
            else:
                self.model_status.setText(f"模型：未下载 · 约 {spec.size_text}")
                self.model_status.setStyleSheet("color: #b45309; font-weight: 600;")
                self.model_status.setToolTip(f"{spec.cache_dir}\n{spec.note}")
                self.download_btn.setText("下载所选模型")
            self.download_btn.setEnabled(True)
            self.remove_btn.setEnabled(False)
        else:
            path = Path(model_id.split(":", 1)[1])
            self.download_btn.setEnabled(False)
            self.remove_btn.setEnabled(True)
            if is_model_dir(path):
                self.model_status.setText("模型：本地导入")
                self.model_status.setStyleSheet("color: #15803d; font-weight: 600;")
                self.model_status.setToolTip(str(path))
            else:
                self.model_status.setText("模型：路径失效")
                self.model_status.setStyleSheet("color: #b91c1c; font-weight: 600;")
                self.model_status.setToolTip(f"{path}\n目录已不存在或不完整")

    def on_download(self) -> None:
        if self.download_worker is not None and self.download_worker.isRunning():
            return
        model_id = self.selected_model_id()
        if not model_id.startswith("builtin:"):
            QMessageBox.information(
                self, "无需下载", "当前选择的是本地导入的模型，不需要下载。"
            )
            return
        spec = get_spec(model_id.split(":", 1)[1])
        if spec is None:
            return
        existing = find_model_in(spec.cache_dir)
        if existing is not None:
            self.append_log(f"[模型] 已就绪：{existing}")
            self.refresh_model_combo(model_id)
            return

        self.append_log(f"[模型] 目标目录：{spec.cache_dir}")
        self.progress.setRange(0, 0)
        self.detail_label.setText(f"准备下载 {spec.label} ...")
        self.download_btn.setEnabled(False)
        self.start_btn.setEnabled(False)

        self.download_worker = DownloadWorker(spec, self)
        self.download_worker.log.connect(self.append_log)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.succeeded.connect(self._on_download_succeeded)
        self.download_worker.failed.connect(self._on_download_failed)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.start()

    def _on_download_progress(self, percent: int, detail: str) -> None:
        if percent < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        self.detail_label.setText(detail)

    def _on_download_succeeded(self, path: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.detail_label.setText("模型已就绪")
        self.refresh_model_combo(self.selected_model_id())
        self.append_log(f"[模型] 就绪：{path}")
        QMessageBox.information(self, "模型就绪", "模型下载完成，可以开始转录了。")

    def _on_download_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.detail_label.setText("下载失败")
        self.append_log(f"[模型] 下载失败：{message}")
        QMessageBox.warning(
            self,
            "模型下载失败",
            f"{message}\n\n请检查网络后重试（本项目只使用 ModelScope 源）。",
        )

    def _on_download_finished(self) -> None:
        self.download_btn.setEnabled(True)
        self._update_model_status()
        if self.transcribe_worker is None or not self.transcribe_worker.isRunning():
            self.start_btn.setEnabled(True)

    def on_import(self) -> None:
        start_dir = self.config.get("selected_model") or ""
        if start_dir.startswith("import:"):
            start_dir = start_dir.split(":", 1)[1]
        else:
            start_dir = str(default_models_dir())
        directory = QFileDialog.getExistingDirectory(
            self, "选择模型目录（需包含 model.bin / config.json / tokenizer.json）", start_dir
        )
        if not directory:
            return
        chosen = Path(directory)
        resolved = chosen if is_model_dir(chosen) else find_model_in(chosen)
        if resolved is None:
            QMessageBox.warning(
                self,
                "不是有效模型",
                "该目录不是完整的 faster-whisper（CTranslate2）模型。\n\n"
                "目录中必须包含：model.bin、config.json、tokenizer.json。",
            )
            return
        if add_imported_model(self.config, resolved):
            save_config(self.config)
            self.append_log(f"[模型] 已导入：{resolved}")
        else:
            self.append_log(f"[模型] 该模型已在列表中：{resolved}")
        self.refresh_model_combo(f"import:{resolved}")
        QMessageBox.information(self, "导入成功", f"已添加本地模型：\n{resolved}")

    def on_remove_import(self) -> None:
        model_id = self.selected_model_id()
        if not model_id.startswith("import:"):
            return
        path = model_id.split(":", 1)[1]
        reply = QMessageBox.question(
            self,
            "移除导入",
            "从列表中移除这个模型吗？（只移除记录，不会删除磁盘上的文件）",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        remove_imported_model(self.config, path)
        save_config(self.config)
        self.append_log(f"[模型] 已移除：{path}")
        self.refresh_model_combo(DEFAULT_SELECTION)

    def on_open_model_dir(self) -> None:
        model_id = self.selected_model_id()
        if model_id.startswith("import:"):
            target = Path(model_id.split(":", 1)[1])
        else:
            spec = get_spec(model_id.split(":", 1)[1])
            target = spec.cache_dir if spec else default_models_dir()
        Path(target).mkdir(parents=True, exist_ok=True)
        _open_path(target)

    # ------------------------------ 环境 ------------------------------
    def _start_env_check(self) -> None:
        if self.env_worker is not None and self.env_worker.isRunning():
            return
        self.env_worker = EnvWorker(self)
        self.env_worker.log.connect(self.append_log)
        self.env_worker.done.connect(self._on_env_ready)
        self.env_worker.start()

    def _on_env_ready(self, report: dict) -> None:
        self._env_ready = True
        cuda_ok = bool(report.get("cuda_ok"))
        compute_type = str(report.get("compute_type", "int8"))
        if cuda_ok:
            self.device_label.setText(f"设备：GPU · cuda / {compute_type}")
        else:
            self.device_label.setText("设备：CPU（速度较慢）")
        self.device_label.setToolTip("\n".join(str(n) for n in report.get("notes") or []))
        self.statusBar().showMessage("环境就绪，可以开始转录")

        if self._auto_download_started:
            return
        model_id = self.selected_model_id()
        if model_id.startswith("builtin:") and self.selected_model_path() is None:
            self._auto_download_started = True
            spec = get_spec(model_id.split(":", 1)[1])
            size_text = spec.size_text if spec else ""
            self.append_log(
                f"[模型] 当前模型未下载（约 {size_text}），"
                "请点“下载所选模型”，或点“导入本地模型...”使用已有模型。"
            )
            if os.environ.get("WHISPER_AUTO_DOWNLOAD", "0") == "1":
                self.append_log("[模型] 检测到 WHISPER_AUTO_DOWNLOAD=1，自动开始下载 ...")
                self.on_download()

    # ------------------------------ 选择文件 ------------------------------
    def on_browse_input(self) -> None:
        start_dir = self.input_edit.text().strip()
        if not start_dir or not Path(start_dir).is_dir():
            start_dir = self.settings.value("last_input_dir", "", type=str) or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "选择音视频文件", start_dir, AUDIO_FILTER)
        if not path:
            return
        self.input_edit.setText(path)
        self.settings.setValue("last_input_dir", str(Path(path).parent))
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(Path(path).parent))
        self.statusBar().showMessage(f"已选择：{path}")

    def on_browse_output(self) -> None:
        start_dir = self.output_edit.text().strip()
        if not start_dir or not Path(start_dir).is_dir():
            start_dir = self.settings.value("last_input_dir", "", type=str) or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", start_dir)
        if directory:
            self.output_edit.setText(directory)

    # ------------------------------ 转录 ------------------------------
    def on_start(self) -> None:
        if self.transcribe_worker is not None and self.transcribe_worker.isRunning():
            return
        if not self._env_ready:
            QMessageBox.information(self, "请稍候", "正在检查运行环境，请稍后再试。")
            return

        input_text = self.input_edit.text().strip()
        if not input_text:
            QMessageBox.warning(self, "缺少输入", "请先选择要转录的文件。")
            return
        input_path = Path(input_text)
        if not input_path.is_file():
            QMessageBox.warning(self, "文件不存在", f"找不到文件：\n{input_path}")
            return

        model_path = self.selected_model_path()
        if model_path is None:
            if self.selected_model_id().startswith("builtin:"):
                reply = QMessageBox.question(
                    self,
                    "模型未就绪",
                    "所选模型还没有下载，是否现在下载？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    self.on_download()
            else:
                QMessageBox.warning(
                    self,
                    "模型不可用",
                    "导入的模型目录已失效，请重新导入一个可用的模型目录。",
                )
            return

        output_dir_text = self.output_edit.text().strip()
        output_dir = Path(output_dir_text) if output_dir_text else input_path.parent
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "输出目录不可用", f"{output_dir}\n{exc}")
            return

        fmt = self.format_combo.currentData() or "txt"
        suffix = FORMAT_SUFFIX.get(fmt, ".txt")
        output_path = output_dir / f"{input_path.stem}{suffix}"
        if output_path.exists():
            reply = QMessageBox.question(
                self,
                "文件已存在",
                f"{output_path.name} 已存在，是否覆盖？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.last_output = output_path
        self.open_output_btn.setEnabled(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.detail_label.setText("准备中 ...")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.download_btn.setEnabled(False)
        self.statusBar().showMessage("转录中 ...")
        self.append_log(f"===== 开始任务：{input_path.name} =====")
        self.append_log(f"[模型] 使用：{model_path}")

        self.transcribe_worker = TranscribeWorker(
            input_path,
            output_path,
            fmt,
            model_path,
            self.language_combo.currentData() or "",
            self.vad_check.isChecked(),
            self,
        )
        self.transcribe_worker.log.connect(self.append_log)
        self.transcribe_worker.progress.connect(self._on_task_progress)
        self.transcribe_worker.succeeded.connect(self._on_task_succeeded)
        self.transcribe_worker.failed.connect(self._on_task_failed)
        self.transcribe_worker.cancelled.connect(self._on_task_cancelled)
        self.transcribe_worker.finished.connect(self._on_task_finished)
        self.transcribe_worker.start()

    def _on_task_progress(self, percent: int, detail: str) -> None:
        if percent < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
        if detail:
            self.detail_label.setText(detail)

    def _on_task_succeeded(self, path: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.detail_label.setText("完成")
        self.statusBar().showMessage(f"转录完成：{path}")
        QMessageBox.information(
            self,
            "转录完成",
            f"结果已保存到：\n{path}\n\n可点击“打开输出目录”查看文件。",
        )

    def _on_task_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.detail_label.setText("失败")
        self.append_log(f"[错误] {message}")
        self.statusBar().showMessage("转录失败")
        QMessageBox.critical(self, "转录失败", message)

    def _on_task_cancelled(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.detail_label.setText("已取消")
        self.append_log("[任务] 已取消，未保留输出文件")
        self.statusBar().showMessage("已取消")

    def _on_task_finished(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._update_model_status()

    def on_stop(self) -> None:
        if self.transcribe_worker is not None and self.transcribe_worker.isRunning():
            self.stop_btn.setEnabled(False)
            self.append_log("[任务] 正在停止 ...")
            self.transcribe_worker.cancel()

    def on_open_output_dir(self) -> None:
        if self.last_output is not None:
            _open_path(self.last_output.parent)

    # ------------------------------ 关闭 ------------------------------
    def closeEvent(self, event) -> None:
        if self.transcribe_worker is not None and self.transcribe_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "任务进行中",
                "转录仍在进行，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.transcribe_worker.cancel()
            self.transcribe_worker.wait(3000)

        if self.download_worker is not None and self.download_worker.isRunning():
            reply = QMessageBox.question(
                self,
                "正在下载模型",
                "模型仍在下载，退出会中断下载，确定退出吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.download_worker.wait(1500)
            if self.download_worker.isRunning():
                self.download_worker.terminate()
                self.download_worker.wait(1000)

        self._save_settings()
        event.accept()


def launch(argv=None) -> int:
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("WhisperTranscriber")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    return app.exec_()
