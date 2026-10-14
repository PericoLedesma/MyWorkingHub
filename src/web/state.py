"""Shared in-process state for the FastAPI web server."""
import asyncio
import threading

# asyncio event loop — set at startup via init()
loop: asyncio.AbstractEventLoop | None = None

# Active /ws/app WebSocket connections
active_ws: set = set()
_ws_lock = threading.Lock()

# Recording state
recording: bool = False
recording_start: float | None = None   # time.time() value when recording started
stop_event: threading.Event | None = None
recording_thread: threading.Thread | None = None

# Project / meeting labels to apply to the recording currently being captured.
# Held server-side (not in the browser) so the label survives a UI reload and is
# written to the DB even when no WebSocket client is connected at stop time.
pending_project: str = ""
pending_meeting: str = ""

# In-progress job id sets (all mutations must happen on the event loop)
transcribing: set[int] = set()
notes_generating: set[int] = set()

# Server-managed focus session (started on launch, ended on shutdown)
focus_session_id: int | None = None
focus_session_start: str | None = None

# Accumulated live transcript segments for the current recording
# Each entry: (timestamp, text, speaker)
live_segments: list[tuple[str, str, str]] = []


def init(event_loop: asyncio.AbstractEventLoop) -> None:
    """Call once at FastAPI startup to wire up the event loop."""
    global loop
    loop = event_loop


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


def count_watchers() -> int:
    """Return the number of active /ws/app connections. Thread-safe."""
    with _ws_lock:
        return len(active_ws)


def add_ws(ws) -> None:
    with _ws_lock:
        active_ws.add(ws)


def remove_ws(ws) -> None:
    with _ws_lock:
        active_ws.discard(ws)
