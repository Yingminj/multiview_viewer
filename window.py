"""PySide6 UI. Decoder workers never access Qt widgets."""
from __future__ import annotations

import json
import time
from pathlib import Path
from queue import Empty

from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QMessageBox, QSplitter, QScrollArea,
    QCheckBox, QDoubleSpinBox, QComboBox, QSlider, QStyle, QSizePolicy, QApplication,
)

from video import View, Worker, discover
from alignment import (AUTO, MODES, apply_alignment, calculate_alignment,
                       configured_mode, episode_versions)


def clock_text(seconds):
    minutes, seconds = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{seconds:06.3f}"


class VideoCanvas(QWidget):
    double_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.message = "加载中…"
        self.setMinimumSize(100, 70)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def sizeHint(self):
        return QSize(400, 225)

    def set_image(self, image):
        data = image.tobytes()
        self.image = QImage(data, image.width, image.height, image.width * 3,
                            QImage.Format.Format_RGB888).copy()
        self.message = ""
        self.update()

    def set_error(self, text):
        self.image = QImage()
        self.message = text
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b1220"))
        if self.image.isNull():
            painter.setPen(QColor("#cbd5e1"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
        else:
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = self.rect()
            target.setSize(size)
            target.moveCenter(self.rect().center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.image)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()


class SeekSlider(QSlider):
    scrub_started = Signal()
    scrub_finished = Signal()

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.dragging = False

    def set_from_mouse(self, event):
        handle = self.style().pixelMetric(QStyle.PixelMetric.PM_SliderLength)
        span = max(1, self.width() - handle)
        pos = max(0, min(span, round(event.position().x() - handle / 2)))
        self.setValue(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), pos, span))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.scrub_started.emit()
            self.set_from_mouse(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.set_from_mouse(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.set_from_mouse(event)
            self.dragging = False
            self.scrub_finished.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, config, directory=None):
        super().__init__()
        self.config = Path(config)
        self.manual_offsets = {}
        try:
            initial_alignment = configured_mode(self.config)
        except (OSError, ValueError):
            initial_alignment = AUTO
        self.views, self.rows, self.workers, self.panels, self.metadata = [], {}, {}, {}, {}
        self.all_workers = set()
        self.closing = False
        self.generation, self.batch, self.received = 0, set(), {}
        self.position, self.duration = 0.0, 0.0
        self.playing, self.step_pending, self.zoomed = False, False, None
        self.updating_slider = False
        self.setWindowTitle("multiviewer · 多视角视频查看器")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.setStyleSheet("""
            QMainWindow, QWidget#main { background: #eef2f6; }
            QPushButton { padding: 7px 12px; }
            QLineEdit { padding: 6px; }
            QWidget#stage { background: #101827; }
            QWidget#panel { background: #1e293b; border-radius: 5px; }
            QLabel#title { color: #f8fafc; padding: 4px; font-weight: bold; }
            QLabel#info { color: #bac8da; padding: 4px; }
        """)
        central = QWidget()
        central.setObjectName("main")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        top = QHBoxLayout()
        top.addWidget(QLabel("数据目录"))
        self.path = QLineEdit(str(directory or ""))
        self.path.setPlaceholderText("选择 episode 文件夹，自动加载三视角")
        self.path.returnPressed.connect(self.load)
        top.addWidget(self.path, 1)
        self.button(top, "选择目录", self.choose)
        self.button(top, "加载", self.load)
        outer.addLayout(top)
        self.version_label = QLabel("数据版本：未加载")
        self.version_label.setWordWrap(True)
        self.version_label.setTextFormat(Qt.TextFormat.PlainText)
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.version_label)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)
        sidebar = QWidget()
        sidebar.setMinimumWidth(240)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.addWidget(QLabel("时间对齐"))
        self.alignment_mode = QComboBox()
        for text, mode in [("自动对齐（曝光结束）", AUTO),
                           ("首帧对齐", "first_frame"), ("手动偏移", "manual")]:
            self.alignment_mode.addItem(text, mode)
        self.alignment_mode.setCurrentIndex(MODES.index(initial_alignment))
        self.alignment_mode.currentIndexChanged.connect(self.change_alignment)
        sidebar_layout.addWidget(self.alignment_mode)
        self.alignment_label = QLabel("加载后显示对齐状态")
        self.alignment_label.setWordWrap(True)
        self.alignment_label.setTextFormat(Qt.TextFormat.PlainText)
        sidebar_layout.addWidget(self.alignment_label)
        sidebar_layout.addWidget(QLabel("视角选择 / 时间偏移（毫秒）"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.list_widget)
        sidebar_layout.addWidget(scroll, 1)
        self.button(sidebar_layout, "添加视频…", self.add_video)
        self.button(sidebar_layout, "保存视角配置…", self.save_config)
        sidebar_layout.addWidget(QLabel("正偏移：该视角晚开始\n双击画面放大，Esc 恢复\n空格播放 / 暂停，← → 逐帧"))
        splitter.addWidget(sidebar)
        self.stage = QWidget()
        self.stage.setObjectName("stage")
        self.grid = QGridLayout(self.stage)
        self.grid.setContentsMargins(4, 4, 4, 4)
        splitter.addWidget(self.stage)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1120])
        controls = QHBoxLayout()
        self.play_button = self.button(controls, "播放", self.toggle_play)
        self.button(controls, "上一帧", lambda: self.step(-1))
        self.button(controls, "下一帧", lambda: self.step(1))
        self.button(controls, "回到开头", lambda: self.seek(0))
        self.speed = QComboBox()
        self.speed.addItems(["0.25", "0.5", "1.0", "1.5", "2.0"])
        self.speed.setCurrentText("1.0")
        self.speed.currentTextChanged.connect(self.reanchor)
        controls.addWidget(self.speed)
        controls.addWidget(QLabel("倍速"))
        controls.addStretch()
        self.time_label = QLabel()
        controls.addWidget(self.time_label)
        outer.addLayout(controls)
        self.slider = SeekSlider()
        self.slider.setRange(0, 1)
        self.slider.scrub_started.connect(self.drag_start)
        self.slider.scrub_finished.connect(self.drag_end)
        self.slider.valueChanged.connect(self.slider_changed)
        outer.addWidget(self.slider)
        self.statusBar().showMessage("各版本均尝试按曝光结束时间对齐；无法对齐时按首帧播放。")
        self.shortcuts = []
        for key, callback in [("Space", self.toggle_play), ("Left", lambda: self.step(-1)),
                              ("Right", lambda: self.step(1)), ("Escape", lambda: self.zoom(None))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append((key, shortcut))
        QApplication.instance().focusChanged.connect(self.focus_changed)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.resized)
        splitter.splitterMoved.connect(lambda *args: self.resize_timer.start(180))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(15)
        self.layout_panels()
        self.update_clock()
        if directory:
            QTimer.singleShot(0, self.load)

    @staticmethod
    def button(layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def focus_changed(self, old, new):
        editing = isinstance(new, (QLineEdit, QDoubleSpinBox, QComboBox, QSlider))
        for key, shortcut in self.shortcuts:
            shortcut.setEnabled(key == "Escape" or not editing)

    def choose(self):
        path = QFileDialog.getExistingDirectory(self, "选择 episode 目录", self.path.text() or str(Path.home()))
        if path:
            self.path.setText(path)
            self.load()

    def load(self):
        if self.closing:
            return
        try:
            if not self.path.text().strip():
                raise ValueError("请先输入或选择 episode 数据目录。")
            views = discover(Path(self.path.text()), self.config)
            directory = Path(self.path.text()).expanduser().resolve()
            alignment = calculate_alignment(directory, self.alignment_mode.currentData())
            versions = episode_versions(directory)
        except Exception as exc:
            QMessageBox.critical(self, "加载失败", str(exc))
            return
        self.pause()
        for worker in self.workers.values():
            worker.stop()
        self.workers.clear()
        self.metadata.clear()
        self.batch.clear()
        self.step_pending = False
        self.generation += 1
        while self.list_layout.count():
            self.list_layout.takeAt(0).widget().deleteLater()
        self.rows.clear()
        self.views = views
        self.directory = directory
        self.manual_offsets = {v.path: v.offset_ms for v in views}
        apply_alignment(views, alignment)
        self.alignment_label.setText(alignment.message)
        self.version_label.setText(
            f"背包软件：{versions['host_software_version']}  ·  数据版本：{versions['host_data_version']}"
            f"  ·  硬件：{versions['host_hardware_version']}\n"
            f"Ego 软件：{versions['ego_software_version']}  ·  SDK：{versions['ego_sdk_version']}"
            f"  ·  Ego 数据版本：{versions['ego_data_version']}")
        self.position, self.duration, self.zoomed = 0.0, 0.0, None
        for i in range(len(views)):
            self.create_row(i)
        self.sync_selection()
        self.statusBar().showMessage(f"发现 {len(views)} 路视频条目；{alignment.message}")

    def change_alignment(self):
        if not self.views:
            return
        self.pause()
        mode = self.alignment_mode.currentData()
        alignment = calculate_alignment(self.directory, mode)
        if mode == "manual":
            for view in self.views:
                view.offset_ms = self.manual_offsets.get(view.path, 0.0)
        else:
            apply_alignment(self.views, alignment)
        for i, view in enumerate(self.views):
            spin = self.rows[i][1]
            spin.blockSignals(True)
            spin.setValue(view.offset_ms)
            spin.blockSignals(False)
        self.alignment_label.setText(alignment.message)
        self.statusBar().showMessage(alignment.message)
        self.update_duration()
        self.seek(0)

    def create_row(self, i):
        view = self.views[i]
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 3, 0, 3)
        enabled = QCheckBox(view.name)
        enabled.setChecked(view.enabled)
        enabled.setToolTip(str(view.path))
        enabled.toggled.connect(self.sync_selection)
        layout.addWidget(enabled)
        line = QHBoxLayout()
        offset = QDoubleSpinBox()
        offset.setRange(-3600000, 3600000)
        offset.setDecimals(3)
        offset.setSingleStep(10)
        offset.setSuffix(" ms")
        offset.setKeyboardTracking(False)
        offset.setValue(view.offset_ms)
        offset.valueChanged.connect(lambda value, index=i: self.apply_offset(index, value))
        line.addWidget(offset)
        if not view.path.is_file():
            line.addWidget(QLabel("文件缺失"))
        layout.addLayout(line)
        self.list_layout.addWidget(row)
        self.rows[i] = (enabled, offset)

    def apply_offset(self, i, value):
        # Editing an automatic result explicitly changes to manual mode.
        if self.alignment_mode.currentData() != "manual":
            self.alignment_mode.blockSignals(True)
            self.alignment_mode.setCurrentIndex(MODES.index("manual"))
            self.alignment_mode.blockSignals(False)
            self.manual_offsets = {v.path: v.offset_ms for v in self.views}
            self.alignment_label.setText("手动偏移；按每路输入值播放")
        self.views[i].offset_ms = value
        self.manual_offsets[self.views[i].path] = value
        self.update_duration()
        self.seek(self.position)

    def sync_selection(self):
        if self.closing:
            return
        self.step_pending = False
        for i, view in enumerate(self.views):
            view.enabled = self.rows[i][0].isChecked()
            if view.enabled and i not in self.workers:
                self.workers[i] = Worker(view.path)
                self.all_workers.add(self.workers[i])
            elif not view.enabled and i in self.workers:
                self.workers.pop(i).stop()
        if self.zoomed not in self.workers:
            self.zoomed = None
        if not self.workers:
            self.pause()
        self.update_duration()
        self.layout_panels()
        self.request_frames()

    def layout_panels(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.panels.clear()
        for i in range(max(self.grid.rowCount(), self.grid.columnCount())):
            self.grid.setRowStretch(i, 0)
            self.grid.setColumnStretch(i, 0)
        indices = [self.zoomed] if self.zoomed is not None else sorted(self.workers)
        if not indices:
            empty = QLabel("选择目录并勾选要显示的视角")
            empty.setStyleSheet("color: #cbd5e1; font-size: 18px")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(empty, 0, 0)
            return
        default_three = len(indices) == 3 and [self.views[i].path.name for i in indices] == ["rgb.mp4", "cam_left.mkv", "cam_right.mkv"]
        columns = 1 if len(indices) == 1 else (2 if len(indices) <= 4 else 3)
        for n, i in enumerate(indices):
            box = QWidget()
            box.setObjectName("panel")
            layout = QVBoxLayout(box)
            layout.setContentsMargins(3, 2, 3, 2)
            layout.setSpacing(0)
            title = QLabel(self.views[i].name)
            title.setObjectName("title")
            title.setToolTip(str(self.views[i].path))
            canvas = VideoCanvas()
            canvas.double_clicked.connect(lambda index=i: self.zoom(index))
            info = QLabel("等待解码…")
            info.setObjectName("info")
            info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            layout.addWidget(title)
            layout.addWidget(canvas, 1)
            layout.addWidget(info)
            if default_three:
                row, col, span = (0, 0, 2) if n == 0 else (1, n - 1, 1)
            else:
                row, col, span = n // columns, n % columns, 1
            self.grid.addWidget(box, row, col, 1, span)
            self.grid.setRowStretch(row, 1)
            for column in range(columns):
                self.grid.setColumnStretch(column, 1)
            self.panels[i] = (canvas, info)
        self.grid.activate()

    def zoom(self, index):
        self.zoomed = None if index == self.zoomed else index
        self.layout_panels()
        self.request_frames()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "resize_timer"):
            self.resize_timer.start(180)

    def resized(self):
        if not self.step_pending:
            self.request_frames()

    def request_frames(self):
        if self.closing:
            return
        self.step_pending = False
        self.generation += 1
        self.batch = set(self.workers)
        self.received = {}
        for i, worker in self.workers.items():
            if i in self.panels:
                canvas, _ = self.panels[i]
                size = (max(80, canvas.width()), max(60, canvas.height()))
            else:
                size = (320, 180)
            worker.request(self.generation, self.position - self.views[i].offset_ms / 1000, size)
        self.update_clock()

    def poll(self):
        self.all_workers = {worker for worker in self.all_workers if worker.is_alive()}
        for i, worker in list(self.workers.items()):
            try:
                generation, value = worker.results.get_nowait()
            except Empty:
                continue
            if generation != self.generation:
                continue
            if self.step_pending:
                self.step_pending = False
                if "step" in value:
                    self.seek(value["step"] + self.views[i].offset_ms / 1000)
                else:
                    self.statusBar().showMessage(f"逐帧失败：{value.get('error', '未知错误')}")
                    self.request_frames()
                break
            self.received[i] = value
            self.batch.discard(i)
        if not self.batch and self.received:
            self.render_batch()
            self.received = {}
        if self.playing and not self.batch and not self.step_pending:
            target = self.anchor_position + (time.monotonic() - self.anchor_time) * float(self.speed.currentText())
            self.position = min(target, self.duration) if self.duration else target
            if self.duration and target >= self.duration:
                self.pause()
            self.request_frames()

    def render_batch(self):
        for i, value in self.received.items():
            if "error" not in value:
                self.metadata[i] = value["duration"]
            if i not in self.panels:
                continue
            canvas, info = self.panels[i]
            if "error" in value:
                canvas.set_error("无法读取视频")
                info.setText(value["error"])
                info.setToolTip(value["error"])
                self.statusBar().showMessage(f"{self.views[i].name}：{value['error']}")
                continue
            canvas.set_image(value["image"])
            flag = " · 未开始" if value["waiting"] else (" · 已结束" if value["ended"] else "")
            w, h = value["resolution"]
            info.setText(f"{w}×{h} · {value['fps']:.2f} FPS · 帧时间 {clock_text(value['pts'])}{flag}")
        self.update_duration()
        self.update_clock()

    def update_duration(self):
        self.duration = max(0.0, max((self.metadata[i] + self.views[i].offset_ms / 1000
                                   for i in self.workers if i in self.metadata), default=0.0))
        self.updating_slider = True
        self.slider.setMaximum(max(1, round(self.duration * 1000)))
        self.updating_slider = False
        if self.duration and self.position > self.duration:
            self.position = self.duration
            self.reanchor()

    def update_clock(self):
        if not self.slider.dragging:
            self.updating_slider = True
            self.slider.setValue(round(self.position * 1000))
            self.updating_slider = False
            self.time_label.setText(f"{clock_text(self.position)} / {clock_text(self.duration)}")

    def reanchor(self, *args):
        self.anchor_position, self.anchor_time = self.position, time.monotonic()

    def pause(self):
        self.playing = False
        self.play_button.setText("播放")

    def toggle_play(self):
        if self.playing:
            self.pause()
        elif self.workers and self.duration > 0:
            if self.position >= self.duration:
                self.seek(0)
            if self.step_pending:
                self.request_frames()
            self.playing = True
            self.reanchor()
            self.play_button.setText("暂停")

    def seek(self, target):
        self.position = max(0.0, min(target, self.duration)) if self.duration else max(0.0, target)
        self.reanchor()
        self.request_frames()

    def drag_start(self):
        self.resume_after_drag = self.playing
        self.pause()

    def slider_changed(self, value):
        if self.slider.dragging:
            self.time_label.setText(f"{clock_text(value / 1000)} / {clock_text(self.duration)}")
        elif not self.updating_slider:
            self.seek(value / 1000)

    def drag_end(self):
        self.seek(self.slider.value() / 1000)
        if getattr(self, "resume_after_drag", False):
            self.toggle_play()

    def step(self, direction):
        self.pause()
        if not self.workers or self.step_pending:
            return
        available = sorted(i for i in self.workers if i in self.metadata)
        if not available:
            return
        reference = next((i for i in available if self.views[i].path.as_posix().endswith("/ego/rgb.mp4")), available[0])
        self.generation += 1
        self.batch.clear()
        self.received.clear()
        self.step_pending = True
        self.workers[reference].request(self.generation, self.position - self.views[reference].offset_ms / 1000,
                                       (320, 180), direction)
        self.statusBar().showMessage(f"逐帧参考：{self.views[reference].name}")

    def add_video(self):
        if not self.views:
            QMessageBox.information(self, "添加视频", "请先加载数据目录。")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "添加其他视角", str(self.directory),
                                               "视频 (*.mp4 *.mkv *.avi *.mov *.webm *.m4v);;所有文件 (*)")
        for name in paths:
            path = Path(name).resolve()
            if any(view.path == path for view in self.views):
                continue
            self.views.append(View(path.stem, path, True))
            self.create_row(len(self.views) - 1)
        if paths:
            self.sync_selection()

    def save_config(self):
        if not self.views:
            return
        name, _ = QFileDialog.getSaveFileName(self, "保存视角配置", str(self.config.parent / "custom_views.json"), "JSON (*.json)")
        if not name:
            return
        items = []
        for view in self.views:
            try:
                path = str(view.path.relative_to(self.directory))
            except ValueError:
                path = str(view.path)
            # An automatic configuration is portable across episodes: never save
            # calculated offsets as manual values for the next recording.
            offset = view.offset_ms if self.alignment_mode.currentData() == "manual" else 0.0
            items.append({"name": view.name, "path": path, "enabled": view.enabled, "offset_ms": offset})
        try:
            settings = {"time_alignment": self.alignment_mode.currentData(), "views": items}
            Path(name).write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.statusBar().showMessage(f"配置已保存；下次启动可指定 --config {name}")

    def stop_workers(self):
        self.closing = True
        self.playing = False
        self.timer.stop()
        self.resize_timer.stop()
        for worker in self.all_workers:
            worker.stop()

    def shutdown(self, timeout=2.0):
        """Join active and retired decoders using one shared exit deadline."""
        self.stop_workers()
        deadline = time.monotonic() + timeout
        for worker in self.all_workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        self.all_workers = {worker for worker in self.all_workers if worker.is_alive()}
        return not self.all_workers

    def closeEvent(self, event):
        self.stop_workers()
        event.accept()
