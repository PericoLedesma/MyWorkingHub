# MCP Tool Catalogue — My Working Hub

Design document for the Claude Desktop integration (`mcp/server.py`). Maps app capabilities to tool groups, marks what already exists, and describes every candidate tool with enough detail to implement it.

---

## 1. App Features

| Capability | Description |
|---|---|
| **Audio recording** | Capture mic-only, system audio (WASAPI loopback), or both simultaneously. Configurable input device. |
| **Live transcription** | faster-whisper (`small` by default) runs locally, producing incremental segments every 5 s while recording. |
| **Post-recording transcription** | OpenAI `whisper-1` API call after recording stops; language auto-detected. |
| **AI summarisation** | DeepSeek `deepseek-chat` via tool-calling (`guardar_resumen`): structured summary with topics, decisions, action items. |
| **AI bullet notes** | DeepSeek `deepseek-chat` via tool-calling (`guardar_notas`): action items, key points, decisions as markdown bullets. |
| **Search & chat** | DeepSeek streaming agent over `/ws/chat`; calls `buscar_grabaciones` to query SQLite full-text across all recordings. |
| **Focus Time tracking** | Work sessions with start/stop/duration stored in `focus_time.db`; daily aggregates available. |
| **Outlook calendar** | Reads upcoming meetings via COM (`pywin32`); requires desktop Outlook installed. |
| **OneNote integration** | Creates OneNote pages via COM (`pywin32`); requires desktop OneNote installed. |
| **Tag management** | `project` and `meeting` labels on each recording; editable via PATCH endpoint. |

---

## 2. Tool Groups + Current Tools

| Group | Tool | Status | Notes |
|---|---|---|---|
| **Recordings – Read** | `list_recordings` | ✅ Exists | Filters: date range, project, meeting, limit |
| | `get_recording` | ✅ Exists | Returns transcript, summary, notes, speaker data |
| | `search_recordings` | ✅ Exists | Keyword search; optionally includes summaries/notes |
| **Focus Time** | `get_focus_stats` | ✅ Exists | Daily totals (hours + session count), last 30 days |
| **Projects – Read** | `list_projects` | ✅ Exists | All projects with recording count, duration, last recording |
| | `get_project` | ✅ Exists | Project metadata + recording list; case-insensitive name match |
| **Projects – Write** | `create_project` | ✅ Exists | Create project; raises error on duplicate name |
| | `update_project` | ✅ Exists | Update any field by project ID; empty string = skip |
| | `assign_recording_to_project` | ✅ Exists | Link recording to project by name; empty name = unassign |
| **Recorder Control** | `get_app_state` | Candidate | Is a recording in progress? Any jobs running? |
| | `start_recording` | Candidate | Trigger recording with source + device; app must be running |
| | `stop_recording` | Candidate | Stop the active recording; app must be running |
| | `list_devices` | Candidate | Enumerate available audio input devices |
| **AI Processing** | `transcribe_recording` | Candidate | Kick off post-recording Whisper transcription |
| | `generate_notes` | Candidate | Kick off DeepSeek notes generation for a recording |
| **Calendar** | `get_calendar_events` | Candidate | Fetch upcoming Outlook meetings; app must be running |
| **Notes & Tagging** | `create_onenote_page` | Candidate | Push a recording's summary/notes to OneNote |
| | `update_recording_tags` | Candidate | Set project or meeting label on a recording |
| **Focus Time – Detail** | `get_work_sessions` | Candidate | Individual session rows (not just daily totals) |

---

## 3. Possible New Tools

### `get_app_state`

**What it does:** Returns a snapshot of the app's live state: whether a recording is in progress, which recording IDs are being transcribed/summarised/noted.

**Endpoint / data:** The `/ws/app` WebSocket sends an `init` event on connect with these fields. For MCP (HTTP polling), a new `GET /api/state` endpoint would be the cleanest approach, or the tool can attempt a quick WebSocket connect and read the first message.

**App must be running:** Yes — this reflects runtime state, not DB state.

**Example prompts:**
- *"Is My Working Hub currently recording something?"*
- *"Check if there are any transcription jobs still running."*

---

### `start_recording`

**What it does:** Starts a new audio recording session. Accepts `source` (`mic` | `app` | `both`) and optional `device_index`.

**Endpoint:** `POST /api/record/start` — body `{ "source": "mic", "device_index": null }`.

**App must be running:** Yes.

**Example prompts:**
- *"Start recording the mic."*
- *"Begin capturing system audio."*
- *"Record both mic and speakers for this meeting."*

---

### `stop_recording`

**What it does:** Stops the active recording. The server then kicks off transcription automatically.

**Endpoint:** `POST /api/record/stop`.

**App must be running:** Yes.

**Example prompts:**
- *"Stop the current recording."*
- *"I'm done with this meeting, stop recording."*

---

### `list_devices`

**What it does:** Lists available audio input devices (index + name).

**Endpoint:** `GET /api/devices`.

**App must be running:** Yes.

**Example prompts:**
- *"What microphones are available?"*
- *"Which audio devices can I record from?"*

---

### `transcribe_recording`

**What it does:** Triggers post-recording Whisper API transcription for a recording that hasn't been transcribed yet (or re-transcribes).

**Endpoint:** `POST /api/recordings/{rid}/transcribe`.

**App must be running:** Yes — the transcription job runs inside the Python process.

**Example prompts:**
- *"Transcribe recording 42."*
- *"Run transcription on last week's Monday meeting."*

---


### `generate_notes`

**What it does:** Triggers DeepSeek bullet-notes generation (action items, key points, decisions) for a recording that has a transcript.

**Endpoint:** `POST /api/recordings/{rid}/generate-notes`.

**App must be running:** Yes.

**Example prompts:**
- *"Generate notes for recording 42."*
- *"What were the action items from yesterday's standup?"* *(combined with `get_recording`)*

---

### `get_calendar_events`

**What it does:** Returns upcoming Outlook calendar events. Accepts `days` (default 1).

**Endpoint:** `GET /api/calendar/events?days=N`. Internally uses `src/outlook.py` via COM.

**App must be running:** Yes. Also requires desktop Outlook installed and the COM interface available.

**Example prompts:**
- *"What meetings do I have today?"*
- *"Show my calendar for the next 3 days."*
- *"Is there anything scheduled this afternoon?"*

---

### `create_onenote_page`

**What it does:** Creates a OneNote page from a recording's summary and notes. The page title would be the meeting name + date; the body would be the structured summary and bullet notes.

**Endpoint:** Currently only `POST /api/onenote/test` exists (hello-world). A real `POST /api/onenote/recording/{rid}` endpoint needs to be added in `server.py` and `src/onenote.py`.

**App must be running:** Yes. Also requires desktop OneNote installed.

**Example prompts:**
- *"Send recording 42's summary to OneNote."*
- *"Create a OneNote page for today's product meeting."*

---

### `update_recording_tags`

**What it does:** Sets the `project` or `meeting` label on a recording.

**Endpoint:** `PATCH /api/recordings/{rid}/tags` — body `{ "field": "project"|"meeting", "value": "..." }`.

**App must be running:** Yes (hits the FastAPI server, though the write goes to SQLite).

**Alternative:** Could be implemented as a direct SQLite write (no app required), matching the pattern of the existing read-only tools.

**Example prompts:**
- *"Tag recording 42 as project 'Alpha'."*
- *"Label last Friday's recording as 'Design Review'."*
- *"Set the meeting name for recording 15 to 'Sprint Planning'."*

---

### `get_work_sessions`

**What it does:** Returns individual work session rows (start time, end time, duration) optionally filtered by date. More granular than `get_focus_stats`, which only gives daily totals.

**Endpoint:** `GET /api/work-sessions?date=YYYY-MM-DD`. Reads from `focus_time.db`.

**App must be running:** No — can read directly from `focus_time.db` (same pattern as existing tools).

**Example prompts:**
- *"Show me all focus sessions from last Monday."*
- *"How long was each work block yesterday?"*
- *"Did I have any focus sessions this morning?"*
