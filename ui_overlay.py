"""Compact QWidget HUD. Every native window is protected before being shown."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import re
import time
from collections import deque

from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QApplication, QButtonGroup, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizeGrip, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget

from pipeline import Mode, Source, SpeechResult
from config import AppConfig
from ui_settings import SettingsPanel
from stealth_win32 import CaptureGuard, HOTKEY_ID, ProtectionError, WM_HOTKEY


def action_icon(kind: str) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#D1D1D6"), 1.35))
    if kind == "copy":
        painter.drawRoundedRect(QRectF(5, 5, 8, 9), 1, 1)
        painter.drawLine(3, 10, 3, 2)
        painter.drawLine(3, 2, 10, 2)
    elif kind == "pause":
        painter.drawLine(5, 3, 5, 13)
        painter.drawLine(11, 3, 11, 13)
    elif kind == "play":
        path = QPainterPath()
        path.moveTo(4, 2)
        path.lineTo(13, 8)
        path.lineTo(4, 14)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "close":
        painter.drawLine(4, 4, 12, 12)
        painter.drawLine(12, 4, 4, 12)
    elif kind == "minimize":
        painter.drawLine(3, 10, 13, 10)
    elif kind == "settings":
        painter.drawEllipse(QRectF(3, 3, 10, 10))
        painter.drawEllipse(QRectF(6, 6, 4, 4))
    else:
        painter.end()
        raise ValueError("Unknown action icon.")
    painter.end()
    return QIcon(pixmap)


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.values = deque([0.0] * 40, maxlen=40)
        self.setMinimumWidth(70)
        self.setFixedHeight(24)

    def add_level(self, level):
        self.values.append(max(0, min(1, level)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#64D2FF"), 2))
        step = self.width() / 40
        for index, level in enumerate(self.values):
            half = 1 + level * 10
            x = round(index * step + step / 2)
            painter.drawLine(x, round(12 - half), x, round(12 + half))


class Overlay(QWidget):
    mode_changed = pyqtSignal(object)
    pause_requested = pyqtSignal()
    shutdown_requested = pyqtSignal()
    protection_failed = pyqtSignal(str)
    record_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    translate_text_requested = pyqtSignal(str)
    settings_saved = pyqtSignal(object)
    settings_opened = pyqtSignal(bool)
    discover_models = pyqtSignal(object)
    local_hidden = pyqtSignal()

    def __init__(self, guard=None, config=None) -> None:
        super().__init__(flags=(Qt.WindowType.FramelessWindowHint
                               | Qt.WindowType.WindowStaysOnTopHint
                               | Qt.WindowType.Tool))
        self._armed = self._checking = self._failed = self._closing = False
        self._guard = guard if guard is not None else CaptureGuard()
        self.mode = Mode.TRANSCRIPT
        self.config = config or AppConfig()
        self._settings_open = False
        self._size_before_settings = QSize(520, 140)
        self._recording = False
        self._working = False
        self._can_record = False
        self._record_started = 0.0
        self.latest_translation = ""
        self._status_color = QColor("#FF9F0A")
        self._status_value = None
        self._phase = 0.0
        self._drag_offset = None
        self._notice_text = "Loading speech engine…"
        self.setWindowTitle("Meeting HUD")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(460, 120)
        self.resize(520, 140)
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet("""
            QWidget { color: #F2F2F7; }
            QLabel { background: transparent; }
            QPushButton { background: transparent; border: none; border-radius: 6px;
                          padding: 5px 8px; color: #AEAEB8; }
            QPushButton:hover { background: rgba(255,255,255,20); color: #F2F2F7; }
            QPushButton:checked { background: rgba(255,255,255,28); color: #F2F2F7; }
            QPushButton#translation:checked { color: #64D2FF; background: rgba(100,210,255,24); }
            QPushButton:disabled { color: #51515A; }
            QTextEdit { background: transparent; border: none; color: #F2F2F7; }
            QScrollBar:vertical { background: transparent; width: 3px; margin: 0; }
            QScrollBar::handle:vertical { background: #50525B; border-radius: 1px; min-height: 12px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QSizeGrip { background: transparent; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 8)
        root.setSpacing(5)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        self.live = QLabel("LOAD")
        self.live.setFixedWidth(42)
        self.live.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        header.addWidget(self.dot)
        header.addWidget(self.live)
        self.transcript_button = QPushButton("Live Transcript")
        self.translation_button = QPushButton("Translation")
        self.translation_button.setObjectName("translation")
        self._modes = QButtonGroup(self)
        self._modes.setExclusive(True)
        for button in (self.transcript_button, self.translation_button):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._modes.addButton(button)
            header.addWidget(button)
        self.transcript_button.setChecked(True)
        self.transcript_button.clicked.connect(lambda: self.select_mode(Mode.TRANSCRIPT))
        self.translation_button.clicked.connect(lambda: self.select_mode(Mode.TRANSLATION))
        header.addStretch(1)

        def action(kind, label, slot):
            button = QPushButton()
            button.setFixedSize(27, 27)
            button.setIcon(action_icon(kind))
            button.setIconSize(QSize(14, 14))
            button.setAccessibleName(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # No native tooltips: each tooltip would be another unprotected window.
            button.clicked.connect(slot)
            header.addWidget(button)
            return button

        self.copy_button = action("copy", "Copy latest English sentence", self.copy_latest)
        self.copy_button.setEnabled(False)
        self.pause_button = action("pause", "Pause capture", self.pause_requested.emit)
        self.minimize_button = action("minimize", "Minimize to tray; Ctrl+Shift+H restores", self.minimize_local)
        self.settings_button = action("settings", "Models and API settings", self.toggle_settings)
        self.close_button = action("close", "Close Meeting HUD", self.shutdown_requested.emit)
        root.addLayout(header)
        self.record_panel = QWidget()
        record_row = QHBoxLayout(self.record_panel)
        record_row.setContentsMargins(0, 0, 0, 0)
        self.record_button = QPushButton("Record")
        self.record_button.clicked.connect(self.record_requested.emit)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.waveform = Waveform()
        self.record_clock = QLabel("MIC OFF")
        record_row.addWidget(self.record_button)
        record_row.addWidget(self.cancel_button)
        record_row.addWidget(self.waveform, 1)
        record_row.addWidget(self.record_clock)
        root.addWidget(self.record_panel)
        self.source_panel = QWidget()
        source_layout = QVBoxLayout(self.source_panel)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_header = QHBoxLayout()
        source_header.addWidget(QLabel("Recognized words · edit to correct"))
        self.retranslate_button = QPushButton("Translate text")
        self.retranslate_button.clicked.connect(lambda: self.translate_text_requested.emit(self.source_edit.toPlainText()))
        source_header.addStretch(1)
        source_header.addWidget(self.retranslate_button)
        source_layout.addLayout(source_header)
        self.source_edit = QPlainTextEdit()
        self.source_edit.setMaximumHeight(72)
        self.source_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.source_edit.setPlaceholderText("Press Record, speak, then Stop & Translate.")
        self.source_edit.setStyleSheet("QPlainTextEdit {background: rgba(255,255,255,8); border: 1px solid #363943; border-radius: 5px; padding: 4px;}")
        self.source_edit.textChanged.connect(self._source_changed)
        source_layout.addWidget(self.source_edit)
        root.addWidget(self.source_panel)
        self.record_panel.hide()
        self.source_panel.hide()
        self.settings_editor = SettingsPanel(self.config)
        self.settings_editor.saved.connect(self.settings_saved.emit)
        self.settings_editor.discover.connect(self.discover_models.emit)
        self.settings_panel = QScrollArea()
        self.settings_panel.setWidgetResizable(True)
        self.settings_panel.setWidget(self.settings_editor)
        self.settings_panel.setFrameShape(QScrollArea.Shape.NoFrame)
        self.settings_panel.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background: transparent;}")
        root.addWidget(self.settings_panel)
        self.settings_panel.hide()
        self.content = QTextEdit()
        self.content.setReadOnly(True)
        self.content.setAcceptRichText(False)
        self.content.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.content.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content.setFont(QFont("Segoe UI", 11))
        self.content.document().setMaximumBlockCount(200)
        self.content.document().setDocumentMargin(0)
        self.content.setPlaceholderText("Meeting audio and your microphone will appear here.")
        root.addWidget(self.content, 1)
        self.partial_caption = QLabel()
        self.partial_caption.setTextFormat(Qt.TextFormat.PlainText)
        self.partial_caption.setWordWrap(True)
        self.partial_caption.setMaximumHeight(38)
        self.partial_caption.setStyleSheet("color: #A4A7B3; font-size: 11px;")
        self.partial_caption.hide()
        self._partials = {}
        root.addWidget(self.partial_caption)
        self.notice = QLabel()
        self.notice.setFont(QFont("Segoe UI", 8))
        self.notice.setFixedHeight(14)
        self.notice.setStyleSheet("color: #9396A1;")
        root.addWidget(self.notice)
        self.grip = QSizeGrip(self)
        self.grip.resize(13, 13)
        self._scroll = QPropertyAnimation(self.content.verticalScrollBar(), b"value", self)
        self._scroll.setDuration(130)
        self._scroll.setEasingCurve(QEasingCurve.Type.OutCubic)
        for widget in (self, self.dot, self.live, self.notice, self.content.viewport()):
            widget.installEventFilter(self)
        self._pulse = QTimer(self)
        self._pulse.setInterval(90)
        self._pulse.timeout.connect(self._pulse_tick)
        self._pulse.start()
        self.tray = QSystemTrayIcon(action_icon("settings"), self)
        self.tray.activated.connect(self._tray_activated)
        self._verify_timer = QTimer(self)
        self._verify_timer.setInterval(1000)
        self._verify_timer.timeout.connect(self._verify_protection)
        self.set_notice(self._notice_text)
        self._pulse_tick()

        try:
            self._guard.protect(int(self.winId()))
        except Exception:
            self._pulse.stop()
            self._guard.close()
            raise
        self._armed = True
        self._verify_timer.start()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(available.x() + (available.width() - self.width()) // 2,
                      available.bottom() - self.height() - 44)

    def _protect(self) -> bool:
        if self._closing or self._failed:
            return False
        if self._checking:
            return True
        self._checking = True
        try:
            self._guard.protect(int(self.winId()))
            return True
        except ProtectionError as exc:
            self._fail_protection(str(exc))
            return False
        finally:
            self._checking = False

    def _fail_protection(self, message: str) -> None:
        if not self._failed:
            self._failed = True
            self.hide()
            self._verify_timer.stop()
            self.protection_failed.emit(message)

    def _verify_protection(self) -> None:
        if self._closing or self._failed:
            return
        try:
            self._guard.verify(int(self.winId()))
        except ProtectionError as exc:
            self._fail_protection(str(exc))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._armed:
            self._protect()

    def event(self, event) -> bool:
        if (event.type() == QEvent.Type.WinIdChange
                and getattr(self, "_armed", False) and not self._checking):
            self._protect()
        return super().event(event)

    def nativeEvent(self, event_type, message):
        try:
            if message and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.toggle_visibility()
                    return True, 0
        except Exception:
            return False, 0
        # Do not call the SIP base fallback: this aborted on the user's Windows build.
        return False, 0

    def toggle_visibility(self) -> None:
        if self._failed or self._closing:
            return
        if self.isVisible():
            self.minimize_local()
        elif self._protect():
            self.show()
            self.raise_()

    def minimize_local(self):
        self.local_hidden.emit()
        self.hide()

    def restore_local(self):
        if self._protect():
            self.show()
            self.raise_()
            self.activateWindow()  # Best effort; Windows may deny foreground focus.

    def set_partial(self, item):
        self._partials[item.source] = item.text
        text = "\n".join(f"{'YOU' if source == Source.MIC else 'MEETING'}: {value}"
                         for source, value in self._partials.items() if value)
        self.partial_caption.setText(text[:800])
        self.partial_caption.setVisible(bool(text) and self.mode == Mode.TRANSCRIPT and not self._settings_open)

    def clear_partials(self):
        self._partials.clear()
        self.partial_caption.clear()
        self.partial_caption.hide()

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            if self._protect():
                self.show()
                self.raise_()

    def toggle_settings(self):
        self.cancel_requested.emit()
        if not self._settings_open:
            self._size_before_settings = self.size()
        self._settings_open = not self._settings_open
        if not self._settings_open:
            self.resize(self._size_before_settings)
        self.settings_opened.emit(self._settings_open)
        self._layout_mode()

    def _layout_mode(self):
        self.clear_partials()
        translating = self.mode == Mode.TRANSLATION and not self._settings_open
        self.record_panel.setVisible(translating)
        self.source_panel.setVisible(translating)
        self.settings_panel.setVisible(self._settings_open)
        self.content.setVisible(not self._settings_open)
        self.pause_button.setVisible(self.mode == Mode.TRANSCRIPT and not self._settings_open)
        if self._settings_open:
            self.resize(max(520, self.width()), 560)
        elif translating:
            self.resize(max(520, self.width()), max(260, self.height()))
        else:
            self.resize(self.width(), 140)
        self.layout().activate()
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
            self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)),
                      max(area.top(), min(self.y(), area.bottom() - self.height() + 1)))

    def set_record_state(self, recording, working, ready):
        if recording and not self._recording:
            self._record_started = time.monotonic()
            self.source_edit.clear()
            self.clear_translation()
        self._recording, self._working, self._can_record = recording, working, ready
        self.record_button.setText("Stop → Translate" if recording else "Record")
        self.record_button.setEnabled(ready and (recording or not working))
        self.cancel_button.setEnabled(recording or working)
        self.source_edit.setReadOnly(recording or working)
        self.retranslate_button.setEnabled(ready and not recording and not working and bool(self.source_edit.toPlainText().strip()))
        if not recording:
            self.record_clock.setText("PROCESSING" if working else "MIC OFF")
            self.waveform.add_level(0)

    def clear_translation(self):
        self.latest_translation = ""
        self.copy_button.setEnabled(False)
        self.content.clear()

    def _source_changed(self):
        self.retranslate_button.setEnabled(self._can_record and not self._recording and not self._working and bool(self.source_edit.toPlainText().strip()))
        if not self._working:
            self.clear_translation()

    def set_recognized(self, text):
        self.source_edit.setPlainText(text)

    def select_mode(self, mode: Mode) -> None:
        if mode == self.mode:
            if self._settings_open:
                self.toggle_settings()
            return
        self.mode = mode
        if self._settings_open:
            self.settings_opened.emit(False)
            self.resize(self._size_before_settings)
        self._settings_open = False
        self._layout_mode()
        self.transcript_button.setChecked(mode == Mode.TRANSCRIPT)
        self.translation_button.setChecked(mode == Mode.TRANSLATION)
        self.content.clear()
        self.latest_translation = ""
        self.copy_button.setEnabled(False)
        self.content.setPlaceholderText("English translation appears here after Stop & Translate."
                                        if mode == Mode.TRANSLATION else
                                        "Meeting audio and your microphone will appear here.")
        self.mode_changed.emit(mode)

    def set_status(self, status: str, paused: bool = False) -> None:
        if self._status_value == (status, paused):
            return
        self._status_value = (status, paused)
        self.live.setText(status)
        self._status_color = QColor("#00E676" if status == "LIVE" else
                                    "#8E8E93" if status == "PAUSED" else "#FF9F0A")
        self.live.setStyleSheet(f"color: {self._status_color.name()};")
        self.pause_button.setIcon(action_icon("play" if paused else "pause"))
        self.pause_button.setAccessibleName("Resume capture" if paused else "Pause capture")
        self._pulse_tick()

    def _pulse_tick(self) -> None:
        self._phase += 0.21
        if self._recording:
            seconds = int(time.monotonic() - self._record_started)
            self.record_clock.setText(f"REC {seconds // 60:02}:{seconds % 60:02}")
        color = QColor(self._status_color)
        color.setAlphaF(0.55 + 0.45 * (math.sin(self._phase) + 1) / 2)
        self.dot.setStyleSheet(f"color: {color.name(QColor.NameFormat.HexArgb)};")

    def set_notice(self, text: str) -> None:
        self._notice_text = text
        self.notice.setText(self.notice.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, self.width() - 38)))

    def add_result(self, result: SpeechResult) -> None:
        if self._failed or self._closing or result.mode != self.mode:
            return
        text = result.text.strip()[:12000]
        if not text:
            return
        translating = result.mode == Mode.TRANSLATION
        if translating:
            self.latest_translation = re.split(r"(?<=[.!?])\s+", text)[-1]
            self.copy_button.setEnabled(True)
        previous_scroll = self.content.verticalScrollBar().value()
        cursor = self.content.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.content.document().isEmpty():
            cursor.insertBlock()
        label = QTextCharFormat()
        label.setForeground(QColor("#8E8E93"))
        label.setFontPointSize(8)
        name = "EN" if translating else "MEETING" if result.source == Source.SYSTEM else "YOU"
        cursor.insertText(name + "  ", label)
        body = QTextCharFormat()
        body.setForeground(QColor("#64D2FF" if translating else "#F2F2F7"))
        body.setFontPointSize(11)
        cursor.insertText(text, body)
        self.content.setTextCursor(cursor)
        self.content.verticalScrollBar().setValue(previous_scroll)
        QTimer.singleShot(0, self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        if self._closing:
            return
        bar = self.content.verticalScrollBar()
        self._scroll.stop()
        self._scroll.setStartValue(bar.value())
        self._scroll.setEndValue(bar.maximum())
        self._scroll.start()

    def copy_latest(self) -> None:
        if self.latest_translation:
            QApplication.clipboard().setText(self.latest_translation)
            self.set_notice("Latest English sentence copied.")

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                return True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
            self._drag_offset = None
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "grip"):
            self.grip.move(self.width() - 15, self.height() - 15)
            self.set_notice(self._notice_text)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        painter.fillPath(path, QColor(20, 22, 29, 235))
        painter.setPen(QPen(QColor(255, 255, 255, 32), 1))
        painter.drawPath(path)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.shutdown_requested.emit()

    def prepare_shutdown(self) -> None:
        self._closing = True
        self.hide()
        self._pulse.stop()
        self._verify_timer.stop()
        self._scroll.stop()
        self.tray.hide()
        self._guard.close()
