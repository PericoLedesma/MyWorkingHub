# SmartRecorder — Agentic Design

This document explains how the app changes when the agentic upgrade is applied,
and how the agent interacts with the rest of the system.

---

## Before vs. After (high level)

```
BEFORE                               AFTER
──────────────────────────           ──────────────────────────────────────────
User records audio                   User records audio
  → Whisper transcribes                → Whisper transcribes
  → Stored in DB                       → Analyzer runs automatically
  → User reads transcript                 (summary, action items, tags → DB)
                                       → Shown enriched in transcript modal
User chats with assistant            User chats with assistant
  → Single API call, no memory         → Tool-use loop: agent reads DB,
  → No access to recordings              controls recorder, answers questions
  → Stateless                           → Stateful across tool calls per turn
```

---

## What Changes in Each File

### `src/db.py` — 3 new columns, 3 new functions

The recordings table gains three columns populated by the post-recording analyzer:

```
recordings
├── ... (existing columns unchanged)
├── summary       TEXT   — 2-3 sentence auto-summary
├── action_items  TEXT   — JSON array, e.g. ["Alice: fix auth by Friday"]
└── tags          TEXT   — JSON array, e.g. ["standup", "auth"]
```

New functions:
- `save_analysis(filepath, summary, action_items, tags)` — writes the three new columns
- `search_transcripts(query)` — LIKE search across transcript text, returns snippets
- `load_metadata()` — updated to also return `id`, `summary`, `tags` per row

### `src/analyzer.py` — new file

A lightweight DeepSeek call that fires once per completed transcription.
Sends the transcript with a strict JSON-output prompt, parses the result,
and persists it via `db.save_analysis`.

### `src/agent.py` — full rewrite

Replaces the one-shot stateless call with a **tool-use loop** and a system prompt.
The agent now accepts a `tool_handlers` dict from the app and holds a list of
tool schemas in OpenAI function-calling format.

### `src/app.py` — wiring

- Instantiates `Analyzer` alongside `Transcriber`
- Passes 6 tool handler methods into `ChatAgent`
- Calls `_on_analysis_done` after each transcription
- Shows summary / action items / tags in the transcript modal

---

## The Tool-Use Loop (how the agent thinks)

DeepSeek supports the same function-calling format as OpenAI.
Each chat turn now works like this:

```
User types a message
        │
        ▼
  send_async() → daemon thread spawned
        │
        ▼
┌───────────────────────────────────────────────┐
│               _loop()  (daemon thread)        │
│                                               │
│  msgs = [system_prompt] + chat_history        │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │  POST /chat/completions                 │  │
│  │  model=deepseek-chat                    │  │
│  │  tools=[list of 6 tool schemas]         │  │
│  └──────────────┬──────────────────────────┘  │
│                 │                             │
│        ┌────────┴────────┐                   │
│        │                 │                   │
│   tool_calls?        no tool_calls           │
│        │                 │                   │
│        ▼                 ▼                   │
│  execute each tool   on_response(text)       │
│  append results      → after(0,…) → UI       │
│  loop ↑              DONE                    │
│                                              │
└──────────────────────────────────────────────┘
```

The loop runs entirely on the daemon thread. Results are marshalled
back to the Tkinter main thread via `self.after(0, ...)` — same
pattern used by Recorder and Transcriber.

---

## The 6 Tools

### Read-only tools (run on agent thread, safe)

| Tool | What it does |
|---|---|
| `list_recordings(limit)` | Returns JSON: id, name, duration, source, summary, tags for recent recordings |
| `get_transcript(recording_id)` | Returns full transcript text for a recording |
| `search_transcripts(query)` | LIKE search across all transcripts; returns matches with 150-char snippets |

### App-control tools (must touch Tkinter thread)

These use a `threading.Event` to schedule the action on the main thread and
wait for confirmation before returning to the agent loop:

```
Agent thread                        Tkinter main thread
────────────────                    ───────────────────
tool handler called
  event = Event()
  self.after(0, do_action) ──────→  do_action() runs
  event.wait(timeout=2s)            modifies app state
                         ←────────  event.set()
  return "Recording started."
```

| Tool | What it does |
|---|---|
| `start_recording(source)` | Sets mic/app toggles, calls `_toggle_recording()` |
| `stop_recording()` | Sets `self._stop_event` to end the current recording |
| `delete_recording(recording_id)` | Finds recording by id, calls `_delete(rec)` |

---

## Post-Recording Analysis Pipeline

```
User clicks Stop
      │
      ▼
Recorder thread exits → on_done(meta) → after(0, _on_done)
      │
      ▼
_on_done() — saves to DB, adds row to list, shows "···"
      │
      ▼  spawns Transcriber daemon thread
Whisper API call
      │
      ▼  on_done(filepath, text) → after(0, _on_transcribed)
_on_transcribed() — saves transcript, updates row to show 📄
      │
      ▼  spawns Analyzer daemon thread        ← NEW
DeepSeek API call (summary + action items + tags)
      │
      ▼  on_done(filepath, result) → after(0, _on_analysis_done)
_on_analysis_done() — saves analysis to DB, updates in-memory rec dict
      │
      ▼
Transcript modal now shows enriched content when opened
```

---

## How the Agent Knows About Recordings

The agent's `list_recordings` tool reads from `self._recordings` (the in-memory list
already loaded into the app). `get_transcript` and `search_transcripts` query
SQLite directly via `db.py`. The agent never reads WAV files.

```
Chat assistant
      │
      ├─ list_recordings  ──→  self._recordings (in-memory)
      ├─ get_transcript   ──→  smart_recorder.db → transcript column
      └─ search_transcripts →  smart_recorder.db → LIKE on transcript column
```

---

## Example Interaction

```
User:  "What action items came out of my last meeting recording?"

Agent: [calls list_recordings(limit=1)]
       → {"id": 7, "name": "Today · 14:32", "source": "both",
          "summary": "Team standup…", "tags": ["standup"]}

       [calls get_transcript(recording_id=7)]
       → "Alice will handle the auth refactor by Friday…"

       "From your recording today at 14:32, the action items are:
        • Alice: finish auth refactor by Friday
        • Bob: update the staging environment config"
```

---

## System Prompt (agent identity)

```
You are an assistant for SmartRecorder, a Windows audio recording app.

You have access to tools that let you:
- List and search the user's recordings
- Read full transcripts
- Start or stop a recording
- Delete recordings

Always use tools to look up real data before answering questions about
recordings or their content. When controlling the recorder, confirm
what you did in plain language.
```