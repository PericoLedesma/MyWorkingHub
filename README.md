# SmartRecorder

A Windows GUI app that captures microphone input, system/app audio (via WASAPI loopback), or both simultaneously.

## Features

- Record from microphone, system audio output, or mix both together
- Real-time VU meter
- Recordings saved as WAV files in `recordings/`
- Metadata (source, duration, sample rate) stored in a SQLite database
- In-app playback with the ▶ button

## Requirements

- Windows (WASAPI loopback capture is Windows-only)
- Python 3.11+

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install customtkinter sounddevice soundfile numpy pyaudiowpatch
```

## Usage

```bash
python main.py
```

Toggle **Microphone** and/or **App Audio** sources, then click **Record**. Click **Stop** when done. Recordings appear in the list below with their source icon and duration.

## Project Structure

```
SmartRecorder/
├── main.py              # Entry point
├── src/
│   ├── app.py           # GUI (customtkinter)
│   ├── recorder.py      # Audio capture (mic, loopback, mixed)
│   └── db.py            # SQLite metadata layer
├── recordings/          # WAV output (gitignored)
├── smart_recorder.db    # Recording metadata (gitignored)
└── .venv/               # Virtual environment (gitignored)
```
