# My Working Hub — Dev Context for Claude Code

## CRITICAL: Recording Integrity Rules

**Recordings are the single source of truth. Everything else — transcripts, summaries, notes — is derived from them. A lost recording is permanently lost data. No refactor, cleanup, or "simplification" of the recording path is worth the risk. When in doubt, leave it alone.**

1. **Never truncate mic audio.** The mic stream is continuous and authoritative. In `_mix_audio`, loopback is zero-padded to match mic length — never the other way around. WASAPI loopback only delivers chunks when audio is playing; silence produces no callbacks, so loopback duration will often be far shorter than wall-clock time. That is expected and must never cause mic data loss.

2. **`run_both()` always saves something.** If the loopback device is missing, fall back to `run_mic()`. If `mic_chunks` is non-empty at the end, always write the file. Never return `_on_done(None, ...)` when mic audio was successfully captured.

3. **`sf.write()` must be inside try/except.** A disk error must call `_on_done(None, "write_error")` — not leave `state.recording = True` permanently, which bricks the app until restart.

4. **Graceful shutdown must flush the recording.** Electron calls `POST /api/shutdown` before hard-killing Python. That endpoint stops the recording and awaits `state.recording_thread.join(timeout=30)` before returning. The lifespan teardown does the same on SIGTERM. The WAV file and DB row must be on disk before the process exits.

5. **`save_metadata` uses `INSERT OR IGNORE`.** Re-importing the same filepath is a no-op; the existing DB row is returned. Schema migrations are additive (`ALTER TABLE ADD COLUMN` only). Current schema version: **10**.

6. **`_stop_monitor()` must join the monitor thread before returning.** The monitor holds open WASAPI loopback stream handles. If recording starts before those handles are released, Windows gives the new stream a dead endpoint — the app audio bar goes silent during recording even though the monitor showed audio fine. `_stop_monitor()` sets the stop event AND calls `state.monitor_thread.join(timeout=2)`. Never remove the join or make `_stop_monitor` fire-and-forget.

7. **Stream open and stream close must be in separate try/except blocks in `run_mic`.** If `stop_stream()`/`close()` raises inside the same `try` as `pa.open()`, the `except` fires `_on_done(None, ...)` and discards all captured audio. The open is guarded to abort early; the close is best-effort. Captured chunks are always preserved.

8. **`sf.write` and `save_metadata` must be in separate try/except blocks.** If they share one block and `save_metadata` raises after the WAV is already written, the `except` reports failure and the WAV is orphaned on disk with no DB row — invisible in the UI, never cleaned up. Always write the file first, return on failure, then call `save_metadata` in its own block.

9. **`_on_recording_done` must always call `_start_monitor()` and broadcast `recording_stopped`.** These lines must run even if the DB work above them raises. Wrap the DB call in its own try/except so a metadata failure cannot leave the UI permanently stuck on "Recording…".

---

## What This Is

A Windows desktop productivity widget built with Electron + Python/FastAPI. Records meetings, transcribes with AI, generates bullet notes, tracks focus time, prevents screen lock (mouse jiggler), and integrates with Outlook/OneNote. An MCP layer exposes all functionality to Claude Desktop, organized by service.

Electron is purely the shell — it spawns Python as a child process and renders the web UI in a frameless always-on-top window with an animated slide-in panel on the right edge of the screen.

---

## Running the App

```batch
launch-widget.bat
```

Kills any process on port 8765, ensures `node_modules` is installed, then runs `npm start`. Electron starts, spawns Python (`main.py`), waits for the server, and shows the HUB tab.

**Browser-only dev mode** (no Electron):
```powershell
.venv\Scripts\python.exe main.py
# open http://127.0.0.1:8765
```

**MCP servers** (standalone, for Claude Desktop — each service is a separate process):
```powershell
.venv\Scripts\python.exe mcp/recordings_server.py
.venv\Scripts\python.exe mcp/hub_server.py
# etc. — see MCP Registration section
```

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Post-recording transcription via `whisper-1` |
| `DEEPSEEK_API_KEY` | Notes and chat via DeepSeek |
| `SMART_RECORDER_DATA_DIR` | Data directory (default: `%USERPROFILE%\OneDrive - Accenture\Desktop\MyDB`) |
| `LIVE_WHISPER_MODEL` | faster-whisper model: `tiny`, `base`, `small` (default), `medium` |

---

## Data Storage

Two SQLite databases in `SMART_RECORDER_DATA_DIR`:
- `smart_recorder.db` — recordings, transcripts, notes, tags, projects
- `focus_time.db` — Focus Time work sessions

WAV files live in a `recordings/` subfolder alongside the databases. Schema migrations are additive (`ALTER TABLE ADD COLUMN`, never destructive). Current schema version: **10**.

---

## Infrastructure

The backbone that all services share.

```
launch-widget.bat → npm start → electron/main.js
  electron/main.js        window management, spawns Python, IPC hub
  electron/tab.html       48×260px edge tab (HUB button + calendar strip)
  electron/preload.js     IPC bridge for the tab window
  electron/app-preload.js IPC bridge for the main app window

main.py → uvicorn → FastAPI on :8765
  src/web/server.py       all REST + WebSocket routes (single file)
  src/web/state.py        shared in-process state (WS connections, job sets, level queue)
  src/web/static/         SPA — index.html, app.js, style.css (no bundler)
  src/db.py               SQLite layer — recordings + projects schema + migrations
```

**WebSocket `/ws/app`** — real-time event bus from Python to the SPA:

| Event | Payload |
|---|---|
| `init` | current recording/job state on connect |
| `level` | mic + app audio VU levels |
| `recording_started` / `recording_stopped` | source, start_time / meta |
| `job_started` | job: `transcribe` \| `notes`, id |
| `transcribed` / `notes_generated` | id, ok |
| `live_transcript` | timestamp, text (incremental segment) |
| `imported` / `import_error` | meta / reason |

**IPC (Electron ↔ Web):**

`app-preload.js` exposes to the SPA:
- `pageChange(name)` — triggers panel width animation
- `setFocusState(active)` — reflects Focus Time dot on the HUB tab
- `onNavigate(cb)` — receives navigation commands from the tab

`preload.js` exposes to the tab window:
- `toggle()` — expand/collapse the panel
- `quit()` — close the app
- `openPage(name)` — navigate the main panel
- `onFocusState(cb)` — receive Focus Time active state

---

## Service: Recording

Handles audio capture, live transcription during recording, and post-recording AI processing (transcription → notes). Also exposes debug and status endpoints.

**Source files:**
```
src/recorder.py          audio capture — mic (WASAPI), loopback, or both
src/live_transcriber.py  real-time faster-whisper segments every 5s
src/transcriber.py       post-recording OpenAI whisper-1 transcription
src/agents/
  base.py                BaseAgent — tool-calling loop + streaming (shared)
  agent_notes.py         NotesAgent — DeepSeek, tool: guardar_notas
```

**AI models:**

| Task | Model | Notes |
|---|---|---|
| Live transcription | faster-whisper `small` (local) | Every 5s during recording |
| Post-recording transcription | OpenAI `whisper-1` | After recording stops |
| Notes | DeepSeek `deepseek-chat` | Tool-call: `guardar_notas` |

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/recordings` | List all recordings with metadata |
| `POST` | `/api/recordings/scan` | Rescan folder and return list |
| `PATCH` | `/api/recordings/{rid}/tags` | Set `project` or `meeting` label |
| `DELETE` | `/api/recordings/{rid}` | Delete recording + WAV file |
| `GET` | `/api/recordings/{rid}/transcript` | Full transcript, notes, speaker |
| `PATCH` | `/api/recordings/{rid}/notes` | Overwrite notes text |
| `POST` | `/api/recordings/{rid}/transcribe` | Trigger Whisper transcription |
| `POST` | `/api/recordings/{rid}/generate-notes` | Trigger DeepSeek notes |
| `GET` | `/api/devices` | List available audio input devices |
| `GET` | `/api/debug/audio-devices` | All PyAudio + WASAPI loopback devices (for capture diagnostics) |
| `POST` | `/api/record/start` | Start recording `{source, device_index}` |
| `POST` | `/api/record/stop` | Stop active recording |
| `GET` | `/api/recording/status` | Current recording state `{recording, recording_start}` |
| `POST` | `/api/shutdown` | Graceful shutdown — flushes recording before process exits |
| `POST` | `/api/import` | Import audio file (WAV or ffmpeg-converted) |
| `GET` | `/audio/{rid}` | Serve WAV file |
| `GET` | `/api/open_folder/{rid}` | Open file location in Explorer |

**MCP tools** (`mcp/recordings_server.py`):

| Tool | Status | Description |
|---|---|---|
| `list_recordings` | Exists | Browse with date/project/meeting filters |
| `get_recording` | Exists | Recording info + transcript + notes |
| `search_recordings` | Exists | Keyword search across transcripts and notes (`include_notes` flag) |
| `transcribe_recording` | Exists | Run Whisper-1 on the WAV file and save transcript to DB |
| `write_notes` | Exists | Persist notes (written by the agent) to DB |
| `launch_app` | Exists | Launch the Electron desktop app via `launch-widget.bat` (detached process) |
| `start_recording` | Exists | Start a live recording (`mic`/`app`/`both`); requires app to be running |
| `get_current_recording` | Exists | Check whether a recording is in progress and how long it has been running |

---

## Service: Projects

Organizes recordings under named projects with metadata (client, status, objective, phases, streams).

**Source files:**
```
src/db.py   projects table — list_projects, create_project, update_project, delete_project
```

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects` | List all projects |
| `POST` | `/api/projects` | Create project `{name, description, status, client, objective, phases, streams, color}` |
| `PATCH` | `/api/projects/{pid}` | Update project fields |
| `DELETE` | `/api/projects/{pid}` | Delete project |

Project status values: `Active` \| `On Hold` \| `Done`.

**MCP tools** (`mcp/projects_server.py`):

| Tool | Status | Description |
|---|---|---|
| `list_projects` | Exists | All projects with recording counts and total duration |
| `get_project` | Exists | Project metadata + its recordings (case-insensitive name match) |
| `create_project` | Exists | Create project; errors on duplicate name |
| `update_project` | Exists | Update fields by project ID; empty string = skip |
| `assign_recording_to_project` | Exists | Link recording to project by name; empty = unassign |

---

## Service: Focus Time

Tracks work/deep-focus sessions with start/stop/duration. Sessions are stored in a separate DB so they never block or conflict with recording operations.

**Source files:**
```
src/focus_db.py   focus_time.db — start_work_session, end_work_session, list_work_sessions
```

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/work-sessions/current` | Currently active session `{id, start_time}` or null |
| `POST` | `/api/work-sessions` | Start a new session |
| `PATCH` | `/api/work-sessions/{id}/end` | End session |
| `GET` | `/api/work-sessions?date=YYYY-MM-DD` | List sessions, optional date filter |
| `DELETE` | `/api/work-sessions?date=YYYY-MM-DD` | Delete all sessions on a date |

**MCP tools** (`mcp/focus_server.py`):

| Tool | Status | Description |
|---|---|---|
| `get_focus_stats` | Exists | Daily totals (hours + session count), last 30 days |
| `get_work_sessions` | Exists | Individual session rows with start/end times, filterable by date |

---

## Service: Jiggler

Prevents the screen from locking by nudging the mouse cursor at a fixed interval. Stops automatically if the user moves the mouse or presses a key (detected via `pynput`).

**Source files:**
```
src/jiggler.py   MouseJiggler class — pyautogui nudge loop + pynput keyboard listener
```

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/jiggler/start` | Start jiggling (3-second interval); auto-stops on user input |
| `POST` | `/api/jiggler/stop` | Stop jiggling |
| `GET` | `/api/jiggler/status` | `{running: bool}` |

**WebSocket events:** `jiggler_stopped` `{reason: "user_input"}` — fired when the jiggler auto-stops because the user moved the mouse or pressed a key.

---

## Service: Calendar (Outlook)

Reads upcoming meetings from Outlook via COM. Requires desktop Outlook installed; app must be running.

**Source files:**
```
src/outlook.py   COM integration — get_events(days_ahead)
```

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/calendar/events?days=N` | Upcoming Outlook meetings (default: 1 day) |

**MCP tools** (`mcp/calendar_server.py`):

| Tool | Status | Description |
|---|---|---|
| `get_calendar_events` | Exists | Upcoming Outlook meetings; `days` param controls how far ahead to look |

---

## Service: Notes (OneNote)

Pushes recording summaries/notes to OneNote as pages via COM. Requires desktop OneNote installed; app must be running.

**Source files:**
```
src/onenote.py   COM integration — get_hierarchy, get_page_content, create_page, rename_*, move_page, create_section
```

**REST API endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/onenote/test` | Hello-world page creation |
| `GET` | `/api/onenote/hierarchy` | List notebooks/sections |

**MCP tools** (`mcp/onenote_server.py`):

| Tool | Status | Description |
|---|---|---|
| `get_onenote_hierarchy` | Exists | Notebooks → sections/section groups → pages with IDs (typed JSON) |
| `get_page_content` | Exists | Read all text content from a page by ID |
| `create_page` | Exists | Create a page in the first available section |
| `create_page_in_section` | Exists | Create a page in a specific section (by section ID) |
| `create_section` | Exists | Create a new section inside a notebook (requires local path) |
| `create_recording_page` | Exists | Push a recording's notes to OneNote (title = meeting + date) |
| `rename_page` | Exists | Rename a page by ID |
| `rename_section` | Exists | Rename a section by ID |
| `rename_notebook` | Exists | Rename a notebook display name by ID |
| `move_page` | Exists | Move a page to a different section (by page ID + target section ID) |

---

## Service: Hub Meta

Read and update hub documentation without needing the app to run. Also provides a service catalogue so Claude knows what tools are available.

**Source files:**
```
mcp/hub_server.py   file I/O on README.md and CLAUDE.md; static service catalogue
```

**MCP tools** (`mcp/hub_server.py`):

| Tool | Status | Description |
|---|---|---|
| `get_readme` | Exists | Return the full README.md content |
| `update_readme` | Exists | Overwrite README.md with new content |
| `get_dev_context` | Exists | Return CLAUDE.md (architecture and dev notes) |
| `list_mcp_services` | Exists | List all MCP servers and their registered tools |

---

## Service: Teams (Microsoft Graph)

Reads Teams data via Graph API. Auth uses **Windows WAM broker** (Web Account Manager) — the same OS-level auth that Teams itself uses. First call shows a native Windows account picker; all subsequent calls (including after restarts) are completely silent. No device-code URL, no browser tab.

**No env vars required.** Uses the Microsoft Graph PowerShell public client ID — no Azure App Registration needed.

**Auth (same pattern as `src/outlook.py`):** Token cached to `DATA_DIR/teams_token_cache.json`. On first call, WAM broker shows a native Windows account picker (no browser, no device code). Silent on all subsequent calls, including after restarts.

**Source files:**
```
src/teams.py          Graph API calls + WAM auth + token cache
mcp/teams_server.py   Thin FastMCP wrapper (same pattern as calendar_server.py)
```

**MCP tools** (`mcp/teams_server.py`):

| Tool | Graph endpoint | Description |
|---|---|---|
| `teams_whoami` | `GET /me` | Confirm auth works — returns display name + email |
| `teams_list_chats` | `GET /me/chats` | List recent chats with topic and last-message preview |
| `teams_get_messages` | `GET /me/chats/{id}/messages` | Read messages from a specific chat |
| `teams_list_teams` | `GET /me/joinedTeams` | List all joined Teams |
| `teams_list_channels` | `GET /teams/{id}/channels` | List channels in a Team |
| `teams_get_presence` | `GET /me/presence` | Current availability / activity status |
| `teams_send_message` | `POST /me/chats/{id}/messages` | Send a message to a chat |

**Scopes:** `Chat.Read`, `User.Read`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`, `Presence.Read` (user-consent only, no admin approval needed).

---

## Service: Chat

Conversational AI over all past recordings. Streaming DeepSeek agent that searches the SQLite database to answer questions about past meetings.

**Source files:**
```
src/agents/base.py              BaseAgent — tool-calling loop + streaming
src/agents/agent_orchestrator.py  ChatAgent — system prompt + buscar_grabaciones tool
```

**WebSocket:** `/ws/chat` — receives `{text}`, streams back `{type: "token", text}` and `{type: "done"}`.

The chat agent has one tool: `buscar_grabaciones` — full-text search over the `transcript` column in SQLite, returns up to 20 matching recordings with context excerpts. Chat history is kept in `state.chat_history` (in-process, resets on restart).

---

## MCP Registration (Claude Desktop)

Each service is a separate MCP server. Add all seven to `claude_desktop_config.json`. Note: Jiggler has no MCP server (UI-only feature).

**Config file location:**
```
%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

```json
{
  "mcpServers": {
    "hub-recordings": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\recordings_server.py"]
    },
    "hub-projects": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\projects_server.py"]
    },
    "hub-focus": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\focus_server.py"]
    },
    "hub-calendar": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\calendar_server.py"]
    },
    "hub-onenote": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\onenote_server.py"]
    },
    "hub-meta": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\hub_server.py"]
    },
    "hub-teams": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\teams_server.py"]
    }
  }
}
```

All tools read SQLite or call COM/PowerShell directly — none require the app to be running. `hub-teams` requires one-time OAuth consent (native Windows WAM account picker); subsequent calls are fully silent.

---

## Development Notes

- Python deps: `.venv\Scripts\pip install -r requirements.txt`
- Node deps: `npm install` (no build step)
- `pyaudiowpatch` required (not `pyaudio`) for WASAPI loopback on Windows
- COM integrations require desktop Office installed (`pywin32`)
- No TypeScript, no bundler — frontend is plain HTML/JS/CSS
- All agents inherit `BaseAgent` and create their own `OpenAI` client pointing to `https://api.deepseek.com`

---

## Response Format

After completing any task, always end the response with a short summary section titled **"What was done"** as a bullet list. Keep each bullet to one sentence. No extra commentary after the list.
