import datetime
import os
import subprocess
import threading
import time

import customtkinter as ctk

from .agent import ChatAgent
from .db import delete_metadata, get_transcript, load_metadata, update_tag
from .recorder import FOLDER, Recorder
from .transcriber import Transcriber

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_GREEN        = "#2ecc71"
_YELLOW       = "#f39c12"
_RED          = "#e74c3c"
_ACCENT       = "#2563eb"
_ACCENT_HOVER = "#1d4ed8"

_SOURCE_ICON = {"mic": "🎤", "app": "🔊", "both": "🎤+🔊", "unknown": "·"}


def _format_name(filepath: str) -> str:
    """Format a recording filepath into a human-friendly date/time label."""
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
    """Format seconds as '1m 05s' or '42s'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


class SmartRecorderApp(ctk.CTk):
    """Main application window: wires together the recorder, transcriber, and chat agent."""

    def __init__(self):
        super().__init__()
        self.title("Smart Recorder")
        self.geometry("1280x800")
        self.resizable(True, True)
        self.minsize(960, 600)

        self._recording = False
        self._stop_event = threading.Event()  # signals recorder thread to stop
        self._monitor_stop = threading.Event()
        self._mic_level = 0.0
        self._app_level = 0.0
        self._recordings: list[dict] = []
        self._record_start: float | None = None
        self._transcribing: set[str] = set()  # filepaths currently being transcribed (shows "···")

        self._mic_on = ctk.BooleanVar(value=True)
        self._app_on = ctk.BooleanVar(value=False)

        self._recorder = Recorder(
            stop_event=self._stop_event,
            set_mic_level=lambda v: setattr(self, "_mic_level", v),
            set_app_level=lambda v: setattr(self, "_app_level", v),
            on_done=lambda meta: self.after(0, self._on_done, meta),
        )
        self._transcriber = Transcriber()
        self._agent = ChatAgent()
        self._chat_history: list[dict] = []
        self._chat_scroll: ctk.CTkScrollableFrame | None = None
        self._chat_input: ctk.CTkEntry | None = None
        self._send_btn: ctk.CTkButton | None = None

        self._build_ui()
        self._load_existing_recordings()
        self._poll_level()
        self._poll_timer()
        self._start_monitor()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        """Construct and lay out all UI widgets."""
        # ── Top bar ────────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color=("gray83", "gray16"), corner_radius=0, height=80)
        top.pack(side="top", fill="x")
        top.pack_propagate(False)

        # Section 1: title + status
        title_sec = ctk.CTkFrame(top, fg_color="transparent")
        title_sec.pack(side="left", padx=(20, 0), pady=10)

        ctk.CTkLabel(
            title_sec, text="Smart Recorder",
            font=ctk.CTkFont(size=17, weight="bold"),
            anchor="w",
        ).pack(anchor="w")

        status_row = ctk.CTkFrame(title_sec, fg_color="transparent")
        status_row.pack(anchor="w")
        self._status_dot = ctk.CTkLabel(
            status_row, text="●", font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray45"),
        )
        self._status_dot.pack(side="left")
        self._status_label = ctk.CTkLabel(
            status_row, text="Ready",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
        )
        self._status_label.pack(side="left", padx=(4, 0))

        # Separator
        ctk.CTkFrame(top, fg_color=("gray70", "gray28"), width=1).pack(side="left", fill="y", padx=16, pady=12)

        # Section 2: input sources + level meters (2 rows)
        sources_sec = ctk.CTkFrame(top, fg_color="transparent")
        sources_sec.pack(side="left", pady=10)

        # Row 1: mic button + mic level
        mic_row = ctk.CTkFrame(sources_sec, fg_color="transparent")
        mic_row.pack(anchor="w", pady=(0, 6))
        self._mic_btn = ctk.CTkButton(
            mic_row, text="🎤  Microphone",
            height=28, corner_radius=8, width=130,
            fg_color=_ACCENT, hover_color=_ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
            command=self._toggle_mic_source,
        )
        self._mic_btn.pack(side="left", padx=(0, 10))
        self._mic_meter = ctk.CTkProgressBar(mic_row, height=7, corner_radius=4, progress_color=_GREEN, width=180)
        self._mic_meter.set(0)
        self._mic_meter.pack(side="left")
        self._mic_level_label = ctk.CTkLabel(
            mic_row, text="0%", font=ctk.CTkFont(size=10), width=30, anchor="e",
            text_color=("gray50", "gray50"),
        )
        self._mic_level_label.pack(side="left", padx=(6, 0))

        # Row 2: app audio button + app level
        app_row = ctk.CTkFrame(sources_sec, fg_color="transparent")
        app_row.pack(anchor="w")
        self._app_btn = ctk.CTkButton(
            app_row, text="🔊  App Audio",
            height=28, corner_radius=8, width=130,
            fg_color="transparent", hover_color=("gray74", "gray24"),
            border_width=1, border_color=("gray65", "gray38"),
            text_color=("gray40", "gray60"),
            font=ctk.CTkFont(size=12),
            command=self._toggle_app_source,
        )
        self._app_btn.pack(side="left", padx=(0, 10))
        self._app_meter = ctk.CTkProgressBar(app_row, height=7, corner_radius=4, progress_color=_GREEN, width=180)
        self._app_meter.set(0)
        self._app_meter.pack(side="left")
        self._app_level_label = ctk.CTkLabel(
            app_row, text="0%", font=ctk.CTkFont(size=10), width=30, anchor="e",
            text_color=("gray50", "gray50"),
        )
        self._app_level_label.pack(side="left", padx=(6, 0))

        # Separator
        ctk.CTkFrame(top, fg_color=("gray70", "gray28"), width=1).pack(side="left", fill="y", padx=16, pady=12)

        # Section 4: timer + record button
        rec_sec = ctk.CTkFrame(top, fg_color="transparent")
        rec_sec.pack(side="left", pady=12)

        self._timer_label = ctk.CTkLabel(
            rec_sec, text=" ",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=_RED,
        )
        self._timer_label.pack(anchor="w")

        self._record_btn = ctk.CTkButton(
            rec_sec,
            text="⏺  Record",
            height=42, corner_radius=21, width=160,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#c0392b", hover_color="#e74c3c",
            command=self._toggle_recording,
        )
        self._record_btn.pack(anchor="w", pady=(4, 0))

        # ── Horizontal divider ─────────────────────────────────────────────
        ctk.CTkFrame(self, fg_color=("gray70", "gray24"), height=1).pack(side="top", fill="x")

        # ── Bottom content area ────────────────────────────────────────────
        content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        content.pack(side="top", fill="both", expand=True)

        # ── Right panel ────────────────────────────────────────────────────
        right = ctk.CTkFrame(content, fg_color="transparent", corner_radius=0)
        right.pack(side="left", fill="both", expand=True)

        # Recordings header
        rec_hdr = ctk.CTkFrame(right, fg_color="transparent")
        rec_hdr.pack(fill="x", padx=16, pady=(20, 8))
        ctk.CTkLabel(
            rec_hdr, text="RECORDINGS",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("gray50", "gray50"),
        ).pack(side="left")
        ctk.CTkButton(
            rec_hdr, text="Open Folder",
            width=90, height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1, border_color=("gray65", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray80", "gray25"),
            command=lambda: subprocess.Popen(["explorer", FOLDER]),
        ).pack(side="right")

        # Log panel — textbox packed first (bottommost), then header above it
        self._log_box = ctk.CTkTextbox(
            right, height=110,
            font=ctk.CTkFont(size=11, family="Courier New"),
            state="disabled", corner_radius=8,
        )
        self._log_box.pack(side="bottom", fill="x", padx=16, pady=(0, 12))

        log_hdr = ctk.CTkFrame(right, fg_color="transparent")
        log_hdr.pack(side="bottom", fill="x", padx=16, pady=(6, 2))
        ctk.CTkLabel(
            log_hdr, text="LOG",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=("gray50", "gray50"),
        ).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear",
            width=50, height=20,
            font=ctk.CTkFont(size=10),
            fg_color="transparent",
            border_width=1, border_color=("gray65", "gray40"),
            text_color=("gray40", "gray60"),
            hover_color=("gray80", "gray25"),
            command=self._clear_log,
        ).pack(side="right")

        # Recordings list
        self._list_frame = ctk.CTkScrollableFrame(right, corner_radius=8)
        self._list_frame.pack(padx=16, pady=(0, 4), fill="both", expand=True)

        # ── Chat divider ───────────────────────────────────────────────────
        ctk.CTkFrame(content, fg_color=("gray70", "gray24"), width=1).pack(side="left", fill="y")

        # ── Chat panel ─────────────────────────────────────────────────────
        chat = ctk.CTkFrame(content, fg_color=("gray83", "gray16"), corner_radius=0, width=340)
        chat.pack(side="left", fill="y")
        chat.pack_propagate(False)
        self._build_chat_panel(chat)

    def _build_chat_panel(self, parent):
        """Build the chat panel inside the given parent frame."""
        ctk.CTkLabel(
            parent, text="Assistant",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(anchor="w", padx=16, pady=(20, 8))

        self._chat_scroll = ctk.CTkScrollableFrame(
            parent, corner_radius=8, fg_color=("gray78", "gray18"),
        )
        self._chat_scroll.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        input_row = ctk.CTkFrame(parent, fg_color="transparent")
        input_row.pack(fill="x", padx=12, pady=(0, 14))
        input_row.columnconfigure(0, weight=1)

        self._chat_input = ctk.CTkEntry(
            input_row, placeholder_text="Ask something…",
            height=36, corner_radius=18,
            font=ctk.CTkFont(size=12),
        )
        self._chat_input.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._chat_input.bind("<Return>", lambda e: self._send_message())

        self._send_btn = ctk.CTkButton(
            input_row, text="Send",
            width=60, height=36, corner_radius=18,
            fg_color=_ACCENT, hover_color=_ACCENT_HOVER,
            font=ctk.CTkFont(size=12),
            command=self._send_message,
        )
        self._send_btn.grid(row=0, column=1)

    # ── Source toggles ─────────────────────────────────────────────────────

    def _apply_source_style(self, btn, active: bool):
        if active:
            btn.configure(fg_color=_ACCENT, hover_color=_ACCENT_HOVER,
                          border_width=0, text_color="white")
        else:
            btn.configure(fg_color="transparent", hover_color=("gray74", "gray24"),
                          border_width=1, border_color=("gray65", "gray38"),
                          text_color=("gray40", "gray60"))

    def _toggle_mic_source(self):
        self._mic_on.set(not self._mic_on.get())
        self._apply_source_style(self._mic_btn, self._mic_on.get())

    def _toggle_app_source(self):
        self._app_on.set(not self._app_on.get())
        self._apply_source_style(self._app_btn, self._app_on.get())

    # ── Level monitor ──────────────────────────────────────────────────────

    def _start_monitor(self):
        self._monitor_stop.clear()
        threading.Thread(
            target=self._recorder.monitor,
            args=(self._monitor_stop,),
            daemon=True,
        ).start()

    def _stop_monitor(self):
        self._monitor_stop.set()

    # ── Recording control ──────────────────────────────────────────────────

    def _toggle_recording(self):
        """Start or stop a recording based on the current state."""
        if self._recording:
            self._stop_event.set()
            return

        mic = self._mic_on.get()
        app = self._app_on.get()
        if not mic and not app:
            return

        self._stop_monitor()
        self._stop_event.clear()
        self._recording = True
        self._record_start = time.time()

        self._record_btn.configure(text="⏹  Stop", fg_color="#1a7a3e", hover_color="#27ae60")
        self._mic_btn.configure(state="disabled")
        self._app_btn.configure(state="disabled")
        self._status_dot.configure(text_color=_RED)
        self._status_label.configure(text="Recording", text_color=_RED)

        sources = " + ".join(filter(None, ["Mic" if mic else "", "App Audio" if app else ""]))
        self._log(f"Recording started  [{sources}]")

        # dispatch to the correct recorder based on active sources
        target = {
            (True, True):  self._recorder.run_both,
            (True, False): self._recorder.run_mic,
            (False, True): self._recorder.run_loopback,
        }[(mic, app)]
        threading.Thread(target=target, daemon=True).start()

    def _on_done(self, meta: dict | None):
        """Handle recording completion: reset UI state and kick off transcription."""
        self._recording = False
        self._record_start = None
        self._record_btn.configure(text="⏺  Record", fg_color="#c0392b", hover_color="#e74c3c")
        self._mic_btn.configure(state="normal")
        self._app_btn.configure(state="normal")
        self._status_dot.configure(text_color=("gray55", "gray45"))
        self._status_label.configure(text="Ready", text_color=("gray45", "gray55"))
        self._timer_label.configure(text=" ")
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
                # after(0) marshals the callback back onto the Tk main thread
                on_done=lambda fp, text: self.after(0, self._on_transcribed, fp, text),
            )
        else:
            self._log("Recording stopped — no audio captured")

    def _on_transcribed(self, filepath: str, text: str | None):
        """Handle transcription result and refresh the recordings list."""
        self._transcribing.discard(filepath)
        for rec in self._recordings:
            if rec["filepath"] == filepath:
                rec["has_transcript"] = text is not None
                break
        name = os.path.basename(filepath)
        if text is not None:
            preview = text[:70].replace("\n", " ")
            suffix = "…" if len(text) > 70 else ""
            self._log(f"Transcript ready  {name}  \"{preview}{suffix}\"")
        else:
            self._log(f"Transcription failed  {name}  (check OPENAI_API_KEY)")
        self._rebuild_list()

    # ── Polling ────────────────────────────────────────────────────────────

    def _poll_level(self):
        """Refresh the VU meters at 20 Hz."""
        for level, meter, label in (
            (self._mic_level, self._mic_meter, self._mic_level_label),
            (self._app_level, self._app_meter, self._app_level_label),
        ):
            meter.set(level)
            label.configure(text=f"{level * 100:.0f}%")
            color = _GREEN if level < 0.5 else (_YELLOW if level < 0.75 else _RED)
            meter.configure(progress_color=color)
        self.after(50, self._poll_level)

    def _poll_timer(self):
        """Update the recording timer label every 500 ms."""
        if self._recording and self._record_start is not None:
            elapsed = int(time.time() - self._record_start)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            time_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
            self._timer_label.configure(text=f"● {time_str}")
        self.after(500, self._poll_timer)

    # ── Recordings list ────────────────────────────────────────────────────

    def _load_existing_recordings(self):
        """Load recordings from disk/DB and populate the list."""
        self._recordings = load_metadata(FOLDER)
        self._rebuild_list()

    def _rebuild_list(self):
        """Clear and redraw the recordings list from self._recordings."""
        for widget in self._list_frame.winfo_children():
            widget.destroy()

        if not self._recordings:
            ctk.CTkLabel(
                self._list_frame,
                text="No recordings yet",
                text_color=("gray55", "gray45"),
                font=ctk.CTkFont(size=12),
            ).pack(pady=24)
            return

        self._add_list_header()
        for rec in self._recordings:
            self._add_row(rec)

    def _add_list_header(self):
        """Add a column-header row above the recordings list."""
        hdr = ctk.CTkFrame(self._list_frame, fg_color="transparent")
        hdr.pack(fill="x", padx=2, pady=(2, 0))
        hdr.columnconfigure(1, weight=1)
        hdr.columnconfigure(2, minsize=96)
        hdr.columnconfigure(3, minsize=96)

        cols = [
            (0, "",           48,  "w",      (8, 0)),
            (1, "Recording",   0,  "w",      (10, 4)),
            (2, "Project",     0,  "w",      (2, 4)),
            (3, "Meeting",     0,  "w",      (2, 4)),
            (4, "Duration",   68,  "e",      (0, 4)),
            (5, "Transcript", 36,  "center", (0, 2)),
            (6, "",           34,  "center", (0, 2)),
            (7, "",           34,  "center", (0, 6)),
        ]
        for col, text, minw, anchor, padx in cols:
            if minw:
                hdr.columnconfigure(col, minsize=minw)
            if text:
                ctk.CTkLabel(
                    hdr, text=text,
                    font=ctk.CTkFont(size=9, weight="bold"),
                    text_color=("gray50", "gray50"),
                    anchor=anchor,
                ).grid(row=0, column=col, sticky="w" if anchor == "w" else "ew",
                       padx=padx, pady=(0, 2))

    def _add_row(self, rec: dict):
        """Append a single recording row with play, transcript, and delete controls."""
        row = ctk.CTkFrame(self._list_frame, fg_color=("gray85", "gray20"), corner_radius=8)
        row.pack(fill="x", pady=3, padx=1)
        row.columnconfigure(1, weight=1)
        row.columnconfigure(2, minsize=96)
        row.columnconfigure(3, minsize=96)

        # col 0: source badge
        badge = ctk.CTkFrame(row, fg_color=("gray78", "gray28"), corner_radius=6, width=32, height=32)
        badge.grid(row=0, column=0, padx=(8, 0), pady=8, sticky="w")
        badge.pack_propagate(False)
        ctk.CTkLabel(
            badge, text=_SOURCE_ICON.get(rec["source"], "·"),
            font=ctk.CTkFont(size=12),
        ).place(relx=0.5, rely=0.5, anchor="center")

        # col 1: recording name
        ctk.CTkLabel(
            row, text=_format_name(rec["filepath"]),
            anchor="w", font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="ew", padx=(10, 4), pady=8)

        # col 2: project (inline editable)
        filepath = rec["filepath"]

        def _make_tag_saver(field, entry, fp=filepath, r=rec):
            def _save(_event=None):
                val = entry.get().strip()
                r[field] = val
                update_tag(fp, field, val)
            return _save

        proj_entry = ctk.CTkEntry(
            row, placeholder_text="Project…",
            height=28, corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=("gray78", "gray26"), border_width=0,
        )
        if rec.get("project"):
            proj_entry.insert(0, rec["project"])
        proj_entry.grid(row=0, column=2, sticky="ew", padx=(2, 4), pady=6)
        proj_saver = _make_tag_saver("project", proj_entry)
        proj_entry.bind("<FocusOut>", proj_saver)
        proj_entry.bind("<Return>", proj_saver)

        # col 3: meeting (inline editable)
        meet_entry = ctk.CTkEntry(
            row, placeholder_text="Meeting…",
            height=28, corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=("gray78", "gray26"), border_width=0,
        )
        if rec.get("meeting"):
            meet_entry.insert(0, rec["meeting"])
        meet_entry.grid(row=0, column=3, sticky="ew", padx=(2, 4), pady=6)
        meet_saver = _make_tag_saver("meeting", meet_entry)
        meet_entry.bind("<FocusOut>", meet_saver)
        meet_entry.bind("<Return>", meet_saver)

        # col 4: duration
        dur_str = _format_duration(rec["duration"]) if rec["duration"] > 0 else "—"
        ctk.CTkLabel(
            row, text=dur_str,
            anchor="e", font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray55"), width=60,
        ).grid(row=0, column=4, sticky="e", padx=(0, 4), pady=8)

        # col 5: transcript icon / spinner
        if filepath in self._transcribing:
            ctk.CTkLabel(
                row, text="···",
                font=ctk.CTkFont(size=13),
                text_color=("gray55", "gray45"), width=32,
            ).grid(row=0, column=5, padx=(0, 2), pady=8)
        elif rec.get("has_transcript", False):
            ctk.CTkButton(
                row, text="📄", width=30, height=28,
                fg_color="transparent",
                text_color=("gray50", "gray55"),
                hover_color=("gray75", "gray28"),
                font=ctk.CTkFont(size=14),
                command=lambda p=filepath: self._view_transcript(p),
            ).grid(row=0, column=5, padx=(0, 2), pady=6)
        else:
            ctk.CTkFrame(row, fg_color="transparent", width=32, height=28).grid(
                row=0, column=5, padx=(0, 2), pady=6)

        # col 6: play
        ctk.CTkButton(
            row, text="▶", width=30, height=28,
            command=lambda r=rec: self._play(r["filepath"]),
        ).grid(row=0, column=6, padx=(0, 2), pady=6)

        # col 7: delete
        ctk.CTkButton(
            row, text="✕", width=28, height=28,
            fg_color="transparent",
            text_color=("gray50", "gray55"),
            hover_color=("gray75", "gray28"),
            font=ctk.CTkFont(size=12),
            command=lambda r=rec: self._delete(r),
        ).grid(row=0, column=7, padx=(0, 6), pady=6)

    # ── Chat ───────────────────────────────────────────────────────────────

    def _send_message(self):
        """Read the chat input, append to history, and send to the agent."""
        text = self._chat_input.get().strip()
        if not text:
            return
        self._chat_input.delete(0, "end")
        self._chat_history.append({"role": "user", "content": text})
        self._add_chat_bubble(text, role="user")
        self._send_btn.configure(state="disabled", text="···")
        self._agent.send_async(
            self._chat_history,
            lambda t: self.after(0, self._on_agent_response, t),
        )

    def _add_chat_bubble(self, text: str, role: str):
        """Append a chat bubble for the given role ('user' or 'assistant')."""
        bubble_row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        bubble_row.pack(fill="x", pady=2, padx=4)
        if role == "user":
            ctk.CTkLabel(
                bubble_row, text=text,
                fg_color=_ACCENT, corner_radius=10,
                wraplength=220, justify="right", anchor="e",
                padx=10, pady=6, font=ctk.CTkFont(size=12),
            ).pack(side="right")
        else:
            ctk.CTkLabel(
                bubble_row, text=text,
                fg_color=("#d1d5db", "#374151"), corner_radius=10,
                wraplength=220, justify="left", anchor="w",
                padx=10, pady=6, font=ctk.CTkFont(size=12),
            ).pack(side="left")
        self.after(10, lambda: self._chat_scroll._parent_canvas.yview_moveto(1.0))

    def _on_agent_response(self, text: str | None):
        """Handle the agent's reply and display it as a chat bubble."""
        self._send_btn.configure(state="normal", text="Send")
        if text is None:
            text = "Error: could not reach assistant."
        else:
            self._chat_history.append({"role": "assistant", "content": text})
        self._add_chat_bubble(text, role="assistant")

    # ── Actions ────────────────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        """Append a timestamped message to the log box."""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"[{ts}]  {message}\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        """Clear all entries from the log box."""
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _delete(self, rec: dict):
        """Delete a recording's WAV file, DB row, and remove it from the list."""
        delete_metadata(rec["filepath"])
        try:
            os.remove(rec["filepath"])
        except OSError as exc:
            self._log(f"Warning: could not delete file {os.path.basename(rec['filepath'])}: {exc}")
        self._transcribing.discard(rec["filepath"])
        if rec in self._recordings:
            self._recordings.remove(rec)
        self._log(f"Deleted  {os.path.basename(rec['filepath'])}")
        self._rebuild_list()

    def _play(self, filepath: str):
        """Play a WAV file in a background thread using Windows SoundPlayer."""
        # PlaySync blocks until done; run in a thread so the UI stays responsive
        threading.Thread(
            target=lambda: subprocess.run(
                ["powershell", "-c",
                 f'(New-Object System.Media.SoundPlayer "{filepath}").PlaySync()'],
                check=False,
            ),
            daemon=True,
        ).start()

    def _view_transcript(self, filepath: str):
        """Open a modal window showing the full transcript for a recording."""
        text = get_transcript(filepath)
        if text is None:
            return

        win = ctk.CTkToplevel(self)
        win.title(_format_name(filepath))
        win.geometry("520x400")
        win.grab_set()  # modal — blocks interaction with the main window

        textbox = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(size=13))
        textbox.pack(fill="both", expand=True, padx=16, pady=16)
        textbox.insert("1.0", text)
        textbox.configure(state="disabled")
