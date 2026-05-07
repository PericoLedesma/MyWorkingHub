# SmartRecorder

Windows GUI app that records microphone, system/app audio (WASAPI loopback), or both — auto-transcribes via OpenAI Whisper, auto-summarizes in Spanish via DeepSeek, and includes a chat assistant.

## Features

- Record from mic, system audio, or both simultaneously
- Auto-transcription via OpenAI Whisper (runs in background after each recording)
- Auto-summarization in Spanish via DeepSeek — triggers automatically after transcription completes
- Summaries cover key topics, decisions, and action items (bullet points)
- Click 📄 on any row to view the summary + full transcript in a single dialog
- Real-time VU meters with color feedback (green → yellow → red)
- Live recording timer
- Recordings saved as WAV; transcript and summary stored in SQLite
- In-app playback (▶) and delete (✕)
- Inline **Project** and **Meeting** tags per recording (editable, persisted to DB)
- Filter recordings by project
- Chat assistant panel (DeepSeek) with conversation history
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
pip install PySide6 sounddevice soundfile numpy pyaudiowpatch openai
```

Set your API keys:

```powershell
$env:OPENAI_API_KEY   = "sk-..."
$env:DEEPSEEK_API_KEY = "sk-..."
```

## Usage

```powershell
python main.py
```

Toggle **Microphone** / **App Audio**, click **Record**, click **Stop**. Transcription starts automatically, followed by summarization. Click 📄 on any row to read the Spanish summary and full transcript. Use the **Assistant** panel on the right to chat about your recordings.

## Project Structure

```
SmartRecorder/
├── main.py              # Entry point
├── src/
│   ├── app.py           # PySide6 GUI — wires all components
│   ├── recorder.py      # Audio capture (mic, loopback, mixed)
│   ├── transcriber.py   # OpenAI Whisper transcription (async)
│   ├── summarizer.py    # DeepSeek summarization in Spanish (async)
│   ├── agent.py         # DeepSeek chat assistant (async)
│   └── db.py            # SQLite metadata layer
├── recordings/          # WAV output (gitignored)
├── smart_recorder.db    # Recording metadata, transcripts, and summaries (gitignored)
└── .venv/               # Virtual environment (gitignored)
```

## Architecture

- All background work (`Recorder`, `Transcriber`, `Summarizer`, `ChatAgent`) runs on daemon threads.
- Callbacks are marshalled safely onto the Qt main thread via PySide6 `Signal`/`emit`.
- Post-recording pipeline: **record → transcribe (Whisper) → summarize (DeepSeek)** — each step triggers the next automatically.
- The `recordings` SQLite table stores `transcript`, `summary`, `project`, and `meeting` per row. Sidecar `.txt` files from older versions are migrated automatically on first run.
