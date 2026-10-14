"""FastAPI web server for My Working Hub."""
import asyncio
import datetime
import json
import logging
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import db, focus_db
from src.recorder import Recorder, list_input_devices, FOLDER
from src.transcriber import Transcriber
from src.live_transcriber import LiveTranscriber
from src.agents.agent_notes import NotesAgent
from src.jiggler import MouseJiggler
from src.web import state

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ── Singletons ───────────────────────────────────────────────────────────────

_transcriber = Transcriber()
_live_transcriber = LiveTranscriber()
_notes_agent = NotesAgent()
_jiggler = MouseJiggler()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rec_to_dict(r: dict) -> dict:
    """Make a DB recording dict JSON-serialisable (convert datetime fields)."""
    return {
        **r,
        "start_time": r["start_time"].isoformat()
            if hasattr(r["start_time"], "isoformat") else str(r["start_time"]),
        "end_time": r["end_time"].isoformat()
            if hasattr(r["end_time"], "isoformat") else str(r["end_time"]),
    }


# ── Recording lifecycle callbacks ────────────────────────────────────────────

async def _on_recording_done(meta: dict | None, reason: str | None) -> None:
    """Runs on the event loop after the recording thread finishes."""
    state.recording = False
    state.recording_start = None
    state.recording_thread = None

    if meta and state.live_segments:
        segs = state.live_segments[:]
        live_tx = "\n".join(
            f"{ts} {'[' + sp + '] ' if sp else ''}{text}".strip()
            for ts, text, sp in segs
        )
        try:
            db.save_transcript_speaker(meta["id"], live_tx)
        except Exception:
            log.exception("Failed to save live transcript for recording %s", meta["id"])
    # Apply the project / meeting labels chosen before or during the recording.
    # Own try/except: a tag failure must never stop the broadcast below.
    if meta:
        for field in ("project", "meeting"):
            value = (state.pending_project if field == "project" else state.pending_meeting).strip()
            if not value:
                continue
            try:
                db.update_tag(meta["id"], field, value)
                meta[field] = value
            except Exception:
                log.exception("Failed to save %s=%r for recording %s", field, value, meta["id"])
    state.pending_project = ""
    state.pending_meeting = ""

    if meta:
        await state.broadcast({"type": "recording_stopped", "meta": _rec_to_dict(meta)})
    else:
        await state.broadcast({"type": "recording_stopped", "meta": None, "reason": reason})


def _transcribe_done_cb(recording_id: int, text: str | None, language: str, error: str | None = None, warning: str | None = None) -> None:
    """Called from transcription thread. Schedules async work on the event loop."""
    async def _cb():
        state.transcribing.discard(recording_id)
        payload = {"type": "transcribed", "id": recording_id, "ok": text is not None, "language": language}
        if error:
            payload["error"] = error
        if warning:
            payload["warning"] = warning
        await state.broadcast(payload)
    asyncio.run_coroutine_threadsafe(_cb(), state.loop)


def _notes_done_cb(recording_id: int, notes: str | None) -> None:
    async def _cb():
        state.notes_generating.discard(recording_id)
        await state.broadcast({
            "type": "notes_generated",
            "id": recording_id,
            "ok": notes is not None,
        })

    asyncio.run_coroutine_threadsafe(_cb(), state.loop)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.init(asyncio.get_running_loop())

    sess = focus_db.start_work_session()
    state.focus_session_id = sess["id"]
    state.focus_session_start = sess["start_time"]
    focus_db.record_app_start()

    yield

    if state.stop_event:
        state.stop_event.set()
    if state.recording_thread and state.recording_thread.is_alive():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: state.recording_thread.join(timeout=30))
    if state.focus_session_id is not None:
        focus_db.end_work_session(state.focus_session_id)
        state.focus_session_id = None


app = FastAPI(lifespan=lifespan)


# ── REST: recordings ─────────────────────────────────────────────────────────

@app.get("/api/recordings")
def get_recordings():
    return [_rec_to_dict(r) for r in db.load_metadata(FOLDER)]


@app.post("/api/recordings/scan")
def scan_recordings():
    return [_rec_to_dict(r) for r in db.load_metadata(FOLDER)]


class TagUpdate(BaseModel):
    field: str
    value: str


@app.patch("/api/recordings/{rid}/tags")
def update_tags(rid: int, body: TagUpdate):
    if body.field not in ("project", "meeting"):
        raise HTTPException(400, "field must be 'project' or 'meeting'")
    db.update_tag(rid, body.field, body.value)
    return {"ok": True}


class ProjectRename(BaseModel):
    old_name: str
    new_name: str


@app.post("/api/projects/rename")
def rename_project(body: ProjectRename):
    if not body.old_name:
        raise HTTPException(400, "old_name is required")
    count = db.rename_project(body.old_name, body.new_name.strip())
    return {"ok": True, "updated": count}


@app.delete("/api/recordings/{rid}")
def delete_recording(rid: int):
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    if rec and not rec["file_missing"] and os.path.exists(rec["filepath"]):
        try:
            os.remove(rec["filepath"])
        except OSError as e:
            log.warning("Could not delete audio file %s: %s", rec["filepath"], e)
    db.delete_metadata(rid)
    return {"ok": True}


@app.get("/api/recordings/{rid}/transcript")
def get_transcript(rid: int):
    return {
        "raw": db.get_transcript_raw(rid),
        "language": db.get_language(rid),
        "notes": db.get_notes(rid),
        "user_notes": db.get_user_notes(rid),
    }


class NotesUpdate(BaseModel):
    text: str


@app.patch("/api/recordings/{rid}/notes")
def save_notes_endpoint(rid: int, body: NotesUpdate):
    db.save_notes(rid, body.text)
    return {"ok": True}


@app.patch("/api/recordings/{rid}/user-notes")
def save_user_notes_endpoint(rid: int, body: NotesUpdate):
    db.save_user_notes(rid, body.text)
    return {"ok": True}


@app.post("/api/recordings/{rid}/generate-notes")
async def generate_notes(rid: int):
    text = db.get_transcript(rid)
    if not text:
        raise HTTPException(400, "no transcript available")
    if rid in state.notes_generating:
        return {"ok": False, "reason": "already_running"}
    state.notes_generating.add(rid)
    language = db.get_language(rid) or "unknown"
    await state.broadcast({"type": "job_started", "job": "notes", "id": rid})
    _notes_agent.generate_notes_async(rid, text, language, _notes_done_cb)
    return {"ok": True}


@app.post("/api/recordings/{rid}/transcribe")
async def transcribe_recording(rid: int):
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    if not rec:
        raise HTTPException(404, "recording not found")
    if rid in state.transcribing:
        return {"ok": False, "reason": "already_running"}
    state.transcribing.add(rid)
    await state.broadcast({"type": "job_started", "job": "transcribe", "id": rid})
    _transcriber.transcribe_async(rid, rec["filepath"], _transcribe_done_cb)
    return {"ok": True}


# ── REST: devices ─────────────────────────────────────────────────────────────

@app.get("/api/devices")
def get_devices():
    try:
        return [{"index": i, "name": n} for i, n in list_input_devices()]
    except Exception as e:
        log.warning("Failed to list devices: %s", e)
        return []


@app.get("/api/debug/audio-devices")
def debug_audio_devices():
    """Return all PyAudio devices + loopback devices for diagnosing capture issues."""
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    try:
        all_devices = []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            all_devices.append({
                "index": i,
                "name": d["name"],
                "maxInputChannels": d["maxInputChannels"],
                "maxOutputChannels": d["maxOutputChannels"],
                "defaultSampleRate": d["defaultSampleRate"],
                "hostApi": d["hostApi"],
            })
        loopbacks = []
        try:
            for d in pa.get_loopback_device_info_generator():
                loopbacks.append({
                    "index": d["index"],
                    "name": d["name"],
                    "channels": d["maxInputChannels"],
                    "sampleRate": d["defaultSampleRate"],
                })
        except Exception as e:
            loopbacks = [{"error": str(e)}]
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out_idx = wasapi["defaultOutputDevice"]
            default_out = pa.get_device_info_by_index(default_out_idx)
            default_comm_idx = wasapi.get("defaultInputDevice")
        except Exception:
            default_out = None
            default_comm_idx = None
        return {
            "allDevices": all_devices,
            "loopbackDevices": loopbacks,
            "defaultOutputDevice": default_out,
            "defaultCommunicationsDeviceIndex": default_comm_idx,
        }
    finally:
        pa.terminate()


# ── REST: record controls ────────────────────────────────────────────────────

class RecordStart(BaseModel):
    source: str          # "mic" | "app" | "both"
    device_index: int | None = None
    project: str = ""
    meeting: str = ""


@app.post("/api/record/start")
async def record_start(body: RecordStart):
    if state.recording:
        return {"ok": False, "reason": "already_recording"}

    stop_ev = threading.Event()
    state.stop_event = stop_ev
    state.recording = True
    state.recording_start = time.time()
    state.pending_project = (body.project or "").strip()
    state.pending_meeting = (body.meeting or "").strip()

    rec = Recorder(
        stop_event=stop_ev,
        on_done=lambda meta, reason: asyncio.run_coroutine_threadsafe(
            _on_recording_done(meta, reason), state.loop
        ),
    )
    if body.device_index is not None:
        rec.mic_device_index = body.device_index

    fn = {"mic": rec.run_mic, "app": rec.run_loopback, "both": rec.run_both}.get(
        body.source, rec.run_mic
    )
    t = threading.Thread(target=fn, daemon=True)
    state.recording_thread = t
    t.start()

    state.live_segments = []

    def _on_live_segment(timestamp: str, text: str, speaker: str = "") -> None:
        state.live_segments.append((timestamp, text, speaker))
        asyncio.run_coroutine_threadsafe(
            state.broadcast({"type": "live_transcript", "timestamp": timestamp, "text": text, "speaker": speaker}),
            state.loop,
        )

    _live_transcriber.start(stop_ev, rec.drain_live_chunks, _on_live_segment)

    await state.broadcast({
        "type": "recording_started",
        "source": body.source,
        "start_time": state.recording_start,
        "project": state.pending_project,
        "meeting": state.pending_meeting,
    })
    return {"ok": True}


class PendingTags(BaseModel):
    project: str | None = None
    meeting: str | None = None


@app.patch("/api/recording/tags")
async def update_pending_tags(body: PendingTags):
    """Set the project / meeting labels for the in-progress recording.

    Lets the user pick or change the meeting *while* recording; the labels are
    written to the DB when the recording stops.
    """
    if not state.recording:
        return {"ok": False, "reason": "not_recording"}
    if body.project is not None:
        state.pending_project = body.project.strip()
    if body.meeting is not None:
        state.pending_meeting = body.meeting.strip()
    await state.broadcast({
        "type": "recording_tags",
        "project": state.pending_project,
        "meeting": state.pending_meeting,
    })
    return {"ok": True, "project": state.pending_project, "meeting": state.pending_meeting}


@app.post("/api/record/stop")
async def record_stop():
    if not state.recording:
        return {"ok": False, "reason": "not_recording"}
    if state.stop_event:
        state.stop_event.set()
    return {"ok": True}


@app.post("/api/shutdown")
async def shutdown():
    """Stop any active recording, wait for it to be written to disk, then return.

    Electron calls this before hard-killing the Python process so the WAV file
    and DB row are guaranteed to be flushed before the process exits.
    """
    if state.stop_event:
        state.stop_event.set()
    if state.recording_thread and state.recording_thread.is_alive():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: state.recording_thread.join(timeout=30))
    return {"ok": True}


@app.get("/api/recording/status")
def recording_status():
    return {
        "recording": state.recording,
        "recording_start": state.recording_start,
        "project": state.pending_project,
        "meeting": state.pending_meeting,
    }


# ── REST: import ─────────────────────────────────────────────────────────────

@app.post("/api/import")
async def import_recording(file: UploadFile, background_tasks: BackgroundTasks):
    import shutil, tempfile, datetime
    import soundfile as sf

    suffix = Path(file.filename or "audio.wav").suffix.lower()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    def _do_import():
        stem = Path(file.filename or "import").stem
        dest = os.path.join(FOLDER, f"import_{stem}_{int(time.time())}.wav")
        try:
            if suffix != ".wav":
                import shutil as _shutil
                import winreg as _winreg

                def _fresh_path() -> str:
                    """Read PATH from registry so it works even if the process inherited a stale env."""
                    parts = []
                    for hive, sub in [
                        (_winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                        (_winreg.HKEY_CURRENT_USER, r"Environment"),
                    ]:
                        try:
                            with _winreg.OpenKey(hive, sub) as k:
                                parts.append(_winreg.QueryValueEx(k, "Path")[0])
                        except FileNotFoundError:
                            pass
                    return os.pathsep.join(parts)

                env = os.environ.copy()
                env["PATH"] = _fresh_path()

                ffmpeg_exe = _shutil.which("ffmpeg", path=env["PATH"])
                if not ffmpeg_exe:
                    raise RuntimeError(
                        "ffmpeg is not installed or not in PATH. "
                        "Install ffmpeg (https://ffmpeg.org/download.html) to import non-WAV files."
                    )
                subprocess.run(
                    [ffmpeg_exe, "-y", "-i", tmp_path, dest],
                    check=True, capture_output=True, env=env
                )
            else:
                shutil.copy(tmp_path, dest)

            try:
                info = sf.info(dest)
                rate, channels, dur = int(info.samplerate), info.channels, info.duration
            except Exception:
                rate, channels, dur = 0, 0, 0.0

            mtime = os.path.getmtime(dest)
            st = datetime.datetime.fromtimestamp(mtime)
            et = datetime.datetime.fromtimestamp(mtime + dur)
            meta = db.save_metadata(dest, "import", st, et, rate, channels)
            asyncio.run_coroutine_threadsafe(
                state.broadcast({"type": "imported", "meta": _rec_to_dict(meta)}),
                state.loop,
            )
        except Exception as e:
            log.exception("Import failed")
            asyncio.run_coroutine_threadsafe(
                state.broadcast({"type": "import_error", "reason": str(e)}),
                state.loop,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    background_tasks.add_task(_do_import)
    return {"ok": True}


# ── REST: open folder ─────────────────────────────────────────────────────────

@app.get("/api/open_folder/{rid}")
def open_folder(rid: int):
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    folder = os.path.dirname(rec["filepath"]) if rec else FOLDER
    try:
        subprocess.Popen(["explorer", folder])
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


# ── Audio serving ─────────────────────────────────────────────────────────────

@app.get("/audio/{rid}")
def serve_audio(rid: int):
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    if not rec or rec["file_missing"]:
        raise HTTPException(404, "audio file not found")
    return FileResponse(rec["filepath"], media_type="audio/wav")


# ── Waveform peaks & crop ────────────────────────────────────────────────────

@app.get("/api/recordings/{rid}/peaks")
def get_recording_peaks(rid: int, n: int = 600):
    import soundfile as sf
    import numpy as np
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    if not rec or rec["file_missing"]:
        raise HTTPException(404, "audio file not found")
    try:
        data, sr = sf.read(rec["filepath"], dtype="float32")
    except Exception as e:
        raise HTTPException(500, f"failed to read audio: {e}")
    if data.ndim > 1:
        data = data.mean(axis=1)
    total = len(data)
    bucket = max(1, total // n)
    peaks = []
    for i in range(n):
        s = i * bucket
        e = min(total, s + bucket)
        if s >= total:
            break
        peaks.append(float(np.max(np.abs(data[s:e]))))
    has_backup = os.path.exists(rec["filepath"] + ".bak")
    return {"peaks": peaks, "duration": float(total / sr), "sample_rate": int(sr), "has_backup": has_backup}


@app.post("/api/recordings/{rid}/crop")
async def crop_recording(rid: int, request: Request):
    import soundfile as sf
    body = await request.json()
    start_sec = float(body.get("start", 0))
    end_sec = body.get("end")
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    if not rec:
        raise HTTPException(404, "recording not found")
    if rec.get("file_missing"):
        raise HTTPException(404, "audio file not found")
    filepath = rec["filepath"]
    try:
        data, sr = sf.read(filepath)
    except Exception as e:
        raise HTTPException(500, f"failed to read audio: {e}")
    total_frames = len(data) if data.ndim == 1 else data.shape[0]
    start_frame = max(0, int(start_sec * sr))
    end_frame = min(total_frames, int(float(end_sec) * sr) if end_sec is not None else total_frames)
    if start_frame >= end_frame:
        raise HTTPException(400, "invalid crop range")
    cropped = data[start_frame:end_frame] if data.ndim == 1 else data[start_frame:end_frame, :]
    backup_path = filepath + ".bak"
    if not os.path.exists(backup_path):
        import shutil
        shutil.copy2(filepath, backup_path)
    try:
        sf.write(filepath, cropped, sr)
    except Exception as e:
        raise HTTPException(500, f"failed to write audio: {e}")
    new_duration = (end_frame - start_frame) / sr
    db.update_recording_duration(rid, new_duration)
    return {"ok": True, "duration": new_duration, "has_backup": True}


@app.post("/api/recordings/{rid}/restore")
def restore_recording(rid: int):
    import soundfile as sf
    import shutil
    recs = db.load_metadata(FOLDER)
    rec = next((r for r in recs if r["id"] == rid), None)
    if not rec:
        raise HTTPException(404, "recording not found")
    filepath = rec["filepath"]
    backup_path = filepath + ".bak"
    if not os.path.exists(backup_path):
        raise HTTPException(404, "no backup found")
    shutil.move(backup_path, filepath)
    try:
        data, sr = sf.read(filepath)
        total = len(data) if data.ndim == 1 else data.shape[0]
        duration = total / sr
    except Exception:
        duration = rec["duration"]
    db.update_recording_duration(rid, duration)
    return {"ok": True, "duration": duration}


# ── REST: work sessions (Focus Time) ─────────────────────────────────────────

@app.get("/api/work-sessions/current")
def get_current_work_session():
    if state.focus_session_id is None:
        return None
    return {"id": state.focus_session_id, "start_time": state.focus_session_start}


@app.post("/api/work-sessions")
def create_work_session():
    sess = focus_db.start_work_session()
    state.focus_session_id = sess["id"]
    state.focus_session_start = sess["start_time"]
    return sess


@app.patch("/api/work-sessions/{session_id}/end")
def end_work_session(session_id: int):
    focus_db.end_work_session(session_id)
    if state.focus_session_id == session_id:
        state.focus_session_id = None
    return {"ok": True}


@app.get("/api/work-sessions")
def get_work_sessions(date: str | None = None):
    return focus_db.list_work_sessions(date)


@app.delete("/api/work-sessions")
def delete_work_sessions(date: str):
    count = focus_db.delete_sessions_by_date(date)
    return {"deleted": count}


@app.get("/api/app-events")
def get_app_events(date: str | None = None):
    today = date or datetime.date.today().isoformat()
    return focus_db.get_app_events(today)


# ── REST: jiggler ────────────────────────────────────────────────────────────

@app.post("/api/jiggler/start")
def jiggler_start():
    def _auto_stop():
        asyncio.run_coroutine_threadsafe(
            state.broadcast({"type": "jiggler_stopped", "reason": "user_input"}),
            state.loop,
        )
    _jiggler.start(interval=3, on_auto_stop=_auto_stop)
    return {"ok": True, "running": True}


@app.post("/api/jiggler/stop")
def jiggler_stop():
    _jiggler.stop()
    return {"ok": True, "running": False}


@app.get("/api/jiggler/status")
def jiggler_status():
    return {"running": _jiggler.running}


# ── WebSocket: /ws/app ────────────────────────────────────────────────────────

@app.websocket("/ws/app")
async def ws_app(ws: WebSocket):
    await ws.accept()
    state.add_ws(ws)
    log.info("ws_app: CONNECTED  — active_watchers=%d", state.count_watchers())
    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "recording": state.recording,
            "recording_start": state.recording_start,
            "project": state.pending_project,
            "meeting": state.pending_meeting,
            "transcribing": list(state.transcribing),
            "notes_generating": list(state.notes_generating),
        }))
        while True:
            await ws.receive_text()   # keep connection alive; absorb any client pings
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        state.remove_ws(ws)
        log.info("ws_app: DISCONNECTED — active_watchers=%d", state.count_watchers())


# ── REST: Outlook calendar ────────────────────────────────────────────────────

@app.get("/api/calendar/events")
async def calendar_events(days: int = 1, days_back: int = 0):
    import asyncio
    from src.outlook import get_events
    try:
        events = await asyncio.get_event_loop().run_in_executor(
            None, lambda: get_events(days_ahead=days, days_back=days_back)
        )
        return {"ok": True, "events": events}
    except Exception as e:
        log.warning("Calendar fetch failed: %s", e)
        err = str(e)
        if "Outlook.Application" in err or "RPC" in err or "com_error" in type(e).__name__:
            err = "Could not connect to Outlook. Make sure Outlook (classic) is open and try again."
        return {"ok": False, "error": err, "events": []}


def _match_meeting(events: list[dict], label: str, rec_start: datetime.datetime) -> dict | None:
    """Pick the calendar event a recording's meeting label refers to.

    The DB only stores the subject text, so the event is recovered by subject and
    by how close it sits to the moment the recording started.
    """
    want = label.strip().casefold()

    def _dist(ev: dict) -> float:
        try:
            ev_start = datetime.datetime.fromisoformat(ev["start"])
        except Exception:
            return float("inf")
        return abs((ev_start - rec_start).total_seconds())

    exact = [e for e in events if (e.get("subject") or "").strip().casefold() == want]
    if exact:
        return min(exact, key=_dist)

    partial = [
        e for e in events
        if want and (want in (e.get("subject") or "").casefold()
                     or (e.get("subject") or "").strip().casefold() in want)
    ]
    if partial:
        return min(partial, key=_dist)
    return None


def _recording_span(rid: int) -> tuple[dict, datetime.datetime, datetime.datetime]:
    """Return (recording, start, end) or raise 404."""
    rec = next((r for r in db.load_metadata(FOLDER) if r["id"] == rid), None)
    if rec is None:
        raise HTTPException(404, "recording not found")

    def _dt(value) -> datetime.datetime:
        return value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value))

    return rec, _dt(rec["start_time"]), _dt(rec["end_time"])


@app.get("/api/recordings/{rid}/meeting-candidates")
async def recording_meeting_candidates(rid: int):
    """The Outlook meetings on the day a recording was made, for the card picker.

    `overlap` marks the events whose time range intersects the recording, which
    is almost always the one the user wants.
    """
    import asyncio
    from src.outlook import get_events_between

    rec, rec_start, rec_end = _recording_span(rid)
    day = rec_start.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        events = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_events_between(day, day + datetime.timedelta(days=1), body_chars=0),
        )
    except Exception as e:
        log.warning("Meeting candidates lookup failed for recording %s: %s", rid, e)
        return {"ok": False, "reason": "outlook_unavailable", "events": [],
                "error": "Could not connect to Outlook. Make sure Outlook (classic) is open."}

    out = []
    for ev in events:
        try:
            ev_start = datetime.datetime.fromisoformat(ev["start"])
            ev_end   = datetime.datetime.fromisoformat(ev["end"])
        except Exception:
            continue
        out.append({
            "subject":   ev.get("subject", ""),
            "start":     ev["start"],
            "end":       ev["end"],
            "location":  ev.get("location", ""),
            "organizer": ev.get("organizer", ""),
            "overlap":   ev_start < rec_end and rec_start < ev_end,
        })
    out.sort(key=lambda e: e["start"])
    return {"ok": True, "date": day.strftime("%Y-%m-%d"),
            "current": (rec.get("meeting") or "").strip(), "events": out}


@app.get("/api/recordings/{rid}/meeting")
async def recording_meeting(rid: int):
    """Outlook details for the meeting a recording is linked to.

    Matching is best-effort: the recording stores only the meeting's subject, so
    the event is found by subject within a +/- 1 day window around the recording.
    """
    import asyncio
    from src.outlook import get_events_between

    rec, rec_start, _ = _recording_span(rid)

    label = (rec.get("meeting") or "").strip()
    if not label:
        return {"ok": False, "reason": "no_meeting"}

    day = rec_start.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        events = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_events_between(
                day - datetime.timedelta(days=1),
                day + datetime.timedelta(days=2),
                body_chars=4000,
            ),
        )
    except Exception as e:
        log.warning("Meeting lookup failed for recording %s: %s", rid, e)
        return {"ok": False, "reason": "outlook_unavailable", "meeting": label,
                "error": "Could not connect to Outlook. Make sure Outlook (classic) is open."}

    event = _match_meeting(events, label, rec_start)
    if event is None:
        return {"ok": False, "reason": "no_match", "meeting": label}
    return {"ok": True, "meeting": label, "event": event}


# ── REST: Diario ─────────────────────────────────────────────────────────────

class DiarioBody(BaseModel):
    state: dict
    date: str = ""


def _diario_date(date: str) -> str:
    """Validate a YYYY-MM-DD date, defaulting to today when empty."""
    if not date:
        return datetime.date.today().isoformat()
    try:
        return datetime.date.fromisoformat(date).isoformat()
    except ValueError:
        raise HTTPException(400, f"Invalid date '{date}'. Use YYYY-MM-DD.")


@app.get("/api/diario")
def get_diario_endpoint(date: str = ""):
    return {"ok": True, "state": db.get_diario(_diario_date(date))}


@app.put("/api/diario")
def save_diario_endpoint(body: DiarioBody):
    db.save_diario(_diario_date(body.date), body.state)
    return {"ok": True}


# ── REST: OneNote ─────────────────────────────────────────────────────────────

@app.post("/api/onenote/test")
async def onenote_test():
    from src.onenote import create_page
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: create_page("Hello World", "Hello from My Working Hub!")
    )
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return result


@app.get("/api/onenote/hierarchy")
async def onenote_hierarchy():
    from src.onenote import get_hierarchy
    result = await asyncio.get_event_loop().run_in_executor(None, get_hierarchy)
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return result


# ── Static files — MUST be last ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
