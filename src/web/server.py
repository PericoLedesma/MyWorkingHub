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

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import db, focus_db
from src.recorder import Recorder, list_input_devices, FOLDER
from src.transcriber import Transcriber
from src.live_transcriber import LiveTranscriber
from src.agents.agent_orchestrator import ChatAgent
from src.agents.agent_notes import NotesAgent
from src.jiggler import MouseJiggler
from src.web import state

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ── Singletons ───────────────────────────────────────────────────────────────

_transcriber = Transcriber()
_live_transcriber = LiveTranscriber()
_chat_agent = ChatAgent()
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
    state.live_segments = []

    # Always restart monitor and broadcast even if DB work above failed —
    # otherwise the UI stays stuck showing "Recording…" with no way to recover.
    _start_monitor()

    if meta:
        await state.broadcast({"type": "recording_stopped", "meta": _rec_to_dict(meta)})
    else:
        await state.broadcast({"type": "recording_stopped", "meta": None, "reason": reason})


def _transcribe_done_cb(recording_id: int, text: str | None, language: str) -> None:
    """Called from transcription thread. Schedules async work on the event loop."""
    async def _cb():
        state.transcribing.discard(recording_id)
        await state.broadcast({
            "type": "transcribed",
            "id": recording_id,
            "ok": text is not None,
            "language": language,
        })
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


def _start_monitor() -> None:
    """Start a level-only monitor (no recording) in a daemon thread."""
    if state.monitor_stop:
        return  # already running
    ev = threading.Event()
    state.monitor_stop = ev
    r = Recorder(
        stop_event=ev,
        set_mic_level=state.set_mic_level,
        set_app_level=state.set_app_level,
        on_done=lambda *_: None,
    )
    t = threading.Thread(target=r.monitor, args=(ev,), daemon=True)
    state.monitor_thread = t
    t.start()


def _stop_monitor() -> None:
    if state.monitor_stop:
        state.monitor_stop.set()
        state.monitor_stop = None
    if state.monitor_thread:
        # Must join before returning: monitor holds WASAPI loopback handles, and recording
        # opening the same device while the monitor still owns it produces a dead stream
        # (audio bar goes silent). See Recording Integrity Rule 6 in CLAUDE.md.
        state.monitor_thread.join(timeout=2)
        state.monitor_thread = None


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.init(asyncio.get_running_loop())

    async def _level_broadcaster():
        while True:
            try:
                msg = await asyncio.wait_for(state.level_queue.get(), timeout=1.0)
                await state.broadcast(msg)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.05)

    task = asyncio.create_task(_level_broadcaster())
    _start_monitor()

    sess = focus_db.start_work_session()
    state.focus_session_id = sess["id"]
    state.focus_session_start = sess["start_time"]
    focus_db.record_app_start()

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _stop_monitor()
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
        "corrected": db.get_transcript(rid),
        "raw": db.get_transcript_raw(rid),
        "language": db.get_language(rid),
        "speaker": db.get_transcript_speaker(rid),
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


@app.post("/api/record/start")
async def record_start(body: RecordStart):
    if state.recording:
        return {"ok": False, "reason": "already_recording"}

    _stop_monitor()

    stop_ev = threading.Event()
    state.stop_event = stop_ev
    state.recording = True
    state.recording_start = time.time()

    rec = Recorder(
        stop_event=stop_ev,
        set_mic_level=state.set_mic_level,
        set_app_level=state.set_app_level,
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
    })
    return {"ok": True}


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
    try:
        await ws.send_text(json.dumps({
            "type": "init",
            "recording": state.recording,
            "recording_start": state.recording_start,
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


# ── WebSocket: /ws/chat ───────────────────────────────────────────────────────

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            user_text = (data.get("text") or "").strip()
            if not user_text:
                continue

            state.chat_history.append({"role": "user", "content": user_text})

            messages = [
                {"role": "system", "content": _chat_agent.system_prompt},
                *state.chat_history,
            ]
            tool_handlers = {"buscar_grabaciones": lambda c: db.search_transcripts(c)}

            token_q: asyncio.Queue = asyncio.Queue()
            ev_loop = state.loop

            def on_token(text: str):
                ev_loop.call_soon_threadsafe(
                    token_q.put_nowait, {"type": "token", "text": text}
                )

            def on_done(final: str | None):
                ev_loop.call_soon_threadsafe(
                    token_q.put_nowait, {"type": "done", "text": final or ""}
                )

            _chat_agent.run_stream_async(messages, tool_handlers, on_token, on_done)

            # Stream tokens to the browser and collect full response
            full: list[str] = []
            while True:
                msg = await token_q.get()
                await ws.send_text(json.dumps(msg))
                if msg["type"] == "token":
                    full.append(msg["text"])
                elif msg["type"] == "done":
                    if not full and msg["text"]:
                        full.append(msg["text"])
                    break

            state.chat_history.append({"role": "assistant", "content": "".join(full)})

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Chat WebSocket error")


# ── REST: Outlook calendar ────────────────────────────────────────────────────

@app.get("/api/calendar/events")
async def calendar_events(days: int = 1):
    import asyncio
    from src.outlook import get_events
    try:
        events = await asyncio.get_event_loop().run_in_executor(
            None, lambda: get_events(days_ahead=days)
        )
        return {"ok": True, "events": events}
    except Exception as e:
        log.warning("Calendar fetch failed: %s", e)
        return {"ok": False, "error": str(e), "events": []}


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
