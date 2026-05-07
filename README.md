# SmartRecorder

Windows GUI app that records microphone, system/app audio (WASAPI loopback), or both — auto-transcribes via OpenAI Whisper, and includes a chat assistant powered by DeepSeek.

## Features

- Record from mic, system audio, or both simultaneously
- Auto-transcription via OpenAI Whisper (runs in background after each recording)
- View transcripts inline with the 📄 button
- Real-time VU meters with color feedback (green → yellow → red)
- Live recording timer
- Recordings saved as WAV with transcript stored in SQLite
- In-app playback (▶) and delete (✕)
- Inline **Project** and **Meeting** tags per recording (editable, persisted to DB)
- Chat assistant panel (DeepSeek) with conversation history
- Activity log panel with timestamps

## Requirements

- Windows (WASAPI loopback is Windows-only)
- Python 3.11+
- `OPENAI_API_KEY` env var (for Whisper transcription)
- `DEEPSEEK_API_KEY` env var (for chat assistant)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install customtkinter sounddevice soundfile numpy pyaudiowpatch openai
```

Set your API keys:

```powershell
$env:OPENAI_API_KEY  = "sk-..."
$env:DEEPSEEK_API_KEY = "sk-..."
```

## Usage

```powershell
python main.py
```

Toggle **Microphone** / **App Audio**, click **Record**, click **Stop**. Transcription starts automatically; click 📄 on any row to read it. Use the **Assistant** panel on the right to chat about your recordings.

## Project Structure

```
SmartRecorder/
├── main.py              # Entry point
├── src/
│   ├── app.py           # GUI (customtkinter) — wires all components
│   ├── recorder.py      # Audio capture (mic, loopback, mixed)
│   ├── transcriber.py   # OpenAI Whisper transcription (async)
│   ├── agent.py         # DeepSeek chat assistant (async)
│   └── db.py            # SQLite metadata layer
├── recordings/          # WAV output (gitignored)
├── smart_recorder.db    # Recording metadata + transcripts (gitignored)
└── .venv/               # Virtual environment (gitignored)
```

## Architecture

- `Recorder` callbacks are marshalled back to the Tkinter thread via `self.after(0, ...)`.
- `Transcriber.transcribe_async` and `ChatAgent.send_async` run on daemon threads; same marshal pattern for their `on_done` callbacks.
- Transcripts are stored in the `recordings` SQLite table (column `transcript`); sidecar `.txt` files are migrated automatically on first run.
- The DB also stores `project` and `meeting` tags per row, editable inline in the recordings list.
