# SmartRecorder

Windows GUI app that records microphone, system/app audio (WASAPI loopback), or both — and auto-transcribes via OpenAI Whisper.

## Features

- Record from mic, system audio, or both simultaneously
- Auto-transcription via OpenAI Whisper (runs in background after each recording)
- View transcripts inline with the 📄 button
- Real-time VU meter with color feedback
- Recordings saved as WAV + `.txt` transcript in `recordings/`
- SQLite metadata (source, duration, sample rate)
- In-app playback (▶) and delete (✕)

## Requirements

- Windows (WASAPI loopback is Windows-only)
- Python 3.11+
- `OPENAI_API_KEY` env var (for transcription)

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install customtkinter sounddevice soundfile numpy pyaudiowpatch openai
```

Set your OpenAI key:

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

## Usage

```bash
python main.py
```

Toggle **Microphone** / **App Audio**, click **Record**, click **Stop**. Transcription starts automatically; click 📄 on any row to read it.

## Project Structure

```
SmartRecorder/
├── main.py              # Entry point
├── src/
│   ├── app.py           # GUI (customtkinter)
│   ├── recorder.py      # Audio capture (mic, loopback, mixed)
│   ├── transcriber.py   # OpenAI Whisper transcription (async)
│   └── db.py            # SQLite metadata layer
├── recordings/          # WAV + .txt output (gitignored)
├── smart_recorder.db    # Recording metadata (gitignored)
└── .venv/               # Virtual environment (gitignored)
```
