# SmartRecorder — Claude context

## Stack

- Python 3.11+, Windows only
- GUI: `customtkinter` (dark theme, blue accent)
- Audio: `sounddevice` + `pyaudiowpatch` (WASAPI loopback)
- Transcription: `openai` SDK → `whisper-1` model
- Storage: WAV files + SQLite via `src/db.py`

## Key files

| File | Role |
|---|---|
| `main.py` | Entry point — instantiates `SmartRecorderApp` |
| `src/app.py` | All GUI logic, wires recorder + transcriber |
| `src/recorder.py` | `Recorder` class: mic, loopback, mixed capture |
| `src/transcriber.py` | `Transcriber` class: async Whisper transcription |
| `src/db.py` | `save_metadata` / `load_metadata` / `delete_metadata` |

## Run

```powershell
.venv\Scripts\activate
$env:OPENAI_API_KEY = "sk-..."
python main.py
```

## Architecture notes

- `Recorder` callbacks (`on_done`) are marshalled back to the Tkinter thread via `self.after(0, ...)`.
- `Transcriber.transcribe_async` runs in a daemon thread; same marshal pattern for `on_done`.
- Transcript files live alongside WAV files: `recording_YYYYMMDD_HHMMSS.txt`.
- `OPENAI_API_KEY` must be set in the environment — no `.env` file support.
