import datetime
import os
import subprocess
import threading
import time

import customtkinter as ctk

from .db import delete_metadata, load_metadata
from .recorder import FOLDER, Recorder

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_GREEN  = "#2ecc71"
_YELLOW = "#f39c12"
_RED    = "#e74c3c"

_SOURCE_ICON = {"mic": "🎤", "app": "🔊", "both": "🎤+🔊", "unknown": "·"}


def _format_name(filepath: str) -> str:
    name = os.path.basename(filepath)
    try:
        stem = name.replace("recording_", "").replace(".wav", "")
        dt = datetime.datetime.strptime(stem, "%Y%m%d_%H%M%S")
        today = datetime.date.today()
        if dt.date() == today:
            return f"Today  ·  {dt.strftime('%H:%M:%S')}"
        if dt.date() == today - datetime.timedelta(days=1):
            return f"Yesterday  ·  {dt.strftime('%H:%M:%S')}"
        return f"{dt.strftime('%b')} {dt.day}  ·  {dt.strftime('%H:%M:%S')}"
    except Exception:
        return name


def _format_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


class SmartRecorderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Smart Recorder")
        self.geometry("480x640")
        self.resizable(False, False)

        self._recording = False
        self._stop_event = threading.Event()
        self._level = 0.0
        self._recordings: list[dict] = []
        self._record_start: float | None = None

        self._recorder = Recorder(
            stop_event=self._stop_event,
            set_level=lambda v: setattr(self, "_level", v),
            on_done=lambda meta: self.after(0, self._on_done, meta),
        )

        self._build_ui()
        self._load_existing_recordings()
        self._poll_level()
        self._poll_timer()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            header,
            text="Smart Recorder",
            font=ctk.CTkFont(size=24, weight="bold"),
            anchor="w",
        ).pack(side="left")

        status_pill = ctk.CTkFrame(header, fg_color=("gray82", "gray22"), corner_radius=10)
        status_pill.pack(side="right")
        self._status_dot = ctk.CTkLabel(
            status_pill, text="●", font=ctk.CTkFont(size=11),
            text_color=("gray55", "gray45"), width=18,
        )
        self._status_dot.pack(side="left", padx=(8, 0), pady=5)
        self._status_label = ctk.CTkLabel(
            status_pill, text="Ready",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
        )
        self._status_label.pack(side="left", padx=(2, 10), pady=5)

        self._timer_label = ctk.CTkLabel(
            self, text=" ",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=_RED,
        )
        self._timer_label.pack(pady=(6, 0))

        sources_frame = ctk.CTkFrame(self, fg_color=("gray90", "gray17"), corner_radius=12)
        sources_frame.pack(padx=24, fill="x", pady=(8, 0))

        ctk.CTkLabel(
            sources_frame, text="INPUT SOURCES",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("gray50", "gray50"),
        ).pack(anchor="w", padx=14, pady=(10, 4))

        mic_row = ctk.CTkFrame(sources_frame, fg_color="transparent")
        mic_row.pack(fill="x", padx=12, pady=(0, 2))
        self._mic_on = ctk.BooleanVar(value=True)
        self._mic_switch = ctk.CTkSwitch(
            mic_row, text="  Microphone",
            variable=self._mic_on, onvalue=True, offvalue=False,
            font=ctk.CTkFont(size=13),
        )
        self._mic_switch.pack(side="left", padx=4)

        app_row = ctk.CTkFrame(sources_frame, fg_color="transparent")
        app_row.pack(fill="x", padx=12, pady=(0, 10))
        self._app_on = ctk.BooleanVar(value=False)
        self._app_switch = ctk.CTkSwitch(
            app_row, text="  App Audio",
            variable=self._app_on, onvalue=True, offvalue=False,
            font=ctk.CTkFont(size=13),
        )
        self._app_switch.pack(side="left", padx=4)

        meter_section = ctk.CTkFrame(self, fg_color="transparent")
        meter_section.pack(fill="x", padx=24, pady=(16, 0))

        meter_hdr = ctk.CTkFrame(meter_section, fg_color="transparent")
        meter_hdr.pack(fill="x")
        ctk.CTkLabel(
            meter_hdr, text="LEVEL",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("gray50", "gray50"),
        ).pack(side="left")
        self._level_label = ctk.CTkLabel(
            meter_hdr, text="0%",
            font=ctk.CTkFont(size=10),
            text_color=("gray50", "gray50"),
        )
        self._level_label.pack(side="right")

        self._meter = ctk.CTkProgressBar(
            meter_section, height=12, corner_radius=6,
            progress_color=_GREEN,
        )
        self._meter.set(0)
        self._meter.pack(fill="x", pady=(4, 0))

        self._record_btn = ctk.CTkButton(
            self,
            text="⏺  Start Recording",
            width=210, height=52,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="#c0392b", hover_color="#e74c3c",
            corner_radius=26,
            command=self._toggle_recording,
        )
        self._record_btn.pack(pady=18)

        list_hdr = ctk.CTkFrame(self, fg_color="transparent")
        list_hdr.pack(fill="x", padx=24, pady=(0, 4))

        ctk.CTkLabel(
            list_hdr, text="RECORDINGS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("gray50", "gray50"),
        ).pack(side="left")

        ctk.CTkButton(
            list_hdr, text="Open Folder",
            width=90, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            border_color=("gray65", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray80", "gray25"),
            command=lambda: subprocess.Popen(["explorer", FOLDER]),
        ).pack(side="right")

        self._list_frame = ctk.CTkScrollableFrame(self, corner_radius=10)
        self._list_frame.pack(padx=24, pady=(0, 20), fill="both", expand=True)

    # ── Recording control ──────────────────────────────────────────────────

    def _toggle_recording(self):
        if self._recording:
            self._stop_event.set()
            return

        mic = self._mic_on.get()
        app = self._app_on.get()
        if not mic and not app:
            return

        self._stop_event.clear()
        self._recording = True
        self._record_start = time.time()

        self._record_btn.configure(text="⏹  Stop Recording", fg_color="#1a7a3e", hover_color="#27ae60")
        self._mic_switch.configure(state="disabled")
        self._app_switch.configure(state="disabled")
        self._status_dot.configure(text_color=_RED)
        self._status_label.configure(text="Recording", text_color=_RED)

        target = {
            (True, True):  self._recorder.run_both,
            (True, False): self._recorder.run_mic,
            (False, True): self._recorder.run_loopback,
        }[(mic, app)]
        threading.Thread(target=target, daemon=True).start()

    def _on_done(self, meta: dict | None):
        self._recording = False
        self._record_start = None
        self._level = 0.0
        self._record_btn.configure(text="⏺  Start Recording", fg_color="#c0392b", hover_color="#e74c3c")
        self._mic_switch.configure(state="normal")
        self._app_switch.configure(state="normal")
        self._status_dot.configure(text_color=("gray55", "gray45"))
        self._status_label.configure(text="Ready", text_color=("gray45", "gray55"))
        self._timer_label.configure(text=" ")
        if meta:
            self._recordings.insert(0, meta)
            self._rebuild_list()

    # ── Polling ────────────────────────────────────────────────────────────

    def _poll_level(self):
        self._meter.set(self._level)
        self._level_label.configure(text=f"{self._level * 100:.0f}%")
        color = _GREEN if self._level < 0.5 else (_YELLOW if self._level < 0.75 else _RED)
        self._meter.configure(progress_color=color)
        self.after(50, self._poll_level)

    def _poll_timer(self):
        if self._recording and self._record_start is not None:
            elapsed = int(time.time() - self._record_start)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            self._timer_label.configure(text=f"● {time_str}")
        self.after(500, self._poll_timer)

    # ── Recordings list ────────────────────────────────────────────────────

    def _load_existing_recordings(self):
        self._recordings = load_metadata(FOLDER)
        self._rebuild_list()

    def _rebuild_list(self):
        for widget in self._list_frame.winfo_children():
            widget.destroy()

        if not self._recordings:
            ctk.CTkLabel(
                self._list_frame,
                text="No recordings yet",
                text_color=("gray55", "gray45"),
                font=ctk.CTkFont(size=12),
            ).pack(pady=20)
            return

        for rec in self._recordings:
            self._add_row(rec)

    def _add_row(self, rec: dict):
        row = ctk.CTkFrame(self._list_frame, fg_color=("gray85", "gray20"), corner_radius=8)
        row.pack(fill="x", pady=3, padx=2)

        ctk.CTkLabel(
            row, text=_SOURCE_ICON.get(rec["source"], "·"), width=36,
            font=ctk.CTkFont(size=13), anchor="center",
        ).pack(side="left", padx=(6, 0), pady=6)

        ctk.CTkLabel(
            row, text=_format_name(rec["filepath"]),
            anchor="w", font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=4, fill="x", expand=True)

        ctk.CTkButton(
            row, text="✕", width=28, height=28,
            fg_color="transparent",
            text_color=("gray50", "gray55"),
            hover_color=("gray75", "gray28"),
            font=ctk.CTkFont(size=12),
            command=lambda r=rec: self._delete(r),
        ).pack(side="right", padx=(0, 6), pady=5)

        ctk.CTkButton(
            row, text="▶", width=32, height=28,
            command=lambda r=rec: self._play(r["filepath"]),
        ).pack(side="right", padx=(0, 2), pady=5)

        if rec["duration"] > 0:
            ctk.CTkLabel(
                row, text=_format_duration(rec["duration"]),
                font=ctk.CTkFont(size=11),
                text_color=("gray50", "gray50"),
            ).pack(side="right", padx=(0, 4))

    def _delete(self, rec: dict):
        delete_metadata(rec["filepath"])
        try:
            os.remove(rec["filepath"])
        except OSError:
            pass
        if rec in self._recordings:
            self._recordings.remove(rec)
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
