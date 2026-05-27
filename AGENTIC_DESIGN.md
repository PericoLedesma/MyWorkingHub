# SmartRecorder — Architecture & Agentic Design

## 1. Current Architecture

### Component Map

```
main.py
└── SmartRecorderApp (src/app.py)   ← PySide6 main window, wires everything
    ├── Recorder       (src/recorder.py)    ← audio capture (mic / loopback / both)
    ├── Transcriber    (src/transcriber.py) ← OpenAI Whisper, async
    ├── Summarizer     (src/summarizer.py)  ← DeepSeek, Spanish bullet summary, async
    ├── ChatAgent      (src/agent.py)       ← DeepSeek chat, async
    └── db             (src/db.py)          ← SQLite, all persistence
```

### Post-Recording Pipeline

```
Record ──► save WAV + DB row
       ──► Transcriber.transcribe_async()
               └─► save transcript to DB
               └─► Summarizer.summarize_async()
                       └─► save summary to DB
```

### Thread Safety Model

All background work runs on daemon threads. Results are marshalled back to the Qt main thread exclusively via `Signal.emit()`. Never call Qt widgets directly from a worker thread.

| Signal | Emitted by | Connected to |
|---|---|---|
| `_sig_recording_done` | `Recorder` callback | `_on_done` |
| `_sig_transcribed` | `Transcriber` callback | `_on_transcribed` |
| `_sig_summarized` | `Summarizer` callback | `_on_summarized` |
| `_sig_agent_response` | `ChatAgent` callback | `_on_agent_response` |

### Database Schema (`smart_recorder.db`)

Table: `recordings`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-increment |
| `filepath` | TEXT UNIQUE | absolute path to .wav |
| `source` | TEXT | `mic`, `app`, `both`, `unknown` |
| `start_time` | TEXT | ISO-8601 |
| `end_time` | TEXT | ISO-8601 |
| `duration` | REAL | seconds |
| `sample_rate` | INTEGER | |
| `channels` | INTEGER | |
| `file_size` | INTEGER | bytes |
| `transcript` | TEXT | null until transcribed |
| `summary` | TEXT | null until summarized |
| `project` | TEXT | user-editable tag |
| `meeting` | TEXT | user-editable tag |

Schema version is tracked in a separate `schema_version` table. Migrations are additive (`ALTER TABLE ADD COLUMN`) — never destructive.

### API Dependencies

| Service | Used for | Env var |
|---|---|---|
| OpenAI Whisper (`whisper-1`) | Transcription | `OPENAI_API_KEY` |
| DeepSeek (`deepseek-chat`) | Summaries + chat | `DEEPSEEK_API_KEY` |

---

## 2. Maintenance Guide

### Adding a new background worker
Follow the pattern of `Transcriber` / `Summarizer`:
1. Create `src/myworker.py` with a class that spawns a daemon thread.
2. Accept `on_done` as a callback (never reference Qt directly).
3. Add a `Signal` in `SmartRecorderApp` and connect it to a handler.
4. Emit the signal from `on_done` instead of calling the handler directly.

### Adding a new DB column
1. Bump `_SCHEMA_VERSION` in `src/db.py`.
2. Add an `if db_version < N:` block that runs `ALTER TABLE recordings ADD COLUMN ...` wrapped in a `try/except OperationalError` (handles the case where the column already exists).
3. Update the `SELECT` in `load_metadata` and the return dict of `save_metadata`.

### Modifying the recording row UI
All row widgets are created in `SmartRecorderApp._make_row()` (`src/app.py`). The row is rebuilt from scratch on every `_rebuild_list()` call — there is no incremental update. Keep row construction cheap.

### Changing the summary prompt
Edit `_SYSTEM_PROMPT` in `src/summarizer.py`. The prompt is sent as the `system` role; the transcript is sent as `user`.

---

## 3. Agentic System Design

### Goal

Replace the current stateless chat assistant with a **tool-calling agent** that can:
- Answer questions about recordings (list, search, read transcripts/summaries)
- Control the app (start/stop recording, delete recordings, tag recordings)
- Chain multiple actions in a single conversation turn

### High-Level Architecture

```
User message
     │
     ▼
ChatAgent.send_async(messages, tools, tool_handlers)
     │
     ▼  ┌──────────────────────────────────────────┐
     │  │          AGENTIC LOOP (background thread) │
     │  │                                           │
     │  │  POST to DeepSeek /chat/completions       │
     │  │       with tools= list                    │
     │  │              │                            │
     │  │    tool_calls in response?                │
     │  │       YES ──► execute each tool           │
     │  │               append results to messages  │
     │  │               loop back ──────────────────┤
     │  │       NO  ──► return final text           │
     │  └──────────────────────────────────────────┘
     │
     ▼
_sig_agent_response.emit(text)  ──► _on_agent_response() → chat bubble
```

### Tool Inventory

| Tool name | Description | Needs app control? |
|---|---|---|
| `list_recordings` | Return recent recordings (id, name, duration, has_transcript, has_summary, project, meeting) | No |
| `get_transcript` | Return full transcript text for a filepath | No |
| `get_summary` | Return stored summary for a filepath | No |
| `search_transcripts` | Full-text search across all transcript columns | No |
| `tag_recording` | Set project and/or meeting on a recording | No |
| `delete_recording` | Delete a recording and its DB row | Yes (UI refresh) |
| `start_recording` | Begin recording (mic / app / both) | Yes (Qt main thread) |
| `stop_recording` | Stop an active recording | Yes (Qt main thread) |

### Implementation Plan

#### `src/db.py` — add `search_transcripts`

```python
def search_transcripts(query: str) -> list[dict]:
    """Return recordings whose transcript contains query (case-insensitive)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filepath, start_time, duration, project, meeting, "
            "substr(transcript, max(1, instr(lower(transcript), lower(?)) - 80), 200) as excerpt "
            "FROM recordings "
            "WHERE transcript LIKE ? "
            "ORDER BY start_time DESC LIMIT 20",
            (query, f"%{query}%"),
        ).fetchall()
    return [
        {"filepath": r[0], "start_time": r[1], "duration": r[2],
         "project": r[3], "meeting": r[4], "excerpt": r[5]}
        for r in rows
    ]
```

#### `src/agent.py` — rewrite with tool-use loop

```python
import json, os, threading
from openai import OpenAI

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_recordings",
            "description": "List recent recordings stored in the app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Filter by project name (optional)"},
                    "limit":   {"type": "integer", "description": "Max results (default 10)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transcript",
            "description": "Return the full transcript for a recording.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary",
            "description": "Return the stored summary for a recording.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_transcripts",
            "description": "Search across all transcripts for a keyword or phrase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tag_recording",
            "description": "Set the project and/or meeting tag on a recording.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "project":  {"type": "string"},
                    "meeting":  {"type": "string"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_recording",
            "description": "Permanently delete a recording and its transcript/summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_recording",
            "description": "Start a new recording.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["mic", "app", "both"],
                        "description": "Audio source to record from.",
                    },
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_recording",
            "description": "Stop the currently active recording.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class ChatAgent:
    def __init__(self):
        self._client = OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        )

    def send_async(self, messages: list[dict], tool_handlers: dict, on_response) -> None:
        threading.Thread(
            target=self._run_loop,
            args=(list(messages), tool_handlers, on_response),
            daemon=True,
        ).start()

    def _run_loop(self, messages: list[dict], tool_handlers: dict, on_response) -> None:
        try:
            while True:
                resp = self._client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
                msg = resp.choices[0].message

                if not msg.tool_calls:
                    on_response(msg.content)
                    return

                messages.append(msg)  # assistant turn with tool_calls

                for call in msg.tool_calls:
                    name = call.function.name
                    args = json.loads(call.function.arguments)
                    handler = tool_handlers.get(name)
                    result = handler(**args) if handler else f"Tool {name!r} not available."
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

        except Exception as exc:
            on_response(None)
```

#### `src/app.py` — wire tool handlers + system prompt

**System prompt** (injected as first message before user turn):
```python
def _build_system_prompt(self) -> str:
    is_recording = "Sí" if self._recording else "No"
    return (
        "Eres el asistente de SmartRecorder. Puedes gestionar grabaciones, "
        "transcripciones, resúmenes y controlar la grabación.\n"
        f"Grabando ahora: {is_recording}.\n"
        "Responde siempre en español."
    )
```

**Tool handlers** (passed to `agent.send_async`):
```python
def _build_tool_handlers(self) -> dict:
    return {
        "list_recordings":    self._tool_list_recordings,
        "get_transcript":     self._tool_get_transcript,
        "get_summary":        self._tool_get_summary,
        "search_transcripts": self._tool_search_transcripts,
        "tag_recording":      self._tool_tag_recording,
        "delete_recording":   self._tool_delete_recording,
        "start_recording":    self._tool_start_recording,
        "stop_recording":     self._tool_stop_recording,
    }
```

**Key handler implementations:**

```python
def _tool_list_recordings(self, project=None, limit=10) -> list:
    recs = self._recordings
    if project:
        recs = [r for r in recs if r.get("project", "").lower() == project.lower()]
    return [
        {
            "filepath": r["filepath"],
            "name": _format_name(r["filepath"]),
            "duration": _format_duration(r["duration"]),
            "has_transcript": r["has_transcript"],
            "has_summary": r.get("has_summary", False),
            "project": r.get("project", ""),
            "meeting": r.get("meeting", ""),
        }
        for r in recs[:limit]
    ]

def _tool_start_recording(self, source: str) -> str:
    # source must map to mic/app toggles before calling _toggle_recording
    if self._recording:
        return "Ya hay una grabación activa."
    source_map = {"mic": (True, False), "app": (False, True), "both": (True, True)}
    self._mic_on, self._app_on = source_map.get(source, (True, False))
    # Must run on Qt main thread — emit a dedicated signal
    self._sig_start_recording.emit()
    return f"Grabación iniciada ({source})."

def _tool_stop_recording(self) -> str:
    if not self._recording:
        return "No hay grabación activa."
    self._sig_stop_recording.emit()
    return "Grabación detenida."
```

App-control signals (add to `SmartRecorderApp`):
```python
_sig_start_recording = Signal()
_sig_stop_recording  = Signal()
```
Connected in `__init__`:
```python
self._sig_start_recording.connect(lambda: self._toggle_recording() if not self._recording else None)
self._sig_stop_recording.connect(lambda: self._stop_event.set() if self._recording else None)
```

**Updated `_send_message`:**
```python
def _send_message(self):
    text = self._chat_input.text().strip()
    if not text:
        return
    self._chat_input.clear()
    self._chat_history.append({"role": "user", "content": text})
    self._add_chat_bubble(text, role="user")
    self._send_btn.setEnabled(False)
    self._send_btn.setText("···")

    messages = [
        {"role": "system", "content": self._build_system_prompt()},
        *self._chat_history,
    ]
    self._agent.send_async(
        messages,
        tool_handlers=self._build_tool_handlers(),
        on_response=lambda t: self._sig_agent_response.emit(t),
    )
```

### Example Conversations

```
User:  ¿Cuántas grabaciones tengo del proyecto "Demo"?
Agent: [calls list_recordings(project="Demo")]
       Tienes 3 grabaciones en el proyecto Demo.

User:  Empieza a grabar el micrófono.
Agent: [calls start_recording(source="mic")]
       Grabación iniciada desde el micrófono.

User:  ¿Qué se habló en la última reunión?
Agent: [calls list_recordings(limit=1)]
       [calls get_summary(filepath="...")]
       En la última reunión se discutió...

User:  Busca todas las grabaciones donde se mencione "presupuesto".
Agent: [calls search_transcripts(query="presupuesto")]
       Encontré 2 grabaciones que mencionan "presupuesto": ...
```

### Files Changed Summary

| File | Change |
|---|---|
| `src/db.py` | Add `search_transcripts()` function |
| `src/agent.py` | Rewrite: tool definitions + agentic loop |
| `src/app.py` | Add tool handlers, system prompt builder, `_sig_start_recording` / `_sig_stop_recording` signals, update `_send_message` |
