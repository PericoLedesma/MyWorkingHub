# My Working Hub

A Windows desktop productivity widget that lives on the right edge of your screen. Records meetings, transcribes with AI, tracks focus time, integrates with Outlook, OneNote, and Teams, and lets you chat with your entire meeting history — all from a slide-in panel triggered by the **HUB** tab.

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
| `DEEPSEEK_API_KEY` | Summaries, notes, and chat via DeepSeek |

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

### Home

The dashboard. Shows a live focus timer, a count of this month's recordings, and today's upcoming calendar events. Quick-action buttons jump to Recordings, Chat, Calendar, and Focus Time.

---

### Recordings

Control panel for capturing audio.

1. Choose a source: **Mic**, **App** (system audio/calls), or **Both**
2. Optionally select an input device from the dropdown
3. Tag the recording with a **Project** and **Meeting** name before or after recording
4. Hit **Record** — VU meters and a live transcript appear in real-time
5. Hit **Stop** — the WAV file is saved, then the pipeline runs automatically:
   - Whisper API transcription
   - DeepSeek summarization (topics, decisions, action items)
   - DeepSeek bullet notes (action items, key points, decisions)

You can also **import** an existing audio file (WAV, or any format if ffmpeg is installed) to run it through the same transcription and summarization pipeline.

**AI models used:**

| Task | Model |
|---|---|
| Live transcription (during recording) | faster-whisper `small` — runs locally, no API cost |
| Post-recording transcription | OpenAI `whisper-1` |
| Summarization | DeepSeek `deepseek-chat` |
| Notes | DeepSeek `deepseek-chat` |

---

### History

Browse all past recordings as cards. Each card shows the date, duration, project, meeting name, and processing status. Click a card to expand it and view the full transcript, structured summary, and bullet notes. Cards can be filtered by project, date range, or keyword.

A timeline bar at the top shows all recordings grouped by month as colored dots — click any dot to jump to that recording.

---

### Projects

Organize recordings under named projects. Each project has:
- Name, description, status (`Active` / `On Hold` / `Done`)
- Client, objective
- Phases and streams (list tags)
- Color label

Recordings can be linked to a project from the History card or from the Recordings tab before/after a session. Projects also track total recorded time and recording count.

---

### Chat

Ask questions across all your past recordings. The AI searches transcripts and summaries in SQLite and answers in a streaming conversational style.

Examples:
- *"What did we decide about the API redesign last week?"*
- *"Summarize all action items from the Alpha project this month."*
- *"Who was mentioned in yesterday's standup?"*

Chat history persists within a session and resets when the app restarts.

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
    "hub-onenote": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\onenote_server.py"]
    },
    "hub-teams": {
      "command": "C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\p.ledesma.jimenez\\scripts\\SmartRecorder\\mcp\\teams_server.py"]
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
| | | `get_recording` | No | Full detail: transcript, summary, notes, speakers |
| | | `search_recordings` | No | Keyword search across transcripts and summaries |
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
│   ├── db.py                   SQLite — recordings, projects, schema migrations
│   ├── focus_db.py             SQLite — Focus Time sessions
│   ├── recorder.py             Audio capture (PyAudioWPatch, WASAPI loopback)
│   ├── transcriber.py          Post-recording Whisper API transcription
│   ├── live_transcriber.py     Real-time faster-whisper (every 5s)
│   ├── outlook.py              Outlook COM integration
│   ├── onenote.py              OneNote COM integration
│   ├── teams.py                Teams Graph API + Windows WAM auth
│   ├── jiggler.py              Mouse jiggler utility
│   ├── agents/
│   │   ├── base.py             BaseAgent — tool-calling loop + streaming
│   │   ├── agent_notes.py      NotesAgent (DeepSeek, tool: guardar_notas)
│   │   └── agent_orchestrator.py  ChatAgent (buscar_grabaciones tool)
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
    ├── onenote_server.py       MCP — OneNote hierarchy and page ops (COM)
    ├── teams_server.py         MCP — Teams chats, messages, presence (Graph API)
    ├── hub_server.py           MCP — hub meta: README, dev context, service catalogue
    └── tools.md                Tool catalogue and candidate tools
```
