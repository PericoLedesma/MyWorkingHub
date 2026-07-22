"""Shared in-process state for the FastAPI web server."""
import asyncio
import threading

# asyncio event loop — set at startup via init()
loop: asyncio.AbstractEventLoop | None = None

# Active /ws/app WebSocket connections
active_ws: set = set()
_ws_lock = threading.Lock()

# Level data queue: pyaudio callbacks → _level_broadcaster task
level_queue: asyncio.Queue | None = None

# Current audio levels (written from pyaudio threads)
_cur_levels = {"mic": 0.0, "app": 0.0}
_levels_lock = threading.Lock()

# Recording state
recording: bool = False
recording_start: float | None = None   # time.time() value when recording started
stop_event: threading.Event | None = None
recording_thread: threading.Thread | None = None

# Monitor state (level-only streams, no saving)
monitor_stop: threading.Event | None = None
monitor_thread: threading.Thread | None = None

# In-progress job id sets (all mutations must happen on the event loop)
transcribing: set[int] = set()
notes_generating: set[int] = set()

# Chat conversation history (user + assistant turns, no system message)
chat_history: list[dict] = []

# Server-managed focus session (started on launch, ended on shutdown)
focus_session_id: int | None = None
focus_session_start: str | None = None

# Accumulated live transcript segments for the current recording
# Each entry: (timestamp, text, speaker)
live_segments: list[tuple[str, str, str]] = []


def init(event_loop: asyncio.AbstractEventLoop) -> None:
    """Call once at FastAPI startup to wire up the event loop."""
    global loop, level_queue
    loop = event_loop
    level_queue = asyncio.Queue(maxsize=40)


def set_mic_level(v: float) -> None:
    """Called from pyaudio thread. Thread-safe bridge to the asyncio queue."""
    with _levels_lock:
        _cur_levels["mic"] = round(v, 3)
        msg = {"type": "level", "mic": _cur_levels["mic"], "app": _cur_levels["app"]}
    _push_level(msg)


def set_app_level(v: float) -> None:
    """Called from pyaudio thread. Thread-safe bridge to the asyncio queue."""
    with _levels_lock:
        _cur_levels["app"] = round(v, 3)
        msg = {"type": "level", "mic": _cur_levels["mic"], "app": _cur_levels["app"]}
    _push_level(msg)


def _push_level(msg: dict) -> None:
    if loop is None or level_queue is None:
        return
    loop.call_soon_threadsafe(_enqueue_level, msg)


def _enqueue_level(msg: dict) -> None:
    """Drop oldest event when the queue is full so we never block pyaudio callbacks."""
    if level_queue is None:
        return
    if level_queue.full():
        try:
            level_queue.get_nowait()
        except Exception:
            pass
    try:
        level_queue.put_nowait(msg)
    except Exception:
        pass


async def broadcast(msg: dict) -> None:
    """Send a JSON message to every connected /ws/app client."""
    import json
    data = json.dumps(msg, ensure_ascii=False, default=str)
    dead: set = set()
    with _ws_lock:
        snapshot = set(active_ws)
    for ws in snapshot:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    if dead:
        with _ws_lock:
            active_ws.difference_update(dead)


def add_ws(ws) -> None:
    with _ws_lock:
        active_ws.add(ws)


def remove_ws(ws) -> None:
    with _ws_lock:
        active_ws.discard(ws)
