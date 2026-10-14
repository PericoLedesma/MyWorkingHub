# My Working Hub

A Windows desktop productivity widget that lives on the right edge of your screen. Records meetings, transcribes with AI, tracks focus time, integrates with Outlook, OneNote, and Teams — all from a slide-in panel triggered by the **HUB** tab.

---

## Setup

**Requirements:**
- Windows (WASAPI loopback is Windows-only)
- Python 3.11+
- Node.js
- `ffmpeg` in `PATH` — optional, only needed to import non-WAV audio files
- Desktop Outlook and OneNote — optional, required for calendar and OneNote integration

**Install:**

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
npm install
```

**API keys** (set as Windows environment variables):

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Post-recording transcription via Whisper |
| `DEEPSEEK_API_KEY` | Notes via DeepSeek |

**Data directory** — recordings and databases are stored in:
```
%USERPROFILE%\OneDrive - Accenture\Desktop\MyDB
```
Override by setting `SMART_RECORDER_DATA_DIR` to any path.

---

## Running

```batch
launch-widget.bat
```

Kills any process on port 8765, installs Node modules if needed, and launches the Electron widget. The HUB tab appears on the right edge of your screen.

**Browser-only mode** (no Electron, useful for dev):
```powershell
.venv\Scripts\python.exe main.py
# open http://127.0.0.1:8765
```

---

## Tabs

### Home (Diario)

The daily agenda dashboard. Shows a live focus timer, this month's recording count, and today's upcoming calendar events. A persistent agenda section lets you add, complete, and delete todo items organized into named sections — state is saved automatically and survives restarts.

Quick-action buttons jump to Recordings, Calendar, and Focus Time.

---

### Recordings

Control panel for capturing audio.

1. Choose a source: **Mic**, **App** (system audio/calls), or **Both**
2. Optionally select an input device from the dropdown
3. Tag the recording with a **Project** and **Meeting** name before or after recording
4. Hit **Record** — VU meters and a live transcript appear in real-time
5. Hit **Stop** — the WAV file is saved, then Whisper transcription runs automatically

You can also **import** an existing audio file (WAV, or any format if ffmpeg is installed) to run it through the same transcription pipeline.

**AI models used:**

| Task | Model |
|---|---|
| Live transcription (during recording) | faster-whisper `small` — runs locally, no API cost |
| Post-recording transcription | OpenAI `whisper-1` |
| Notes | DeepSeek `deepseek-chat` (via API or MCP only) |

---

### History

Browse all past recordings as cards. Each card shows:
- Meeting name (editable inline), project tag, source, duration, and transcript status
- A compact audio player with waveform seek
- **Footer actions:** Transcribe (primary, glows indigo when no transcript), folder icon to open file location, trash icon to delete, and a **Details** pill that expands the card

Cards are centered and capped at 780 px wide so they never stretch edge-to-edge.

Expanding a card opens a two-tab detail panel:
1. **Notes** (active by default) — personal notes textarea, auto-saved on blur
2. **Transcript** — full Whisper transcript

A timeline bar at the top shows all recordings grouped by month as colored dots — click any dot to jump to that recording.

Cards can be filtered by project, date range, or keyword search.

> **Note:** AI notes generation is not available in the History UI. Notes can only be triggered via the API (`POST /api/recordings/{rid}/generate-notes`) or Claude Desktop MCP (`write_notes`).

---

### Calendar

Shows today's Outlook calendar events pulled via the Office COM API. Requires desktop Outlook installed and running. You can look ahead by a configurable number of days.

---

### Focus Time

Tracks deep-work sessions with a start/stop timer. Each session records its start time, end time, and duration in a local SQLite database. The dashboard shows daily totals and a session log for the selected date. Sessions can be deleted by date.

The Focus dot on the HUB tab glows while a session is active so you can see your status at a glance without opening the panel.

---

### OneNote

Pushes content to OneNote via the Office COM API. Requires desktop OneNote installed. Supports browsing the notebook hierarchy, creating pages and sections, renaming, and moving pages. Recording summaries can be pushed to OneNote as formatted pages directly from Claude Desktop via the MCP server.

---

### Tools

Utility page for audio device management and other diagnostics. Lists available audio input devices with their index numbers, which is useful for selecting a specific microphone in the Recordings tab.

---

### Guide

In-app help page with usage instructions and tips.

---

## Claude Desktop Integration (MCP)

My Working Hub exposes one MCP server per service. Most read SQLite directly or call COM — they do not require the app to be running.

**Config file location:**
```
%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

**Register in `claude_desktop_config.json`:**

```json
{
  "mcpServers": {
    "hub-recordings": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\recordings_server.py"],
      "env": { "OPENAI_API_KEY": "<your-key>" }
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
    "hub-outlook": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\outlook_server.py"]
    },
    "hub-onenote": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\onenote_server.py"]
    },
    "hub-teams": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\teams_server.py"]
    },
    "hub-diario": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\diario_server.py"]
    },
    "hub-meta": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\hub_server.py"]
    }
  }
}
```

**Available tools by service:**

| Service | Server | Tool | App required? | Description |
|---|---|---|---|---|
| **Recordings** | `hub-recordings` | `list_recordings` | No | Browse with date/project/meeting filters |
| | | `get_recording` | No | Full detail: transcript, notes, speakers |
| | | `search_recordings` | No | Keyword search across transcripts and notes (`include_notes` flag) |
| | | `transcribe_recording` | Yes | Run Whisper-1 on a WAV file and save the transcript |
| | | `write_notes` | No | Persist notes to the database |
| | | `launch_app` | No | Launch the Electron desktop app |
| | | `start_recording` | Yes | Start a live recording (mic / app / both) |
| | | `get_current_recording` | Yes | Check recording state and elapsed time |
| **Projects** | `hub-projects` | `list_projects` | No | All projects with recording counts and total duration |
| | | `get_project` | No | Project metadata and its recordings |
| | | `create_project` | No | Create a new project |
| | | `update_project` | No | Update project fields by ID |
| | | `assign_recording_to_project` | No | Link a recording to a project by name |
| **Focus Time** | `hub-focus` | `get_focus_stats` | No | Daily focus hours and session counts (last 30 days) |
| | | `get_work_sessions` | No | Individual sessions with start/end times, filterable by date |
| **Calendar** | `hub-calendar` | `get_calendar_events` | No* | Upcoming Outlook meetings (*requires desktop Outlook) |
| **Outlook Email** | `hub-outlook` | `get_emails` | No* | Most recent inbox emails; `count` param (default 20), newest first |
| | | `get_unread_emails` | No* | Unread inbox emails only; `count` param (default 20) |
| **OneNote** | `hub-onenote` | `get_onenote_hierarchy` | No* | Notebooks → sections → pages with IDs |
| | | `get_page_content` | No* | Read text content of a page by ID |
| | | `create_page` | No* | Create a page in the first available section |
| | | `create_page_in_section` | No* | Create a page in a specific section |
| | | `create_section` | No* | Create a section inside a notebook |
| | | `create_recording_page` | No* | Push a recording's notes to OneNote |
| | | `rename_page` | No* | Rename a page by ID |
| | | `rename_section` | No* | Rename a section by ID |
| | | `rename_notebook` | No* | Rename a notebook display name by ID |
| | | `move_page` | No* | Move a page to a different section |
| **Teams** | `hub-teams` | `teams_whoami` | No | Confirm Graph auth — returns display name + email |
| | | `teams_list_chats` | No | List recent chats with topic and last-message preview |
| | | `teams_get_messages` | No | Read messages from a specific chat |
| | | `teams_list_teams` | No | List all joined Teams |
| | | `teams_list_channels` | No | List channels in a Team |
| | | `teams_get_presence` | No | Current availability / activity status |
| | | `teams_send_message` | No | Send a message to a chat |
| **Diario** | `hub-diario` | `get_diario` | No | Full agenda for a date (default: today) |
| | | `add_item` | No | Add a todo item to a section |
| | | `complete_item` | No | Toggle an item's done state |
| | | `delete_item` | No | Remove an item from today |
| | | `add_section` | No | Add a new section to today |
| | | `delete_section` | No | Remove a section and all its items |
| **Hub Meta** | `hub-meta` | `get_readme` | No | Return the full README.md content |
| | | `update_readme` | No | Overwrite README.md with new content |
| | | `get_dev_context` | No | Return CLAUDE.md (architecture and dev notes) |
| | | `list_mcp_services` | No | List all MCP servers and their registered tools |

*No app required, but requires desktop Office (Outlook / OneNote) installed.

**Teams auth:** Zero config — no Azure App Registration needed. Uses the Microsoft Graph PowerShell public client ID with Windows WAM broker. First call shows a native Windows account picker (one click, no browser). Token cached locally; all subsequent calls are completely silent.

---

## Project Structure

```
SmartRecorder/
├── main.py                     Python entry point (uvicorn/FastAPI)
├── launch-widget.bat           Windows launcher
├── package.json                Electron/npm config
├── requirements.txt            Python dependencies
├── electron/
│   ├── main.js                 Desktop shell, window management, spawns Python
│   ├── tab.html                48×260px HUB tab on screen edge
│   ├── preload.js              IPC bridge for the tab window
│   └── app-preload.js          IPC bridge for the main app window
├── src/
│   ├── db.py                   SQLite — recordings, projects, diario, schema migrations
│   ├── focus_db.py             SQLite — Focus Time sessions
│   ├── recorder.py             Audio capture (PyAudioWPatch, WASAPI loopback)
│   ├── transcriber.py          Post-recording Whisper API transcription
│   ├── live_transcriber.py     Real-time faster-whisper (every 5s)
│   ├── outlook.py              Outlook COM integration (calendar + email)
│   ├── onenote.py              OneNote COM integration
│   ├── teams.py                Teams Graph API + Windows WAM auth
│   ├── jiggler.py              Mouse jiggler utility
│   ├── agents/
│   │   ├── base.py             BaseAgent — tool-calling loop + streaming
│   │   └── agent_notes.py      NotesAgent (DeepSeek, tool: guardar_notas)
│   └── web/
│       ├── server.py           FastAPI app — all REST + WebSocket routes
│       ├── state.py            Shared in-process state (WS connections, queues)
│       └── static/
│           ├── index.html      SPA shell + all tab markup
│           ├── app.js          All frontend logic (vanilla JS)
│           └── style.css       Dark theme styles
└── mcp/
    ├── recordings_server.py    MCP — list/get/search/transcribe recordings
    ├── projects_server.py      MCP — list/create/update projects
    ├── focus_server.py         MCP — focus stats and work sessions
    ├── calendar_server.py      MCP — Outlook calendar events (COM)
    ├── outlook_server.py       MCP — Outlook inbox email (COM)
    ├── onenote_server.py       MCP — OneNote hierarchy and page ops (COM)
    ├── teams_server.py         MCP — Teams chats, messages, presence (Graph API)
    ├── diario_server.py        MCP — daily agenda (read DB directly)
    ├── hub_server.py           MCP — hub meta: README, dev context, service catalogue
    └── tools.md                Tool catalogue and candidate tools
```
