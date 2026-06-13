# SmartRecorder

Windows GUI app that records microphone, system/app audio (WASAPI loopback), or both — auto-transcribes via OpenAI Whisper, auto-summarizes in Spanish via DeepSeek, and includes a chat assistant.

## Features

- Record from mic, system audio, or both simultaneously
- Auto-transcription via OpenAI Whisper (runs in background after each recording)
- Transcript grammar/spelling correction via GPT-4o-mini (applied automatically after Whisper)
- Auto-summarization in Spanish via DeepSeek — triggers automatically after transcription completes
- Summaries cover key topics, decisions, and action items (bullet points)
- Click 📄 on any row to view the summary + full transcript in a single dialog
- Experimental WhisperX transcription with speaker diarization (TX button per row)
- Real-time VU meters with color feedback (green → yellow → red)
- Live recording timer
- Recordings saved as WAV; transcript and summary stored in SQLite
- In-app playback (▶) and delete (✕)
- Inline **Project** and **Meeting** tags per recording (editable, persisted to DB)
- Recordings automatically moved to per-project subfolders when a Project tag is assigned
- Filter recordings by project
- Chat assistant panel (DeepSeek) with conversation history and `buscar_grabaciones` tool to search transcripts
- Activity log panel with timestamps

## Requirements

- Windows (WASAPI loopback is Windows-only)
- Python 3.11+
- `OPENAI_API_KEY` env var (for Whisper transcription)
- `DEEPSEEK_API_KEY` env var (for chat assistant and summaries)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set your API keys:

```powershell
$env:OPENAI_API_KEY   = "sk-..."
$env:DEEPSEEK_API_KEY = "sk-..."
```

Data is stored in `%USERPROFILE%\OneDrive - Accenture\Desktop\MyDB` by default. Override with:

```powershell
$env:SMART_RECORDER_DATA_DIR = "C:\your\preferred\path"
```

## Usage

```powershell
python main.py
```

Toggle **Microphone** / **App Audio**, click **Record**, click **Stop**. Transcription starts automatically, followed by summarization. Click 📄 on any row to read the Spanish summary and full transcript. Use the **Assistant** panel on the right to chat about your recordings.

## Project Structure

```
SmartRecorder/
├── main.py              # Entry point — dark theme, Fusion style
├── requirements.txt
├── src/
│   ├── app.py           # PySide6 GUI — wires all components
│   ├── recorder.py      # Audio capture (mic, loopback, mixed)
│   ├── transcriber.py   # Whisper transcription + GPT-4o-mini correction (async)
│   ├── db.py            # SQLite metadata layer
│   └── agents/
│       ├── base.py               # BaseAgent — tool-calling agentic loop
│       ├── agent_summarization.py # SummarizationAgent — post-transcription summary
│       └── agent_orchestrator.py  # ChatAgent — in-app assistant with buscar_grabaciones tool
└── .venv/               # Virtual environment (gitignored)

# Data directory (outside repo, default: %USERPROFILE%\OneDrive - Accenture\Desktop\MyDB)
MyDB/
├── recordings/          # WAV files (per-project subfolders when tagged)
└── smart_recorder.db    # Recording metadata, transcripts, and summaries
```

## Flow

```
Record → WAV file → Whisper → Transcript → DeepSeek → Summary
                                   ↕
                             Chat searches DB
```

1. **Startup** — `app.py` initializes the Qt UI, loads existing recordings from SQLite, and starts a background audio level monitor.
2. **Record** — `Recorder` captures mic and/or system audio via PyAudio, saves a `.wav` file, and stores metadata in the DB.
3. **Transcribe** *(auto)* — `Transcriber` sends the `.wav` to OpenAI Whisper (`whisper-1`), then runs the raw text through GPT-4o-mini for grammar/spelling correction. Transcript saved to DB.
4. **Summarize** *(auto)* — `SummarizationAgent` sends the transcript to DeepSeek with a `guardar_resumen` tool. DeepSeek calls the tool with its generated summary, which is persisted to DB.
5. **Diarize** *(optional)* — Clicking **TX** runs WhisperX locally to produce a speaker-labeled transcript (`[SPEAKER_00] text...`), stored separately in DB.
6. **Chat** — `ChatAgent` sends user questions to DeepSeek with a `buscar_grabaciones` tool. DeepSeek searches the transcript DB and answers in Spanish.

## Architecture

- All background work (`Recorder`, `Transcriber`, `SummarizationAgent`, `ChatAgent`) runs on daemon threads.
- Callbacks are marshalled safely onto the Qt main thread via PySide6 `Signal`/`emit`.
- Post-recording pipeline: **record → transcribe (Whisper) → summarize (DeepSeek)** — each step triggers the next automatically.
- Agents inherit from `BaseAgent`, which drives a tool-calling loop against the DeepSeek API. `SummarizationAgent` uses a `guardar_resumen` tool to persist summaries; `ChatAgent` uses a `buscar_grabaciones` tool to search recording transcripts and responds in Spanish.
- The `recordings` SQLite table stores `transcript`, `summary`, `project`, and `meeting` per row. Sidecar `.txt` files from older versions are migrated automatically on first run.


## List of ToDo
- Organizar en notas
- Participantes y quien habla que
- Todos mios
- Notas generales del proyecto