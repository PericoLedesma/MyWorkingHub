"""FastAPI web server for SmartRecorder."""
import asyncio
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

from src import db
from src.recorder import Recorder, list_input_devices, FOLDER
from src.transcriber import Transcriber
from src.live_transcriber import LiveTranscriber
from src.agents.agent_orchestrator import ChatAgent
from src.agents.agent_summarization import SummarizationAgent
from src.web import state

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# ── Singletons ───────────────────────────────────────────────────────────────

_transcriber = Transcriber()
_live_transcriber = LiveTranscriber()
_chat_agent = ChatAgent()
_sum_agent = SummarizationAgent()


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

    # Restart level monitor
    _start_monitor()

    if meta:
        await state.broadcast({"type": "recording_stopped", "meta": _rec_to_dict(meta)})
        rid = meta["id"]
        fp = meta["filepath"]
        state.transcribing.add(rid)
        await state.broadcast({"type": "job_started", "job": "transcribe", "id": rid})
        _transcriber.transcribe_async(rid, fp, _transcribe_done_cb)
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
        if text:
            state.summarizing.add(recording_id)
            await state.broadcast({"type": "job_started", "job": "summarize", "id": recording_id})
            _sum_agent.summarize_async(recording_id, text, language, _summarize_done_cb)

    asyncio.run_coroutine_threadsafe(_cb(), state.loop)


def _summarize_done_cb(recording_id: int, summary: str | None) -> None:
    async def _cb():
        state.summarizing.discard(recording_id)
        await state.broadcast({
            "type": "summarized",
            "id": recording_id,
            "ok": summary is not None,
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
    threading.Thread(target=r.monitor, args=(ev,), daemon=True).start()


def _stop_monitor() -> None:
    if state.monitor_stop:
        state.monitor_stop.set()
        state.monitor_stop = None


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

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _stop_monitor()
    if state.stop_event:
        state.stop_event.set()


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
        "summary": db.get_summary(rid),
        "language": db.get_language(rid),
        "speaker": db.get_transcript_speaker(rid),
    }


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


@app.post("/api/recordings/{rid}/summarize")
async def summarize_recording(rid: int):
    text = db.get_transcript(rid)
    if not text:
        raise HTTPException(400, "no transcript available")
    if rid in state.summarizing:
        return {"ok": False, "reason": "already_running"}
    state.summarizing.add(rid)
    language = db.get_language(rid) or "unknown"
    await state.broadcast({"type": "job_started", "job": "summarize", "id": rid})
    _sum_agent.summarize_async(rid, text, language, _summarize_done_cb)
    return {"ok": True}


# ── REST: devices ─────────────────────────────────────────────────────────────

@app.get("/api/devices")
def get_devices():
    try:
        return [{"index": i, "name": n} for i, n in list_input_devices()]
    except Exception as e:
        log.warning("Failed to list devices: %s", e)
        return []


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
    threading.Thread(target=fn, daemon=True).start()

    def _on_live_segment(timestamp: str, text: str) -> None:
        asyncio.run_coroutine_threadsafe(
            state.broadcast({"type": "live_transcript", "timestamp": timestamp, "text": text}),
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
            "summarizing": list(state.summarizing),
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


# ── Static files — MUST be last ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
