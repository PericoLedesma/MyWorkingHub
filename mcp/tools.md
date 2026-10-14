# MCP Tool Catalogue — My Working Hub

Current state of the Claude Desktop integration. Maps app capabilities to tool groups and documents every implemented tool.

---

## 1. App Capabilities

| Capability | Description |
|---|---|
| **Audio recording** | Capture mic-only, system audio (WASAPI loopback), or both simultaneously. Configurable input device. |
| **Live transcription** | faster-whisper (`small` by default) runs locally, producing incremental segments every 5 s while recording. |
| **Post-recording transcription** | OpenAI `whisper-1` API call after recording stops; language auto-detected. |
| **AI bullet notes** | DeepSeek `deepseek-chat` via tool-calling (`guardar_notas`): action items, key points, decisions as markdown bullets. |
| **Focus Time tracking** | Work sessions with start/stop/duration stored in `focus_time.db`; daily aggregates available. |
| **Mouse jiggler** | Prevents screen lock by nudging the cursor; auto-stops on user input. UI-only, no MCP tools. |
| **Outlook calendar** | Reads upcoming meetings via COM (`pywin32`); requires desktop Outlook installed. |
| **OneNote integration** | Creates OneNote pages via COM (`pywin32`); requires desktop OneNote installed. |
| **Teams (Graph API)** | Reads chats, messages, teams, channels, presence via Microsoft Graph. WAM auth (no browser). |
| **Tag management** | `project` and `meeting` labels on each recording; editable via PATCH endpoint. |

---

## 2. Implemented Tools

| Group | Tool | Server | Notes |
|---|---|---|---|
| **Recordings – Read** | `list_recordings` | `recordings_server.py` | Filters: date range, project, meeting, limit |
| | `get_recording` | `recordings_server.py` | Returns transcript, notes, speaker data |
| | `search_recordings` | `recordings_server.py` | Keyword search across transcripts; `include_notes` flag |
| **Recordings – Write** | `transcribe_recording` | `recordings_server.py` | Run Whisper-1 on the WAV file; saves transcript to DB |
| | `write_notes` | `recordings_server.py` | Persist notes text to DB |
| | `launch_app` | `recordings_server.py` | Launch Electron app via `launch-widget.bat` (detached) |
| | `start_recording` | `recordings_server.py` | Start a live recording (`mic`/`app`/`both`); app must be running |
| | `get_current_recording` | `recordings_server.py` | Is a recording in progress? How long running? |
| **Projects – Read** | `list_projects` | `projects_server.py` | All projects with recording count, duration, last recording |
| | `get_project` | `projects_server.py` | Project metadata + recording list; case-insensitive name match |
| **Projects – Write** | `create_project` | `projects_server.py` | Create project; errors on duplicate name |
| | `update_project` | `projects_server.py` | Update any field by project ID; empty string = skip |
| | `assign_recording_to_project` | `projects_server.py` | Link recording to project by name; empty = unassign |
| **Focus Time** | `get_focus_stats` | `focus_server.py` | Daily totals (hours + session count), last 30 days |
| | `get_work_sessions` | `focus_server.py` | Individual session rows with start/end times, filterable by date |
| **Calendar** | `get_calendar_events` | `calendar_server.py` | Upcoming Outlook meetings; `days` param (default 1) |
| **OneNote** | `get_onenote_hierarchy` | `onenote_server.py` | Notebooks → sections/groups → pages with IDs |
| | `get_page_content` | `onenote_server.py` | Read all text from a page by ID |
| | `create_page` | `onenote_server.py` | Create page in first available section |
| | `create_page_in_section` | `onenote_server.py` | Create page in a specific section (by section ID) |
| | `create_section` | `onenote_server.py` | Create new section inside a notebook (requires local path) |
| | `create_recording_page` | `onenote_server.py` | Push a recording's notes to OneNote (title = meeting + date) |
| | `rename_page` | `onenote_server.py` | Rename a page by ID |
| | `rename_section` | `onenote_server.py` | Rename a section by ID |
| | `rename_notebook` | `onenote_server.py` | Rename a notebook display name by ID |
| | `move_page` | `onenote_server.py` | Move page to a different section |
| **Teams** | `teams_whoami` | `teams_server.py` | Confirm auth — returns display name + email |
| | `teams_list_chats` | `teams_server.py` | Recent chats with topic and last-message preview |
| | `teams_get_messages` | `teams_server.py` | Messages from a specific chat |
| | `teams_list_teams` | `teams_server.py` | All joined Teams |
| | `teams_list_channels` | `teams_server.py` | Channels in a Team |
| | `teams_get_presence` | `teams_server.py` | Current availability / activity status |
| | `teams_send_message` | `teams_server.py` | Send a message to a chat |
| **Hub Meta** | `get_readme` | `hub_server.py` | Return full README.md content |
| | `update_readme` | `hub_server.py` | Overwrite README.md |
| | `get_dev_context` | `hub_server.py` | Return CLAUDE.md (architecture + dev notes) |
| | `list_mcp_services` | `hub_server.py` | List all MCP servers and their registered tools |

---

## 3. Possible Future Tools

### `stop_recording`
Stop the active recording via MCP (currently only possible through the UI or direct HTTP).
**Endpoint:** `POST /api/record/stop`

### `list_devices`
List available audio input devices so Claude can help the user pick one before starting a recording.
**Endpoint:** `GET /api/devices`

### `update_recording_tags`
Set the `project` or `meeting` label on a recording directly from Claude.
**Endpoint:** `PATCH /api/recordings/{rid}/tags`
**Alternative:** Direct SQLite write (no app required), consistent with other read tools.
