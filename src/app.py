import datetime
import os
import shutil
import subprocess
import threading
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget, QComboBox,
)

from .agents.agent_orchestrator import ChatAgent
from .db import delete_metadata, get_language, get_summary, get_transcript, get_transcript_raw, get_transcript_x, load_metadata, update_filepath, update_tag
from .recorder import FOLDER, Recorder
from .agents.agent_summarization import SummarizationAgent
from .transcriber import Transcriber
from .transcriber_x import TranscriberX

# ── Color palette (zinc / Linear aesthetic) ───────────────────────────────────
_BG     = "#09090b"
_SURF   = "#111113"
_CARD   = "#18181b"
_INPUT  = "#27272a"
_BD1    = "#27272a"
_BD2    = "#3f3f46"
_T1     = "#fafafa"
_T2     = "#a1a1aa"
_T3     = "#52525b"
_GREEN  = "#34d399"
_YELLOW = "#fbbf24"
_RED    = "#f87171"
_REC    = "#ef4444"
_STOP   = "#22c55e"
_ACCENT = "#6366f1"

# Solid variants (avoid QSS alpha-hex portability issues)
_ACCENT_DIM  = "#1e1b4b"   # very dark indigo (active source-btn bg)
_ACCENT_MID  = "#312e81"   # medium indigo (border / hover)
_ACCENT_LIT  = "#818cf8"   # light indigo text on dark
_STOP_DIM    = "#14532d"   # dark green (stop-btn bg)
_STOP_MID    = "#166534"   # medium green (stop-btn border, normal)
_STOP_BRT    = "#16a34a"   # bright green (stop-btn border, pulse on)
_STOP_LIT    = "#4ade80"   # light green text

_ORANGE = "#e07b39"   # warning color for missing-file rows

_BADGE = {
    "mic":     ("#1a3152", "#60a5fa"),
    "app":     ("#271740", "#a78bfa"),
    "both":    ("#173028", "#34d399"),
    "unknown": ("#1c1c20", "#52525b"),
}
_BADGE_LABEL = {"mic": "MIC", "app": "APP", "both": "MIC+APP", "unknown": "·"}


def _format_name(filepath: str) -> str:
    name = os.path.basename(filepath)
    try:
        stem = name.replace("recording_", "").replace(".wav", "")
        dt = datetime.datetime.strptime(stem, "%Y%m%d_%H%M%S")
        today = datetime.date.today()
        if dt.date() == today:
            return f"Today  ·  {dt.strftime('%H:%M')}"
        if dt.date() == today - datetime.timedelta(days=1):
            return f"Yesterday  ·  {dt.strftime('%H:%M')}"
        return f"{dt.strftime('%b %d')}  ·  {dt.strftime('%H:%M')}"
    except Exception:
        return name


def _format_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


class LevelBar(QWidget):
    """VU meter with rounded ends, color-coded green → amber → red."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setFixedSize(180, 6)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_value(self, v: float):
        self._value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = self.rect()
        p.setBrush(QBrush(QColor(_BD1)))
        p.drawRoundedRect(r, 3, 3)
        if self._value > 0:
            color = (QColor(_GREEN) if self._value < 0.5
                     else QColor(_YELLOW) if self._value < 0.75
                     else QColor(_RED))
            p.setBrush(QBrush(color))
            fill_w = max(6, int(r.width() * self._value))
            p.drawRoundedRect(0, 0, fill_w, r.height(), 3, 3)


class RecordingRow(QFrame):
    """Row widget that highlights on hover without relying on unreliable QSS :hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("recordingRow")
        self.setAttribute(Qt.WA_Hover, True)
        self._hovered = False
        self._apply_style()

    def _apply_style(self):
        bg = "#1c1c20" if self._hovered else _CARD
        bd = _BD2    if self._hovered else "transparent"
        self.setStyleSheet(f"""
            QFrame#recordingRow {{
                background: {bg};
                border: 1px solid {bd};
                border-radius: 8px;
            }}
            QFrame#recordingRow QLabel {{ background: transparent; }}
            QFrame#recordingRow QLineEdit {{
                background: {_INPUT}; color: {_T1};
                border: 1px solid {_BD1}; border-radius: 5px;
                font-size: 11px; padding: 0 6px;
            }}
            QFrame#recordingRow QLineEdit:focus {{ border-color: {_ACCENT_MID}; }}
        """)

    def event(self, e):
        if e.type() == QEvent.HoverEnter:
            self._hovered = True
            self._apply_style()
        elif e.type() == QEvent.HoverLeave:
            self._hovered = False
            self._apply_style()
        return super().event(e)


class SmartRecorderApp(QMainWindow):
    _sig_recording_done  = Signal(object)
    _sig_transcribed     = Signal(int, object, str)
    _sig_transcribed_x   = Signal(int, object)
    _sig_summarized      = Signal(int, object)
    _sig_agent_response  = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Recorder — AI Meeting Notes")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)

        self._recording = False
        self._stop_event = threading.Event()
        self._monitor_stop = threading.Event()
        self._mic_level = 0.0
        self._app_level = 0.0
        self._recordings: list[dict] = []
        self._record_start: float | None = None
        self._transcribing: set[int] = set()
        self._transcribing_x: set[int] = set()
        self._summarizing: set[int] = set()
        self._filters: dict = {"project": None}
        self._mic_on = True
        self._app_on = False
        self._chat_history: list[dict] = []
        self._blink_state = True
        self._pulse_on = False

        self._sig_recording_done.connect(self._on_done)
        self._sig_transcribed.connect(self._on_transcribed)
        self._sig_transcribed_x.connect(self._on_transcribed_x)
        self._sig_summarized.connect(self._on_summarized)
        self._sig_agent_response.connect(self._on_agent_response)

        self._recorder = Recorder(
            stop_event=self._stop_event,
            set_mic_level=lambda v: setattr(self, "_mic_level", v),
            set_app_level=lambda v: setattr(self, "_app_level", v),
            on_done=self._sig_recording_done.emit,
        )
        self._transcriber = Transcriber()
        self._transcriber_x = TranscriberX()
        self._summarizer = SummarizationAgent()
        self._agent = ChatAgent()

        self._build_ui()

        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._poll_level)
        self._level_timer.start(50)

        self._timer_timer = QTimer(self)
        self._timer_timer.timeout.connect(self._poll_timer)
        self._timer_timer.start(500)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(600)
        self._pulse_timer.timeout.connect(self._pulse_record_btn)

        self._load_existing_recordings()
        self._start_monitor()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        central.setStyleSheet(f"background: {_BG};")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        root.addWidget(self._hline())
        root.addWidget(self._build_content(), stretch=1)
        self._build_menubar()

    def _build_menubar(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        scan_action = file_menu.addAction("Scan DB")
        scan_action.triggered.connect(self._scan_db)

    def _hline(self):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background-color: {_BD1};")
        return line

    def _vline(self):
        line = QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background-color: {_BD1};")
        return line

    def _vsep(self):
        sep = QFrame()
        sep.setFixedSize(1, 38)
        sep.setStyleSheet(f"background-color: {_BD1};")
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        h = QHBoxLayout(w)
        h.setContentsMargins(20, 0, 20, 0)
        h.addWidget(sep)
        return w

    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(64)
        bar.setStyleSheet(f"QFrame {{ background-color: {_SURF}; }}")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(0)

        # ── Title ──
        title_sec = QWidget()
        title_sec.setStyleSheet("background: transparent;")
        tl = QVBoxLayout(title_sec)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(3)

        title_lbl = QLabel("Smart Recorder")
        title_lbl.setStyleSheet(
            f"font-size: 36px; font-weight: 800; color: {_ACCENT_LIT}; "
            "letter-spacing: -1.5px; background: transparent;"
        )
        tl.addWidget(title_lbl)

        status_row = QWidget()
        status_row.setStyleSheet("background: transparent;")
        sl = QHBoxLayout(status_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(5)
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"font-size: 7px; color: {_T3}; background: transparent;")
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {_T3}; background: transparent;")
        sl.addWidget(self._status_dot)
        sl.addWidget(self._status_label)
        sl.addStretch()
        tl.addWidget(status_row)

        layout.addWidget(title_sec)
        layout.addWidget(self._vsep())

        # ── Sources ──
        sources_sec = QWidget()
        sources_sec.setStyleSheet("background: transparent;")
        src_layout = QVBoxLayout(sources_sec)
        src_layout.setContentsMargins(0, 0, 0, 0)
        src_layout.setSpacing(5)

        self._mic_btn = self._make_source_btn("Microphone", active=True)
        self._mic_btn.clicked.connect(self._toggle_mic_source)
        self._mic_meter = LevelBar()
        self._mic_level_lbl = self._make_level_lbl()

        self._app_btn = self._make_source_btn("App Audio", active=False)
        self._app_btn.clicked.connect(self._toggle_app_source)
        self._app_meter = LevelBar()
        self._app_level_lbl = self._make_level_lbl()

        for btn, meter, pct_lbl in (
            (self._mic_btn, self._mic_meter, self._mic_level_lbl),
            (self._app_btn, self._app_meter, self._app_level_lbl),
        ):
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(8)
            row_l.addWidget(btn)
            row_l.addWidget(meter)
            row_l.addWidget(pct_lbl)
            src_layout.addWidget(row_w)

        layout.addWidget(sources_sec)
        layout.addWidget(self._vsep())

        # ── Timer + Record button ──
        rec_sec = QWidget()
        rec_sec.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(rec_sec)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(12)

        self._timer_label = QLabel("")
        self._timer_label.setFixedWidth(84)
        self._timer_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {_REC}; background: transparent;"
        )

        self._record_btn = QPushButton("Record")
        self._record_btn.setFixedSize(148, 38)
        self._set_record_btn_style(recording=False)
        self._record_btn.clicked.connect(self._toggle_recording)

        rl.addWidget(self._timer_label)
        rl.addWidget(self._record_btn)

        layout.addWidget(rec_sec)
        layout.addStretch()
        return bar

    def _make_level_lbl(self) -> QLabel:
        lbl = QLabel("0%")
        lbl.setFixedWidth(28)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl.setStyleSheet(f"font-size: 10px; color: {_T3}; background: transparent;")
        return lbl

    def _make_source_btn(self, text: str, active: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(118, 26)
        self._style_source_btn(btn, active)
        return btn

    def _style_source_btn(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_ACCENT_DIM}; color: {_ACCENT_LIT};
                    border: 1px solid {_ACCENT_MID}; border-radius: 5px;
                    font-size: 11px; font-weight: 500;
                }}
                QPushButton:hover {{ background: {_ACCENT_MID}; }}
                QPushButton:disabled {{ background: {_INPUT}; color: {_T3}; border-color: {_BD1}; }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {_T3};
                    border: 1px solid {_BD1}; border-radius: 5px;
                    font-size: 11px;
                }}
                QPushButton:hover {{ background: {_CARD}; color: {_T2}; border-color: {_BD2}; }}
                QPushButton:disabled {{ color: {_T3}; border-color: {_BD1}; }}
            """)

    def _set_record_btn_style(self, recording: bool):
        if recording:
            self._record_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_STOP_DIM}; color: {_STOP_LIT};
                    border: 1px solid {_STOP_MID}; border-radius: 6px;
                    font-size: 13px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {_STOP_MID}; }}
            """)
        else:
            self._record_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_REC}; color: white;
                    border: 1px solid {_REC}; border-radius: 6px;
                    font-size: 13px; font-weight: 600;
                }}
                QPushButton:hover {{ background: #dc2626; border-color: #dc2626; }}
                QPushButton:disabled {{ background: {_INPUT}; color: {_T3}; border-color: {_BD1}; }}
            """)

    def _pulse_record_btn(self):
        self._pulse_on = not self._pulse_on
        border = _STOP_BRT if self._pulse_on else _STOP_MID
        self._record_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_STOP_DIM}; color: {_STOP_LIT};
                border: 1px solid {border}; border-radius: 6px;
                font-size: 13px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {_STOP_MID}; }}
        """)

    def _build_content(self):
        content = QWidget()
        content.setStyleSheet(f"background: {_BG};")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_recordings_panel(), stretch=1)
        layout.addWidget(self._build_chat_panel())
        return content

    def _build_recordings_panel(self):
        panel = QWidget()
        panel.setStyleSheet(f"background: {_BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("RECORDINGS")
        lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 1px; "
            f"color: {_T3}; background: transparent;"
        )
        hl.addWidget(lbl)
        hl.addStretch()
        open_btn = QPushButton("Open Folder")
        open_btn.setFixedSize(90, 24)
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_T3};
                border: 1px solid {_BD1}; border-radius: 4px; font-size: 11px;
            }}
            QPushButton:hover {{ color: {_T2}; border-color: {_BD2}; }}
        """)
        def _open_folder():
            proj = self._filters.get("project")
            path = os.path.join(FOLDER, proj) if proj else FOLDER
            subprocess.Popen(["explorer", path])
        open_btn.clicked.connect(_open_folder)
        hl.addWidget(open_btn)
        layout.addWidget(hdr)

        # Filter bar
        filter_bar = QWidget()
        filter_bar.setStyleSheet("background: transparent;")
        fl = QHBoxLayout(filter_bar)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(6)
        proj_lbl = QLabel("Project")
        proj_lbl.setStyleSheet(f"font-size: 11px; color: {_T3}; background: transparent;")
        fl.addWidget(proj_lbl)
        self._project_combo = QComboBox()
        self._project_combo.setFixedSize(160, 26)
        self._project_combo.addItem("All")
        self._project_combo.currentTextChanged.connect(self._on_project_filter_changed)
        fl.addWidget(self._project_combo)
        fl.addStretch()
        layout.addWidget(filter_bar)

        # Scrollable list
        self._list_inner = QWidget()
        self._list_inner.setStyleSheet(f"background: {_BG};")
        self._list_layout = QVBoxLayout(self._list_inner)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidget(self._list_inner)
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {_BG}; }}")
        layout.addWidget(self._list_scroll, stretch=1)

        # Log header
        log_hdr = QWidget()
        log_hdr.setStyleSheet("background: transparent;")
        lhl = QHBoxLayout(log_hdr)
        lhl.setContentsMargins(0, 4, 0, 0)
        log_lbl = QLabel("LOG")
        log_lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 1px; "
            f"color: {_T3}; background: transparent;"
        )
        lhl.addWidget(log_lbl)
        lhl.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(46, 20)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_T3};
                border: 1px solid {_BD1}; border-radius: 3px; font-size: 10px;
            }}
            QPushButton:hover {{ color: {_T2}; border-color: {_BD2}; }}
        """)
        clear_btn.clicked.connect(self._clear_log)
        lhl.addWidget(clear_btn)
        layout.addWidget(log_hdr)

        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(96)
        self._log_box.setStyleSheet(f"""
            QPlainTextEdit {{
                background: #0d0d0f; color: {_T3};
                font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 10px;
                border: none; border-radius: 6px; padding: 6px;
            }}
        """)
        layout.addWidget(self._log_box)
        return panel

    def _build_chat_panel(self):
        panel = QFrame()
        panel.setObjectName("chatPanel")
        panel.setFixedWidth(340)
        panel.setStyleSheet(
            f"QFrame#chatPanel {{ background: {_SURF}; border-left: 1px solid {_BD1}; }}"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(10)

        # Header with AI badge
        hdr_row = QWidget()
        hdr_row.setStyleSheet("background: transparent;")
        hdr_l = QHBoxLayout(hdr_row)
        hdr_l.setContentsMargins(0, 0, 0, 0)
        hdr_l.setSpacing(8)

        hdr = QLabel("Assistant")
        hdr.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_T1}; background: transparent;")
        hdr_l.addWidget(hdr)

        ai_badge = QLabel("AI")
        ai_badge.setFixedSize(26, 16)
        ai_badge.setAlignment(Qt.AlignCenter)
        ai_badge.setStyleSheet(f"""
            QLabel {{
                background: {_ACCENT_DIM}; color: {_ACCENT_LIT};
                border: 1px solid {_ACCENT_MID}; border-radius: 3px;
                font-size: 9px; font-weight: 700; letter-spacing: 0.5px;
            }}
        """)
        hdr_l.addWidget(ai_badge)
        hdr_l.addStretch()
        layout.addWidget(hdr_row)

        # Chat scroll
        self._chat_inner = QWidget()
        self._chat_inner.setStyleSheet("background: transparent;")
        self._chat_vbox = QVBoxLayout(self._chat_inner)
        self._chat_vbox.setContentsMargins(6, 6, 6, 6)
        self._chat_vbox.setSpacing(6)
        self._chat_vbox.addStretch()

        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidget(self._chat_inner)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet(f"""
            QScrollArea {{ border: 1px solid {_BD1}; border-radius: 8px; background: {_CARD}; }}
        """)
        layout.addWidget(self._chat_scroll, stretch=1)

        # Input row
        input_row = QWidget()
        input_row.setStyleSheet("background: transparent;")
        il = QHBoxLayout(input_row)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(6)

        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("Ask about your recordings…")
        self._chat_input.setFixedHeight(36)
        self._chat_input.setStyleSheet(f"""
            QLineEdit {{
                background: {_INPUT}; color: {_T1};
                border: 1px solid {_BD1}; border-radius: 6px;
                padding: 0 12px; font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {_ACCENT_MID}; }}
        """)
        self._chat_input.returnPressed.connect(self._send_message)
        il.addWidget(self._chat_input, stretch=1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setFixedSize(60, 36)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: white;
                border: none; border-radius: 6px; font-size: 12px; font-weight: 500;
            }}
            QPushButton:hover {{ background: #4f46e5; }}
            QPushButton:disabled {{ background: {_INPUT}; color: {_T3}; }}
        """)
        self._send_btn.clicked.connect(self._send_message)
        il.addWidget(self._send_btn)

        layout.addWidget(input_row)
        return panel

    # ── Source toggles ─────────────────────────────────────────────────────

    def _toggle_mic_source(self):
        self._mic_on = not self._mic_on
        self._style_source_btn(self._mic_btn, self._mic_on)

    def _toggle_app_source(self):
        self._app_on = not self._app_on
        self._style_source_btn(self._app_btn, self._app_on)

    # ── Level monitor ──────────────────────────────────────────────────────

    def _start_monitor(self):
        self._monitor_stop.clear()
        threading.Thread(target=self._recorder.monitor, args=(self._monitor_stop,), daemon=True).start()

    def _stop_monitor(self):
        self._monitor_stop.set()

    # ── Recording control ──────────────────────────────────────────────────

    def _toggle_recording(self):
        if self._recording:
            self._stop_event.set()
            return

        mic = self._mic_on
        app = self._app_on
        if not mic and not app:
            return

        self._stop_monitor()
        self._stop_event.clear()
        self._recording = True
        self._record_start = time.time()

        self._record_btn.setText("Stop Recording")
        self._set_record_btn_style(recording=True)
        self._pulse_timer.start()
        self._mic_btn.setEnabled(False)
        self._app_btn.setEnabled(False)
        self._status_dot.setStyleSheet(f"font-size: 7px; color: {_REC}; background: transparent;")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {_REC}; background: transparent;")
        self._status_label.setText("Recording")

        sources = " + ".join(filter(None, ["Mic" if mic else "", "App Audio" if app else ""]))
        self._log(f"Recording started  [{sources}]")

        target = {
            (True, True):  self._recorder.run_both,
            (True, False): self._recorder.run_mic,
            (False, True): self._recorder.run_loopback,
        }[(mic, app)]
        threading.Thread(target=target, daemon=True).start()

    def _on_done(self, meta: dict | None):
        self._recording = False
        self._record_start = None
        self._pulse_timer.stop()
        self._record_btn.setText("Record")
        self._set_record_btn_style(recording=False)
        self._mic_btn.setEnabled(True)
        self._app_btn.setEnabled(True)
        self._status_dot.setStyleSheet(f"font-size: 7px; color: {_T3}; background: transparent;")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {_T3}; background: transparent;")
        self._status_label.setText("Ready")
        self._timer_label.setText("")
        self._start_monitor()

        if meta:
            dur = _format_duration(meta["duration"])
            self._log(f"Saved  {os.path.basename(meta['filepath'])}  ({dur})")
            self._recordings.insert(0, meta)
            self._transcribing.add(meta["id"])
            self._rebuild_list()
            self._log(f"Transcribing…  {os.path.basename(meta['filepath'])}")
            self._transcriber.transcribe_async(
                meta["id"],
                meta["filepath"],
                on_done=lambda rid, text, lang: self._sig_transcribed.emit(rid, text, lang),
            )
        else:
            self._log("Recording stopped — no audio captured")

    def _on_transcribed(self, rec_id: int, text: str | None, language: str):
        self._transcribing.discard(rec_id)
        filepath = None
        for rec in self._recordings:
            if rec["id"] == rec_id:
                rec["has_transcript"] = text is not None
                rec["language"] = language
                filepath = rec["filepath"]
                break
        name = os.path.basename(filepath) if filepath else str(rec_id)
        if text is not None:
            preview = text[:70].replace("\n", " ")
            suffix = "…" if len(text) > 70 else ""
            self._log(f'Transcript ready  {name}  [{language}]  "{preview}{suffix}"')
            self._summarizing.add(rec_id)
            self._log(f"Summarizing…  {name}")
            self._summarizer.summarize_async(
                rec_id,
                text,
                language,
                on_done=lambda rid, s: self._sig_summarized.emit(rid, s),
            )
        else:
            self._log(f"Transcription failed  {name}  (check OPENAI_API_KEY)")
        self._rebuild_list()

    def _on_summarized(self, rec_id: int, summary: str | None):
        self._summarizing.discard(rec_id)
        filepath = None
        for rec in self._recordings:
            if rec["id"] == rec_id:
                rec["has_summary"] = summary is not None
                filepath = rec["filepath"]
                break
        name = os.path.basename(filepath) if filepath else str(rec_id)
        if summary is not None:
            self._log(f"Summary ready  {name}")
        else:
            self._log(f"Summary failed  {name}  (check DEEPSEEK_API_KEY)")
        self._rebuild_list()

    # ── Polling ────────────────────────────────────────────────────────────

    def _poll_level(self):
        for level, meter, lbl in (
            (self._mic_level, self._mic_meter, self._mic_level_lbl),
            (self._app_level, self._app_meter, self._app_level_lbl),
        ):
            meter.set_value(level)
            lbl.setText(f"{level * 100:.0f}%")

    def _poll_timer(self):
        if self._recording and self._record_start is not None:
            self._blink_state = not self._blink_state
            elapsed = int(time.time() - self._record_start)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            dot = "●" if self._blink_state else " "
            self._timer_label.setText(f"{dot} {time_str}")

    # ── Recordings list ────────────────────────────────────────────────────

    def _load_existing_recordings(self):
        self._recordings = load_metadata(FOLDER)
        self._refresh_project_combo()
        self._rebuild_list()

    def _scan_db(self):
        before = {r["id"] for r in self._recordings}
        self._recordings = load_metadata(FOLDER)
        after = {r["id"] for r in self._recordings}

        new_count = len(after - before)
        missing = sum(1 for r in self._recordings if r.get("file_missing"))

        parts = [f"Scan complete  {len(self._recordings)} recording(s)"]
        if new_count:
            parts.append(f"+{new_count} new")
        if missing:
            parts.append(f"{missing} missing")
        self._log("  ·  ".join(parts))

        self._refresh_project_combo()
        self._rebuild_list()

    def _unique_projects(self) -> list[str]:
        seen: list[str] = []
        for rec in self._recordings:
            p = (rec.get("project") or "").strip()
            if p and p not in seen:
                seen.append(p)
        return sorted(seen)

    def _apply_filters(self) -> list[dict]:
        result = self._recordings
        proj = self._filters.get("project")
        if proj:
            result = [r for r in result if (r.get("project") or "").strip() == proj]
        return result

    def _refresh_project_combo(self):
        projects = self._unique_projects()
        current = self._filters.get("project")
        if current and current not in projects:
            self._filters["project"] = None
            current = None
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItems(["All"] + projects)
        self._project_combo.setCurrentText("All" if current is None else current)
        self._project_combo.blockSignals(False)

    def _on_project_filter_changed(self, value: str):
        self._filters["project"] = None if value == "All" else value
        self._rebuild_list()

    def _rebuild_list(self):
        records = self._apply_filters()

        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        if not records:
            msg = "No recordings match the filter" if self._recordings else "No recordings yet"
            placeholder = QLabel(msg)
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color: {_T3}; font-size: 12px; padding: 24px; background: transparent;")
            self._list_layout.insertWidget(0, placeholder)
            return

        for i, rec in enumerate(records):
            self._list_layout.insertWidget(i, self._make_row(rec))

    # ── Row button factories ──────────────────────────────────────────────

    def _make_process_btn(self, rec_id: int, filepath: str, file_missing: bool, has_transcript: bool) -> QPushButton:
        processing = rec_id in self._transcribing or rec_id in self._summarizing
        btn = QPushButton("···" if processing else "⟳")
        btn.setFixedSize(26, 22)
        if processing:
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { background: #0f1a28; color: #3a5070;"
                " border: 1px solid #1e3040; border-radius: 4px; font-size: 10px; }"
            )
        elif file_missing and not has_transcript:
            btn.setEnabled(False)
            btn.setToolTip("Audio file not found — cannot process")
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #1e2c38;"
                " border: 1px solid #151e28; border-radius: 4px; font-size: 10px; font-weight: 700; }"
            )
        else:
            btn.clicked.connect(lambda _=False, rid=rec_id, p=filepath, fm=file_missing: (
                self._resummarize(rid, p) if fm else self._retranscribe(rid, p)
            ))
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #3a5070;"
                " border: 1px solid #1e3040; border-radius: 4px; font-size: 10px; font-weight: 700; }"
                " QPushButton:hover { background: #101c2a; color: #60a5fa; }"
            )
        return btn

    def _make_t_view_btn(self, rec_id: int, filepath: str, has_transcript: bool) -> QPushButton:
        btn = QPushButton("T")
        btn.setFixedSize(26, 22)
        if has_transcript:
            btn.setStyleSheet(
                "QPushButton { background: #1a3152; color: #60a5fa;"
                " border: 1px solid #1e3f80; border-radius: 4px; font-size: 10px; font-weight: 700; }"
                " QPushButton:hover { background: #1e3f80; }"
            )
            btn.clicked.connect(lambda _=False, rid=rec_id, p=filepath: self._view_transcript(rid, p))
        else:
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #1e2c38;"
                " border: 1px solid #151e28; border-radius: 4px; font-size: 10px; font-weight: 700; }"
            )
        return btn

    def _make_s_view_btn(self, rec_id: int, filepath: str, has_summary: bool) -> QPushButton:
        btn = QPushButton("S")
        btn.setFixedSize(26, 22)
        if has_summary:
            btn.setStyleSheet(
                f"QPushButton {{ background: #112218; color: {_GREEN};"
                f" border: 1px solid #1e3828; border-radius: 4px; font-size: 10px; font-weight: 700; }}"
                f" QPushButton:hover {{ background: #1e3828; }}"
            )
            btn.clicked.connect(lambda _=False, rid=rec_id, p=filepath: self._view_summary(rid, p))
        else:
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #151e18;"
                " border: 1px solid #101810; border-radius: 4px; font-size: 10px; font-weight: 700; }"
            )
        return btn

    def _make_tx_process_btn(self, rec_id: int, filepath: str, file_missing: bool, has_transcript_x: bool) -> QPushButton:
        processing = rec_id in self._transcribing_x
        btn = QPushButton("···" if processing else "⟳X")
        btn.setFixedSize(30, 22)
        if processing:
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { background: #0f1a28; color: #3a5070;"
                " border: 1px solid #1e3040; border-radius: 4px; font-size: 9px; }"
            )
        elif file_missing and not has_transcript_x:
            btn.setEnabled(False)
            btn.setToolTip("Audio file not found — cannot run WhisperX")
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #1e1a2e;"
                " border: 1px solid #151520; border-radius: 4px; font-size: 9px; font-weight: 700; }"
            )
        else:
            btn.setToolTip("Run WhisperX transcription with speaker diarization")
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #4c1d95;"
                " border: 1px solid #2e1060; border-radius: 4px; font-size: 9px; font-weight: 700; }"
                " QPushButton:hover { background: #1a0f36; color: #a78bfa; }"
            )
            btn.clicked.connect(lambda _=False, rid=rec_id, p=filepath: self._retranscribe_x(rid, p))
        return btn

    def _make_tx_view_btn(self, rec_id: int, filepath: str, has_transcript_x: bool) -> QPushButton:
        btn = QPushButton("TX")
        btn.setFixedSize(30, 22)
        if has_transcript_x:
            btn.setStyleSheet(
                "QPushButton { background: #271740; color: #a78bfa;"
                " border: 1px solid #4c1d95; border-radius: 4px; font-size: 10px; font-weight: 700; }"
                " QPushButton:hover { background: #3d1a6e; }"
            )
            btn.clicked.connect(lambda _=False, rid=rec_id, p=filepath: self._view_transcript_x(rid, p))
        else:
            btn.setEnabled(False)
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #1e1a2e;"
                " border: 1px solid #151520; border-radius: 4px; font-size: 10px; font-weight: 700; }"
            )
        return btn

    def _make_row(self, rec: dict) -> RecordingRow:
        row = RecordingRow()
        row.setFixedHeight(52)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(8)

        rec_id = rec["id"]
        filepath = rec["filepath"]
        source = rec.get("source", "unknown")
        file_missing = rec.get("file_missing", False)
        auto_imported = rec.get("auto_imported", False)
        bg_c, fg_c = _BADGE.get(source, _BADGE["unknown"])

        # Source badge
        badge = QLabel(_BADGE_LABEL.get(source, "·"))
        badge.setFixedSize(54, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"""
            QLabel {{
                background: {bg_c}; color: {fg_c};
                border-radius: 4px; font-size: 9px; font-weight: 700; letter-spacing: 0.5px;
            }}
        """)
        layout.addWidget(badge)

        # Name
        if file_missing:
            name_text = f"⚠  {_format_name(filepath)}"
            name_color = _ORANGE
            name_tip = "Audio file not found on disk — transcript and tags are preserved"
        elif auto_imported:
            name_text = _format_name(filepath)
            name_color = _T2
            name_tip = "Imported from folder (no original recording session metadata)"
        else:
            name_text = _format_name(filepath)
            name_color = _T1
            name_tip = ""
        name_lbl = QLabel(name_text)
        name_lbl.setStyleSheet(
            f"font-size: 12px; color: {name_color}; font-weight: 500; background: transparent;"
        )
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if name_tip:
            name_lbl.setToolTip(name_tip)
        layout.addWidget(name_lbl, stretch=1)

        # Project
        proj_entry = QLineEdit()
        proj_entry.setPlaceholderText("Project…")
        proj_entry.setFixedSize(88, 26)
        if rec.get("project"):
            proj_entry.setText(rec["project"])

        def _save_project(r=rec, e=proj_entry):
            val = e.text().strip()
            r["project"] = val
            update_tag(r["id"], "project", val)
            new_fp = self._move_recording(r["id"], r["filepath"], val)
            if new_fp != r["filepath"]:
                r["filepath"] = new_fp
            self._refresh_project_combo()
            self._rebuild_list()

        proj_entry.editingFinished.connect(_save_project)
        layout.addWidget(proj_entry)

        # Meeting
        meet_entry = QLineEdit()
        meet_entry.setPlaceholderText("Meeting…")
        meet_entry.setFixedSize(88, 26)
        if rec.get("meeting"):
            meet_entry.setText(rec["meeting"])

        def _save_meeting(r=rec, e=meet_entry):
            val = e.text().strip()
            r["meeting"] = val
            update_tag(r["id"], "meeting", val)

        meet_entry.editingFinished.connect(_save_meeting)
        layout.addWidget(meet_entry)

        # Duration
        dur_str = _format_duration(rec["duration"]) if rec["duration"] > 0 else "—"
        dur_lbl = QLabel(dur_str)
        dur_lbl.setFixedWidth(52)
        dur_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dur_lbl.setStyleSheet(f"font-size: 11px; color: {_T3}; background: transparent;")
        layout.addWidget(dur_lbl)

        # ── Transcript + Summary: run/view buttons ────────────────────────
        has_transcript   = rec.get("has_transcript", False)
        has_summary      = rec.get("has_summary", False)
        has_transcript_x = rec.get("has_transcript_x", False)
        layout.addWidget(self._make_process_btn(rec_id, filepath, file_missing, has_transcript))
        layout.addWidget(self._make_t_view_btn(rec_id, filepath, has_transcript))
        layout.addWidget(self._make_tx_process_btn(rec_id, filepath, file_missing, has_transcript_x))
        layout.addWidget(self._make_tx_view_btn(rec_id, filepath, has_transcript_x))
        layout.addWidget(self._make_s_view_btn(rec_id, filepath, has_summary))

        # Info
        info_btn = QPushButton("i")
        info_btn.setFixedSize(22, 22)
        info_btn.setToolTip("Recording info")
        info_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_T3};"
            f" border: 1px solid {_BD1}; border-radius: 4px; font-size: 10px; font-style: italic; }}"
            f" QPushButton:hover {{ color: {_T2}; border-color: {_BD2}; }}"
        )
        info_btn.clicked.connect(lambda _=False, rid=rec_id, p=filepath: self._view_info(rid, p))
        layout.addWidget(info_btn)

        # Play
        play_btn = QPushButton("▶")
        play_btn.setFixedSize(28, 26)
        play_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT_DIM}; color: {_ACCENT_LIT};
                border: 1px solid {_ACCENT_MID}; border-radius: 4px; font-size: 10px;
            }}
            QPushButton:hover {{ background: {_ACCENT_MID}; }}
            QPushButton:disabled {{ background: {_INPUT}; color: {_T3}; border-color: {_BD1}; }}
        """)
        if file_missing:
            play_btn.setEnabled(False)
            play_btn.setToolTip("Audio file not found — cannot play")
        else:
            play_btn.clicked.connect(lambda _=False, p=filepath: self._play(p))
        layout.addWidget(play_btn)

        # Delete
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(26, 26)
        del_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; font-size: 11px; color: {_T3}; }}
            QPushButton:hover {{ color: {_RED}; }}
        """)
        del_btn.clicked.connect(lambda _=False, r=rec: self._delete(r))
        layout.addWidget(del_btn)

        return row

    # ── Chat ───────────────────────────────────────────────────────────────

    def _send_message(self):
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self._chat_history.append({"role": "user", "content": text})
        self._add_chat_bubble(text, role="user")
        self._send_btn.setEnabled(False)
        self._send_btn.setText("···")
        self._agent.send_async(
            self._chat_history,
            lambda t: self._sig_agent_response.emit(t),
        )

    def _add_chat_bubble(self, text: str, role: str):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(224)
        bubble.setContentsMargins(10, 7, 10, 7)

        if role == "user":
            bubble.setStyleSheet(f"""
                QLabel {{
                    background: {_ACCENT}; color: white;
                    border-radius: 10px 10px 2px 10px; font-size: 12px;
                }}
            """)
            bubble.setAlignment(Qt.AlignRight)
        else:
            bubble.setStyleSheet(f"""
                QLabel {{
                    background: {_INPUT}; color: {_T1};
                    border-radius: 10px 10px 10px 2px; font-size: 12px;
                }}
            """)
            bubble.setAlignment(Qt.AlignLeft)

        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(4, 0, 4, 0)
        row_layout.setSpacing(0)

        if role == "user":
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch()

        self._chat_vbox.insertWidget(self._chat_vbox.count() - 1, row_widget)

        QTimer.singleShot(10, lambda: self._chat_scroll.verticalScrollBar().setValue(
            self._chat_scroll.verticalScrollBar().maximum()
        ))

    def _on_agent_response(self, text: str | None):
        self._send_btn.setEnabled(True)
        self._send_btn.setText("Send")
        if text is None:
            text = "Error: could not reach assistant."
        else:
            self._chat_history.append({"role": "assistant", "content": text})
        self._add_chat_bubble(text, role="assistant")

    # ── Actions ────────────────────────────────────────────────────────────

    def _move_recording(self, rec_id: int, old_fp: str, project: str) -> str:
        """Move the WAV to its project subfolder (or back to root if project cleared).
        Updates the DB filepath and returns the new path (old_fp on failure)."""
        dest_dir = os.path.join(FOLDER, project) if project else FOLDER
        os.makedirs(dest_dir, exist_ok=True)
        new_fp = os.path.join(dest_dir, os.path.basename(old_fp))
        if new_fp == old_fp:
            return old_fp
        if not os.path.exists(old_fp):
            return old_fp
        try:
            shutil.move(old_fp, new_fp)
        except OSError as exc:
            self._log(f"Warning: could not move {os.path.basename(old_fp)}: {exc}")
            return old_fp
        update_filepath(rec_id, new_fp)
        return new_fp

    def _log(self, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.appendPlainText(f"[{ts}]  {message}")

    def _clear_log(self) -> None:
        self._log_box.clear()

    def _delete(self, rec: dict):
        delete_metadata(rec["id"])
        if rec.get("file_missing"):
            self._log(f"Removed metadata for missing file  {os.path.basename(rec['filepath'])}")
        else:
            try:
                os.remove(rec["filepath"])
            except OSError as exc:
                self._log(f"Warning: could not delete {os.path.basename(rec['filepath'])}: {exc}")
            self._log(f"Deleted  {os.path.basename(rec['filepath'])}")
        self._transcribing.discard(rec["id"])
        self._transcribing_x.discard(rec["id"])
        if rec in self._recordings:
            self._recordings.remove(rec)
        self._refresh_project_combo()
        self._rebuild_list()

    def _play(self, filepath: str):
        if not os.path.exists(filepath):
            self._log(f"File not found — cannot play  {os.path.basename(filepath)}")
            return
        threading.Thread(
            target=lambda: subprocess.run(
                ["powershell", "-c",
                 f'(New-Object System.Media.SoundPlayer "{filepath}").PlaySync()'],
                check=False,
            ),
            daemon=True,
        ).start()

    @staticmethod
    def _summary_to_html(text: str) -> str:
        sections = [s.strip() for s in text.split("\n\n") if s.strip()]
        parts = [
            "<body style='margin:0; padding:0; font-family: sans-serif; font-size: 13px;'>"
        ]
        for section in sections:
            lines = section.splitlines()
            header = lines[0].strip()
            bullets = [l.strip() for l in lines[1:] if l.strip()]
            parts.append(
                f"<p style='margin: 0 0 2px 0; font-size: 10px; font-weight: 700; "
                f"letter-spacing: 1px; color: #34d399;'>{header}</p>"
            )
            for b in bullets:
                parts.append(
                    f"<p style='margin: 0 0 1px 8px; color: #d1fae5;'>{b}</p>"
                )
            parts.append("<p style='margin: 6px 0;'></p>")
        parts.append("</body>")
        return "".join(parts)

    def _view_transcript(self, rec_id: int, filepath: str):
        transcript = get_transcript(rec_id)
        if transcript is None:
            return

        transcript_raw = get_transcript_raw(rec_id)
        summary = get_summary(rec_id)

        dlg = QDialog(self)
        dlg.setWindowTitle(_format_name(filepath))
        dlg.resize(1000, 620)
        dlg.setModal(True)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {_CARD}; }}
            QWidget {{ background: {_CARD}; color: {_T1}; }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        if summary:
            sum_lbl = QLabel("SUMMARY")
            sum_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 600; letter-spacing: 1px; "
                f"color: {_T3}; background: transparent;"
            )
            layout.addWidget(sum_lbl)

            sum_box = QTextEdit()
            sum_box.setReadOnly(True)
            sum_box.setFixedHeight(200)
            sum_box.setHtml(self._summary_to_html(summary))
            sum_box.setStyleSheet(f"""
                QTextEdit {{
                    background: #0f1f14;
                    border: 1px solid #1e3828; border-radius: 6px;
                    padding: 8px;
                }}
            """)
            layout.addWidget(sum_box)

            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(f"background-color: {_BD1};")
            layout.addWidget(sep)

        _tx_box_style = f"""
            QPlainTextEdit {{
                background: {_BG}; color: {_T1};
                border: 1px solid {_BD1}; border-radius: 6px;
                font-size: 13px; padding: 10px;
            }}
        """
        _lbl_style = (
            f"font-size: 10px; font-weight: 600; letter-spacing: 1px; "
            f"color: {_T3}; background: transparent;"
        )

        panels_layout = QHBoxLayout()
        panels_layout.setSpacing(12)

        raw_col = QVBoxLayout()
        raw_col.setSpacing(4)
        raw_lbl = QLabel("RAW")
        raw_lbl.setStyleSheet(_lbl_style)
        raw_col.addWidget(raw_lbl)
        raw_box = QPlainTextEdit(
            transcript_raw if transcript_raw
            else "(not available — recorded before raw transcript was saved)"
        )
        raw_box.setReadOnly(True)
        raw_box.setStyleSheet(_tx_box_style)
        raw_col.addWidget(raw_box, stretch=1)
        panels_layout.addLayout(raw_col, stretch=1)

        corr_col = QVBoxLayout()
        corr_col.setSpacing(4)
        corr_lbl = QLabel("CORRECTED")
        corr_lbl.setStyleSheet(_lbl_style)
        corr_col.addWidget(corr_lbl)
        corr_box = QPlainTextEdit(transcript)
        corr_box.setReadOnly(True)
        corr_box.setStyleSheet(_tx_box_style)
        corr_col.addWidget(corr_box, stretch=1)
        panels_layout.addLayout(corr_col, stretch=1)

        layout.addLayout(panels_layout, stretch=1)
        dlg.exec()

    def _view_summary(self, rec_id: int, filepath: str):
        self._view_transcript(rec_id, filepath)

    def _retranscribe(self, rec_id: int, filepath: str):
        if rec_id in self._transcribing:
            return
        if not os.path.exists(filepath):
            self._log(f"File not found — cannot transcribe  {os.path.basename(filepath)}")
            return
        self._transcribing.add(rec_id)
        self._rebuild_list()
        self._log(f"Transcribing…  {os.path.basename(filepath)}")
        self._transcriber.transcribe_async(
            rec_id,
            filepath,
            on_done=lambda rid, text, lang: self._sig_transcribed.emit(rid, text, lang),
        )

    def _resummarize(self, rec_id: int, filepath: str):
        if rec_id in self._summarizing:
            return
        transcript = get_transcript(rec_id)
        if transcript is None:
            self._retranscribe(rec_id, filepath)
            return
        language = get_language(rec_id) or "spanish"
        self._summarizing.add(rec_id)
        self._rebuild_list()
        self._log(f"Summarizing…  {os.path.basename(filepath)}")
        self._summarizer.summarize_async(
            rec_id, transcript, language,
            on_done=lambda rid, s: self._sig_summarized.emit(rid, s),
        )

    def _retranscribe_x(self, rec_id: int, filepath: str):
        if rec_id in self._transcribing_x:
            return
        if not os.path.exists(filepath):
            self._log(f"File not found — cannot run WhisperX  {os.path.basename(filepath)}")
            return
        self._transcribing_x.add(rec_id)
        self._rebuild_list()
        self._log(f"WhisperX transcribing…  {os.path.basename(filepath)}")
        self._transcriber_x.transcribe_async(
            rec_id,
            filepath,
            on_done=lambda rid, text: self._sig_transcribed_x.emit(rid, text),
        )

    def _on_transcribed_x(self, rec_id: int, text: str | None):
        self._transcribing_x.discard(rec_id)
        filepath = None
        for rec in self._recordings:
            if rec["id"] == rec_id:
                rec["has_transcript_x"] = text is not None
                filepath = rec["filepath"]
                break
        name = os.path.basename(filepath) if filepath else str(rec_id)
        if text is not None:
            self._log(f"WhisperX transcript ready  {name}")
        else:
            self._log(f"WhisperX failed  {name}  (check HF_TOKEN and that whisperx is installed)")
        self._rebuild_list()

    def _view_info(self, rec_id: int, filepath: str):
        dlg = QDialog(self)
        dlg.setWindowTitle("Recording Info")
        dlg.setFixedSize(440, 118)
        dlg.setModal(False)
        dlg.setStyleSheet(f"QDialog {{ background: {_CARD}; }}")

        layout = QFormLayout(dlg)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.setLabelAlignment(Qt.AlignRight)

        def _key(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {_T3}; font-size: 11px; background: transparent;")
            return lbl

        def _val(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lbl.setStyleSheet(f"color: {_T1}; font-size: 12px; background: transparent;")
            return lbl

        layout.addRow(_key("ID"), _val(f"#{rec_id:04d}"))
        layout.addRow(_key("File"), _val(os.path.basename(filepath)))
        layout.addRow(_key("Path"), _val(filepath))
        dlg.exec()

    def _view_transcript_x(self, rec_id: int, filepath: str):
        transcript_x = get_transcript_x(rec_id)
        if transcript_x is None:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"WhisperX — {_format_name(filepath)}")
        dlg.resize(560, 520)
        dlg.setModal(True)
        dlg.setStyleSheet(f"""
            QDialog {{ background: {_CARD}; }}
            QWidget {{ background: {_CARD}; color: {_T1}; }}
        """)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        hdr = QLabel("WHISPERX TRANSCRIPT  ·  SPEAKERS")
        hdr.setStyleSheet(
            "font-size: 10px; font-weight: 600; letter-spacing: 1px; "
            "color: #a78bfa; background: transparent;"
        )
        layout.addWidget(hdr)

        txt = QPlainTextEdit(transcript_x)
        txt.setReadOnly(True)
        txt.setStyleSheet("""
            QPlainTextEdit {
                background: #0f0a1e; color: #e2d9f3;
                border: 1px solid #2e1a5c; border-radius: 6px;
                font-size: 13px; padding: 10px;
            }
        """)
        layout.addWidget(txt, stretch=1)
        dlg.exec()
