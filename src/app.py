import datetime
import os
import subprocess
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget, QComboBox,
)

from .agent import ChatAgent
from .db import delete_metadata, get_summary, get_transcript, load_metadata, update_tag
from .recorder import FOLDER, Recorder
from .summarizer import Summarizer
from .transcriber import Transcriber

_GREEN  = "#2ecc71"
_YELLOW = "#f39c12"
_RED    = "#e74c3c"
_ACCENT = "#2563eb"

_SOURCE_ICON = {"mic": "🎤", "app": "🔊", "both": "🎤+🔊", "unknown": "·"}


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
    """Custom VU meter drawn with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setFixedSize(180, 8)

    def set_value(self, v: float):
        self._value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, QColor("#2a2a2a"))
        if self._value > 0:
            color = (QColor(_GREEN) if self._value < 0.5
                     else QColor(_YELLOW) if self._value < 0.75
                     else QColor(_RED))
            p.fillRect(0, 0, int(r.width() * self._value), r.height(), color)


class SmartRecorderApp(QMainWindow):
    _sig_recording_done = Signal(object)
    _sig_transcribed    = Signal(str, object)
    _sig_summarized     = Signal(str, object)
    _sig_agent_response = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Recorder")
        self.resize(1280, 800)
        self.setMinimumSize(960, 600)

        self._recording = False
        self._stop_event = threading.Event()
        self._monitor_stop = threading.Event()
        self._mic_level = 0.0
        self._app_level = 0.0
        self._recordings: list[dict] = []
        self._record_start: float | None = None
        self._transcribing: set[str] = set()
        self._summarizing: set[str] = set()
        self._filters: dict = {"project": None}
        self._mic_on = True
        self._app_on = False
        self._chat_history: list[dict] = []

        # Signals marshal cross-thread callbacks safely onto the Qt main thread
        self._sig_recording_done.connect(self._on_done)
        self._sig_transcribed.connect(self._on_transcribed)
        self._sig_summarized.connect(self._on_summarized)
        self._sig_agent_response.connect(self._on_agent_response)

        self._recorder = Recorder(
            stop_event=self._stop_event,
            set_mic_level=lambda v: setattr(self, "_mic_level", v),
            set_app_level=lambda v: setattr(self, "_app_level", v),
            on_done=self._sig_recording_done.emit,
        )
        self._transcriber = Transcriber()
        self._summarizer = Summarizer()
        self._agent = ChatAgent()

        self._build_ui()

        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._poll_level)
        self._level_timer.start(50)

        self._timer_timer = QTimer(self)
        self._timer_timer.timeout.connect(self._poll_timer)
        self._timer_timer.start(500)

        self._load_existing_recordings()
        self._start_monitor()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        root.addWidget(self._hline())
        root.addWidget(self._build_content(), stretch=1)

    def _hline(self):
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #2a2a2a;")
        return line

    def _vline(self):
        line = QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet("background-color: #2a2a2a;")
        return line

    def _vsep(self):
        """Vertical separator with horizontal padding for the top bar."""
        sep = QFrame()
        sep.setFixedSize(1, 56)
        sep.setStyleSheet("background-color: #2a2a2a;")
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(16, 0, 16, 0)
        h.addWidget(sep)
        return w

    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(80)
        bar.setStyleSheet("QFrame { background-color: #161616; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(0)

        # ── Section 1: title + status ──
        title_sec = QWidget()
        tl = QVBoxLayout(title_sec)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)

        title_lbl = QLabel("Smart Recorder")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: bold; color: #e0e0e0;")
        tl.addWidget(title_lbl)

        status_row = QWidget()
        sl = QHBoxLayout(status_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("font-size: 10px; color: #555;")
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet("font-size: 11px; color: #666;")
        sl.addWidget(self._status_dot)
        sl.addWidget(self._status_label)
        sl.addStretch()
        tl.addWidget(status_row)

        layout.addWidget(title_sec)
        layout.addWidget(self._vsep())

        # ── Section 2: input sources + level meters ──
        sources_sec = QWidget()
        src_layout = QVBoxLayout(sources_sec)
        src_layout.setContentsMargins(0, 0, 0, 0)
        src_layout.setSpacing(6)

        mic_row = QWidget()
        ml = QHBoxLayout(mic_row)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(10)
        self._mic_btn = self._make_source_btn("🎤  Microphone", active=True)
        self._mic_btn.clicked.connect(self._toggle_mic_source)
        self._mic_meter = LevelBar()
        self._mic_level_lbl = QLabel("0%")
        self._mic_level_lbl.setStyleSheet("font-size: 10px; color: #555; min-width: 30px;")
        self._mic_level_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ml.addWidget(self._mic_btn)
        ml.addWidget(self._mic_meter)
        ml.addWidget(self._mic_level_lbl)

        app_row = QWidget()
        al = QHBoxLayout(app_row)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(10)
        self._app_btn = self._make_source_btn("🔊  App Audio", active=False)
        self._app_btn.clicked.connect(self._toggle_app_source)
        self._app_meter = LevelBar()
        self._app_level_lbl = QLabel("0%")
        self._app_level_lbl.setStyleSheet("font-size: 10px; color: #555; min-width: 30px;")
        self._app_level_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        al.addWidget(self._app_btn)
        al.addWidget(self._app_meter)
        al.addWidget(self._app_level_lbl)

        src_layout.addWidget(mic_row)
        src_layout.addWidget(app_row)
        layout.addWidget(sources_sec)
        layout.addWidget(self._vsep())

        # ── Section 3: timer + record button ──
        rec_sec = QWidget()
        rl = QVBoxLayout(rec_sec)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)

        self._timer_label = QLabel(" ")
        self._timer_label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {_RED};")
        rl.addWidget(self._timer_label)

        self._record_btn = QPushButton("⏺  Record")
        self._record_btn.setFixedSize(160, 42)
        self._set_record_btn_style(recording=False)
        self._record_btn.clicked.connect(self._toggle_recording)
        rl.addWidget(self._record_btn)

        layout.addWidget(rec_sec)
        layout.addStretch()
        return bar

    def _make_source_btn(self, text: str, active: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setFixedSize(130, 28)
        self._style_source_btn(btn, active)
        return btn

    def _style_source_btn(self, btn: QPushButton, active: bool):
        if active:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_ACCENT}; color: white;
                    border: none; border-radius: 8px;
                    font-size: 12px; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #1d4ed8; }}
                QPushButton:disabled {{ background: #374151; color: #6b7280; }}
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #888;
                    border: 1px solid #444; border-radius: 8px;
                    font-size: 12px; padding: 0 8px;
                }
                QPushButton:hover { background: #2a2a2a; color: #bbb; }
                QPushButton:disabled { color: #555; border-color: #333; }
            """)

    def _set_record_btn_style(self, recording: bool):
        if recording:
            self._record_btn.setStyleSheet("""
                QPushButton {
                    background: #1a7a3e; color: white;
                    border: none; border-radius: 21px;
                    font-size: 14px; font-weight: bold;
                }
                QPushButton:hover { background: #27ae60; }
            """)
        else:
            self._record_btn.setStyleSheet("""
                QPushButton {
                    background: #c0392b; color: white;
                    border: none; border-radius: 21px;
                    font-size: 14px; font-weight: bold;
                }
                QPushButton:hover { background: #e74c3c; }
            """)

    def _build_content(self):
        content = QWidget()
        layout = QHBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_recordings_panel(), stretch=1)
        layout.addWidget(self._vline())
        layout.addWidget(self._build_chat_panel())
        return content

    def _build_recordings_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(6)

        # Header
        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("RECORDINGS")
        lbl.setStyleSheet("font-size: 10px; font-weight: bold; color: #666;")
        hl.addWidget(lbl)
        hl.addStretch()
        open_btn = QPushButton("Open Folder")
        open_btn.setFixedSize(90, 24)
        open_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #888;
                border: 1px solid #444; border-radius: 4px; font-size: 11px;
            }
            QPushButton:hover { color: #bbb; border-color: #666; }
        """)
        open_btn.clicked.connect(lambda: subprocess.Popen(["explorer", FOLDER]))
        hl.addWidget(open_btn)
        layout.addWidget(hdr)

        # Filter bar
        filter_bar = QWidget()
        fl = QHBoxLayout(filter_bar)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(6)
        proj_lbl = QLabel("Project:")
        proj_lbl.setStyleSheet("font-size: 11px; color: #777;")
        fl.addWidget(proj_lbl)
        self._project_combo = QComboBox()
        self._project_combo.setFixedSize(160, 28)
        self._project_combo.addItem("All")
        self._project_combo.currentTextChanged.connect(self._on_project_filter_changed)
        fl.addWidget(self._project_combo)
        fl.addStretch()
        layout.addWidget(filter_bar)

        # Scrollable recordings list
        self._list_inner = QWidget()
        self._list_inner.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_inner)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidget(self._list_inner)
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        layout.addWidget(self._list_scroll, stretch=1)

        # Log header
        log_hdr = QWidget()
        lhl = QHBoxLayout(log_hdr)
        lhl.setContentsMargins(0, 0, 0, 0)
        log_lbl = QLabel("LOG")
        log_lbl.setStyleSheet("font-size: 10px; font-weight: bold; color: #666;")
        lhl.addWidget(log_lbl)
        lhl.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(50, 20)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #888;
                border: 1px solid #444; border-radius: 4px; font-size: 10px;
            }
            QPushButton:hover { color: #bbb; }
        """)
        clear_btn.clicked.connect(self._clear_log)
        lhl.addWidget(clear_btn)
        layout.addWidget(log_hdr)

        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(110)
        self._log_box.setStyleSheet("""
            QPlainTextEdit {
                background: #111; color: #9ca3af;
                font-family: 'Courier New'; font-size: 11px;
                border: none; border-radius: 6px; padding: 4px;
            }
        """)
        layout.addWidget(self._log_box)
        return panel

    def _build_chat_panel(self):
        panel = QFrame()
        panel.setObjectName("chatPanel")
        panel.setFixedWidth(340)
        panel.setStyleSheet("QFrame#chatPanel { background: #141414; }")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 20, 12, 14)
        layout.setSpacing(8)

        hdr = QLabel("Assistant")
        hdr.setStyleSheet("font-size: 13px; font-weight: bold; color: #e0e0e0;")
        layout.addWidget(hdr)

        self._chat_inner = QWidget()
        self._chat_inner.setStyleSheet("background: transparent;")
        self._chat_vbox = QVBoxLayout(self._chat_inner)
        self._chat_vbox.setContentsMargins(4, 4, 4, 4)
        self._chat_vbox.setSpacing(4)
        self._chat_vbox.addStretch()

        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidget(self._chat_inner)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet("""
            QScrollArea { border: none; border-radius: 8px; background: #1e1e1e; }
        """)
        layout.addWidget(self._chat_scroll, stretch=1)

        input_row = QWidget()
        il = QHBoxLayout(input_row)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(6)

        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText("Ask something…")
        self._chat_input.setFixedHeight(36)
        self._chat_input.setStyleSheet("""
            QLineEdit {
                background: #2d2d2d; color: #e0e0e0;
                border: none; border-radius: 18px;
                padding: 0 14px; font-size: 12px;
            }
        """)
        self._chat_input.returnPressed.connect(self._send_message)
        il.addWidget(self._chat_input, stretch=1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setFixedSize(60, 36)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: white;
                border: none; border-radius: 18px; font-size: 12px;
            }}
            QPushButton:hover {{ background: #1d4ed8; }}
            QPushButton:disabled {{ background: #374151; color: #6b7280; }}
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

        self._record_btn.setText("⏹  Stop")
        self._set_record_btn_style(recording=True)
        self._mic_btn.setEnabled(False)
        self._app_btn.setEnabled(False)
        self._status_dot.setStyleSheet(f"font-size: 10px; color: {_RED};")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {_RED};")
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
        self._record_btn.setText("⏺  Record")
        self._set_record_btn_style(recording=False)
        self._mic_btn.setEnabled(True)
        self._app_btn.setEnabled(True)
        self._status_dot.setStyleSheet("font-size: 10px; color: #555;")
        self._status_label.setStyleSheet("font-size: 11px; color: #666;")
        self._status_label.setText("Ready")
        self._timer_label.setText(" ")
        self._start_monitor()

        if meta:
            dur = _format_duration(meta["duration"])
            self._log(f"Saved  {os.path.basename(meta['filepath'])}  ({dur})")
            self._recordings.insert(0, meta)
            self._transcribing.add(meta["filepath"])
            self._rebuild_list()
            self._log(f"Transcribing…  {os.path.basename(meta['filepath'])}")
            self._transcriber.transcribe_async(
                meta["filepath"],
                on_done=lambda fp, text: self._sig_transcribed.emit(fp, text),
            )
        else:
            self._log("Recording stopped — no audio captured")

    def _on_transcribed(self, filepath: str, text: str | None):
        self._transcribing.discard(filepath)
        for rec in self._recordings:
            if rec["filepath"] == filepath:
                rec["has_transcript"] = text is not None
                break
        name = os.path.basename(filepath)
        if text is not None:
            preview = text[:70].replace("\n", " ")
            suffix = "…" if len(text) > 70 else ""
            self._log(f'Transcript ready  {name}  "{preview}{suffix}"')
            self._summarizing.add(filepath)
            self._log(f"Summarizing…  {name}")
            self._summarizer.summarize_async(
                filepath,
                text,
                on_done=lambda fp, s: self._sig_summarized.emit(fp, s),
            )
        else:
            self._log(f"Transcription failed  {name}  (check OPENAI_API_KEY)")
        self._rebuild_list()

    def _on_summarized(self, filepath: str, summary: str | None):
        self._summarizing.discard(filepath)
        for rec in self._recordings:
            if rec["filepath"] == filepath:
                rec["has_summary"] = summary is not None
                break
        name = os.path.basename(filepath)
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
            elapsed = int(time.time() - self._record_start)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            self._timer_label.setText(f"● {time_str}")

    # ── Recordings list ────────────────────────────────────────────────────

    def _load_existing_recordings(self):
        self._recordings = load_metadata(FOLDER)
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

        # Remove all rows but keep the trailing stretch
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        if not records:
            msg = "No recordings match the current filter" if self._recordings else "No recordings yet"
            placeholder = QLabel(msg)
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #666; font-size: 12px; padding: 24px;")
            self._list_layout.insertWidget(0, placeholder)
            return

        for i, rec in enumerate(records):
            self._list_layout.insertWidget(i, self._make_row(rec))

    def _make_row(self, rec: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("recordingRow")
        row.setStyleSheet("""
            QFrame#recordingRow { background: #242424; border-radius: 8px; }
            QFrame#recordingRow QLabel { background: transparent; }
            QFrame#recordingRow QWidget { background: transparent; }
        """)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 6, 6, 6)
        layout.setSpacing(6)

        # Source badge
        badge = QFrame()
        badge.setFixedSize(32, 32)
        badge.setStyleSheet("background: #333; border-radius: 6px;")
        bl = QHBoxLayout(badge)
        bl.setContentsMargins(0, 0, 0, 0)
        icon = QLabel(_SOURCE_ICON.get(rec["source"], "·"))
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 12px; background: transparent;")
        bl.addWidget(icon)
        layout.addWidget(badge)

        # Recording name
        name_lbl = QLabel(_format_name(rec["filepath"]))
        name_lbl.setStyleSheet("font-size: 12px; color: #e0e0e0;")
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(name_lbl, stretch=1)

        filepath = rec["filepath"]

        # Project (inline editable)
        proj_entry = QLineEdit()
        proj_entry.setPlaceholderText("Project…")
        proj_entry.setFixedSize(96, 28)
        proj_entry.setStyleSheet("""
            QLineEdit {
                background: #2d2d2d; color: #e0e0e0;
                border: none; border-radius: 6px; font-size: 11px; padding: 0 6px;
            }
        """)
        if rec.get("project"):
            proj_entry.setText(rec["project"])

        def _save_project(fp=filepath, r=rec, e=proj_entry):
            val = e.text().strip()
            r["project"] = val
            update_tag(fp, "project", val)
            self._refresh_project_combo()
            if self._filters.get("project") is not None:
                self._rebuild_list()

        proj_entry.editingFinished.connect(_save_project)
        layout.addWidget(proj_entry)

        # Meeting (inline editable)
        meet_entry = QLineEdit()
        meet_entry.setPlaceholderText("Meeting…")
        meet_entry.setFixedSize(96, 28)
        meet_entry.setStyleSheet("""
            QLineEdit {
                background: #2d2d2d; color: #e0e0e0;
                border: none; border-radius: 6px; font-size: 11px; padding: 0 6px;
            }
        """)
        if rec.get("meeting"):
            meet_entry.setText(rec["meeting"])

        def _save_meeting(fp=filepath, r=rec, e=meet_entry):
            val = e.text().strip()
            r["meeting"] = val
            update_tag(fp, "meeting", val)

        meet_entry.editingFinished.connect(_save_meeting)
        layout.addWidget(meet_entry)

        # Duration
        dur_str = _format_duration(rec["duration"]) if rec["duration"] > 0 else "—"
        dur_lbl = QLabel(dur_str)
        dur_lbl.setFixedWidth(60)
        dur_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        dur_lbl.setStyleSheet("font-size: 11px; color: #666;")
        layout.addWidget(dur_lbl)

        # Transcript indicator
        if filepath in self._transcribing:
            spin = QLabel("···")
            spin.setFixedWidth(32)
            spin.setAlignment(Qt.AlignCenter)
            spin.setStyleSheet("font-size: 13px; color: #666;")
            layout.addWidget(spin)
        elif rec.get("has_transcript", False):
            tx_btn = QPushButton("📄")
            tx_btn.setFixedSize(30, 28)
            tx_btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; font-size: 14px; color: #888; }
                QPushButton:hover { color: #ccc; }
            """)
            tx_btn.clicked.connect(lambda _=False, p=filepath: self._view_transcript(p))
            layout.addWidget(tx_btn)
        else:
            spacer = QWidget()
            spacer.setFixedSize(32, 28)
            spacer.setStyleSheet("background: transparent;")
            layout.addWidget(spacer)

        # Summary indicator
        if filepath in self._summarizing:
            sum_spin = QLabel("···")
            sum_spin.setFixedWidth(32)
            sum_spin.setAlignment(Qt.AlignCenter)
            sum_spin.setStyleSheet("font-size: 13px; color: #555;")
            layout.addWidget(sum_spin)
        elif rec.get("has_summary", False):
            sum_btn = QPushButton("📝")
            sum_btn.setFixedSize(30, 28)
            sum_btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; font-size: 14px; color: #888; }
                QPushButton:hover { color: #ccc; }
            """)
            sum_btn.clicked.connect(lambda _=False, p=filepath: self._view_summary(p))
            layout.addWidget(sum_btn)
        else:
            sum_spacer = QWidget()
            sum_spacer.setFixedSize(32, 28)
            sum_spacer.setStyleSheet("background: transparent;")
            layout.addWidget(sum_spacer)

        # Play
        play_btn = QPushButton("▶")
        play_btn.setFixedSize(30, 28)
        play_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: white;
                border: none; border-radius: 4px; font-size: 11px;
            }}
            QPushButton:hover {{ background: #1d4ed8; }}
        """)
        play_btn.clicked.connect(lambda _=False, p=filepath: self._play(p))
        layout.addWidget(play_btn)

        # Delete
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(28, 28)
        del_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; font-size: 12px; color: #555; }
            QPushButton:hover { color: #e74c3c; }
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
        bubble.setMaximumWidth(220)
        bubble.setContentsMargins(10, 6, 10, 6)

        if role == "user":
            bubble.setStyleSheet(f"""
                QLabel {{
                    background: {_ACCENT}; color: white;
                    border-radius: 10px; font-size: 12px;
                }}
            """)
            bubble.setAlignment(Qt.AlignRight)
        else:
            bubble.setStyleSheet("""
                QLabel {
                    background: #374151; color: #e0e0e0;
                    border-radius: 10px; font-size: 12px;
                }
            """)
            bubble.setAlignment(Qt.AlignLeft)

        row_widget = QWidget()
        row_widget.setStyleSheet("background: transparent;")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)

        if role == "user":
            row_layout.addStretch()
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch()

        # Insert before the trailing stretch
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

    def _log(self, message: str) -> None:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.appendPlainText(f"[{ts}]  {message}")

    def _clear_log(self) -> None:
        self._log_box.clear()

    def _delete(self, rec: dict):
        delete_metadata(rec["filepath"])
        try:
            os.remove(rec["filepath"])
        except OSError as exc:
            self._log(f"Warning: could not delete {os.path.basename(rec['filepath'])}: {exc}")
        self._transcribing.discard(rec["filepath"])
        if rec in self._recordings:
            self._recordings.remove(rec)
        self._log(f"Deleted  {os.path.basename(rec['filepath'])}")
        self._refresh_project_combo()
        self._rebuild_list()

    def _play(self, filepath: str):
        threading.Thread(
            target=lambda: subprocess.run(
                ["powershell", "-c",
                 f'(New-Object System.Media.SoundPlayer "{filepath}").PlaySync()'],
                check=False,
            ),
            daemon=True,
        ).start()

    def _view_transcript(self, filepath: str):
        transcript = get_transcript(filepath)
        if transcript is None:
            return

        summary = get_summary(filepath)

        dlg = QDialog(self)
        dlg.setWindowTitle(_format_name(filepath))
        dlg.resize(520, 560)
        dlg.setModal(True)
        dlg.setStyleSheet("QDialog { background: #1a1a1a; }")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        if summary:
            sum_lbl = QLabel("SUMMARY")
            sum_lbl.setStyleSheet("font-size: 10px; font-weight: bold; color: #666;")
            layout.addWidget(sum_lbl)

            sum_box = QPlainTextEdit(summary)
            sum_box.setReadOnly(True)
            sum_box.setFixedHeight(160)
            sum_box.setStyleSheet("""
                QPlainTextEdit {
                    background: #1e2a1e; color: #a3d9a5;
                    border: none; border-radius: 6px;
                    font-size: 12px; padding: 8px;
                }
            """)
            layout.addWidget(sum_box)

            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet("background-color: #2a2a2a;")
            layout.addWidget(sep)

        tx_lbl = QLabel("TRANSCRIPT")
        tx_lbl.setStyleSheet("font-size: 10px; font-weight: bold; color: #666;")
        layout.addWidget(tx_lbl)

        txt = QPlainTextEdit(transcript)
        txt.setReadOnly(True)
        txt.setStyleSheet("""
            QPlainTextEdit {
                background: #111; color: #e0e0e0;
                border: none; border-radius: 6px;
                font-size: 13px; padding: 8px;
            }
        """)
        layout.addWidget(txt, stretch=1)
        dlg.exec()

    def _view_summary(self, filepath: str):
        self._view_transcript(filepath)
