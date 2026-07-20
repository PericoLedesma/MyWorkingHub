#!/usr/bin/env python3
"""
MCP server — Recording Service.

Reads directly from smart_recorder.db; the app does not need to be running.

Tools:
    list_recordings      — browse with date/project/meeting filters
    get_recording        — recording info + transcript + notes
    search_recordings    — keyword search across transcripts and notes
    transcribe_recording — run Whisper transcription on a recording's WAV file
    write_notes          — persist notes you wrote to the database

Register in claude_desktop_config.json:
    { "mcpServers": { "hub-recordings": { "command": "...", "args": ["mcp/recordings_server.py"] } } }
"""

import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP

_DEFAULT_DATA_DIR = r"C:\Users\p.ledesma.jimenez\OneDrive - Accenture\Desktop\MyDB"
DATA_DIR = os.environ.get("SMART_RECORDER_DATA_DIR", _DEFAULT_DATA_DIR)
DB_PATH = os.path.join(DATA_DIR, "smart_recorder.db")

APP_DIR = str(Path(__file__).parent.parent)
APP_URL = "http://127.0.0.1:8765"

mcp = FastMCP("My Working Hub — Recordings")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@mcp.tool()
def list_recordings(
    date_from: str = "",
    date_to: str = "",
    project: str = "",
    meeting: str = "",
    limit: int = 20,
) -> str:
    """List recordings with metadata. Filter by date range (YYYY-MM-DD), project, or meeting name."""
    query = (
        "SELECT id, start_time, end_time, duration, source, project, meeting, language, "
        "(transcript IS NOT NULL) as has_transcript, "
        "(notes IS NOT NULL) as has_notes, "
        "(transcript_speaker IS NOT NULL) as has_speaker "
        "FROM recordings WHERE 1=1"
    )
    params: list = []
    if date_from:
        query += " AND start_time >= ?"
        params.append(date_from)
    if date_to:
        query += " AND start_time <= ?"
        params.append(date_to + "T23:59:59")
    if project:
        query += " AND project LIKE ?"
        params.append(f"%{project}%")
    if meeting:
        query += " AND meeting LIKE ?"
        params.append(f"%{meeting}%")
    query += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    with _conn() as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return "No recordings found matching the given filters."

    return json.dumps([
        {
            "id": r["id"],
            "start_time": r["start_time"],
            "duration_min": round(r["duration"] / 60, 1),
            "source": r["source"],
            "project": r["project"] or "",
            "meeting": r["meeting"] or "",
            "language": r["language"] or "",
            "has_transcript": bool(r["has_transcript"]),
            "has_notes": bool(r["has_notes"]),
            "has_speaker": bool(r["has_speaker"]),
        }
        for r in rows
    ], indent=2)


@mcp.tool()
def get_recording(recording_id: int) -> str:
    """Get full details of a recording including transcript, notes, and speaker data."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, start_time, end_time, duration, source, project, meeting, "
            "language, transcript, notes, transcript_speaker, speaker_names "
            "FROM recordings WHERE id = ?",
            (recording_id,),
        ).fetchone()

    if not row:
        return f"No recording found with id {recording_id}."

    result: dict = {
        "id": row["id"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_min": round(row["duration"] / 60, 1),
        "source": row["source"],
        "project": row["project"] or "",
        "meeting": row["meeting"] or "",
        "language": row["language"] or "",
        "transcript": row["transcript"] or "(not transcribed)",
        "notes": row["notes"] or "(no notes)",
    }
    if row["speaker_names"]:
        result["speaker_names"] = row["speaker_names"]
    if row["transcript_speaker"]:
        result["transcript_speaker"] = row["transcript_speaker"]

    return json.dumps(result, indent=2)


@mcp.tool()
def search_recordings(
    query: str,
    date_from: str = "",
    date_to: str = "",
    include_notes: bool = False,
) -> str:
    """Search recording transcripts for a keyword or phrase. Set include_notes=true to also search notes."""
    if include_notes:
        text_condition = "(transcript LIKE ? OR transcript_raw LIKE ? OR notes LIKE ?)"
        params: list = [f"%{query}%"] * 3
    else:
        text_condition = "(transcript LIKE ? OR transcript_raw LIKE ?)"
        params = [f"%{query}%", f"%{query}%"]

    conditions = [text_condition]
    if date_from:
        conditions.append("start_time >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("start_time <= ?")
        params.append(date_to + "T23:59:59")

    sql = (
        "SELECT id, start_time, duration, project, meeting, transcript "
        "FROM recordings WHERE " + " AND ".join(conditions) +
        " ORDER BY start_time DESC LIMIT 10"
    )

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    if not rows:
        return f'No recordings found containing "{query}".'

    results = []
    for r in rows:
        transcript = r["transcript"] or ""
        idx = transcript.lower().find(query.lower())
        if idx >= 0:
            start = max(0, idx - 100)
            excerpt = "..." + transcript[start: start + 300] + "..."
        else:
            excerpt = (transcript[:300] + "...") if len(transcript) > 300 else transcript

        results.append({
            "id": r["id"],
            "start_time": r["start_time"],
            "duration_min": round(r["duration"] / 60, 1),
            "project": r["project"] or "",
            "meeting": r["meeting"] or "",
            "excerpt": excerpt,
        })

    return json.dumps(results, indent=2)


_MAX_CHUNK_BYTES = 24 * 1024 * 1024
_BUCKET_SEC = 120


def _transcribe_wav(filepath: str) -> tuple[str, str]:
    """Call OpenAI Whisper on filepath; return (formatted_transcript, language)."""
    import scipy.io.wavfile as wav
    from openai import OpenAI

    client = OpenAI()

    def _call(path: str, offset_sec: float = 0.0) -> tuple[list[str], str]:
        with open(path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1", file=f, response_format="verbose_json"
            )
        parts: list[str] = []
        current_bucket = -1
        bucket_texts: list[str] = []
        bucket_label = ""
        for seg in resp.segments:
            t = int(seg.start) + int(offset_sec)
            bucket = t // _BUCKET_SEC
            if bucket != current_bucket:
                if bucket_texts:
                    parts.append(f"{bucket_label} {' '.join(bucket_texts)}")
                current_bucket = bucket
                h, rem = divmod(t, 3600)
                m, s = divmod(rem, 60)
                bucket_label = f"[{h:02d}:{m:02d}:{s:02d}]"
                bucket_texts = [seg.text.strip()]
            else:
                bucket_texts.append(seg.text.strip())
        if bucket_texts:
            parts.append(f"{bucket_label} {' '.join(bucket_texts)}")
        return parts, resp.language

    if os.path.getsize(filepath) <= _MAX_CHUNK_BYTES:
        parts, language = _call(filepath)
        return "\n".join(parts), language

    rate, data = wav.read(filepath)
    bytes_per_sample = data.dtype.itemsize * (data.ndim if data.ndim > 1 else 1)
    samples_per_chunk = _MAX_CHUNK_BYTES // bytes_per_sample
    total_samples = data.shape[0]
    n_chunks = math.ceil(total_samples / samples_per_chunk)

    all_parts: list[str] = []
    language = "unknown"
    tmp_files: list[str] = []
    try:
        for i in range(n_chunks):
            start = i * samples_per_chunk
            end = min(start + samples_per_chunk, total_samples)
            start_sec = start / rate
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            tmp_files.append(tmp)
            wav.write(tmp, rate, data[start:end])
            parts, lang = _call(tmp, offset_sec=start_sec)
            all_parts.extend(parts)
            if i == 0:
                language = lang
    finally:
        for t in tmp_files:
            os.unlink(t)

    return "\n".join(all_parts), language


@mcp.tool()
def transcribe_recording(recording_id: int) -> str:
    """Transcribe a recording's WAV file using OpenAI Whisper and save the result to the database.

    Requires OPENAI_API_KEY. The recording must have an associated WAV file on disk.
    Returns a confirmation with the detected language and transcript character count.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT filepath, transcript FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()

    if not row:
        return f"No recording found with id {recording_id}."
    filepath = row["filepath"]
    if not os.path.exists(filepath):
        return f"WAV file not found on disk: {filepath}"
    if row["transcript"]:
        return (
            f"Recording {recording_id} already has a transcript "
            f"({len(row['transcript'])} chars). Use overwrite=True to re-run."
        )

    transcript, language = _transcribe_wav(filepath)

    with _conn() as conn:
        conn.execute(
            "UPDATE recordings SET transcript = ?, transcript_raw = ?, language = ? WHERE id = ?",
            (transcript, transcript, language, recording_id),
        )

    return (
        f"Transcription complete. Language: {language}. "
        f"Transcript saved ({len(transcript)} chars)."
    )


@mcp.tool()
def write_notes(recording_id: int, notes: str) -> str:
    """Save notes text to the database for a recording.

    Use this after reading a recording's transcript with get_recording and
    producing the notes yourself. The notes are persisted to smart_recorder.db
    and will appear in the app's detail view.
    """
    with _conn() as conn:
        rows_affected = conn.execute(
            "UPDATE recordings SET notes = ? WHERE id = ?",
            (notes, recording_id),
        ).rowcount

    if rows_affected == 0:
        return f"No recording found with id {recording_id}."
    return f"Notes saved for recording {recording_id} ({len(notes)} chars)."


def _app_post(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        f"{APP_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _app_get(path: str) -> dict:
    with urllib.request.urlopen(f"{APP_URL}{path}", timeout=10) as resp:
        return json.loads(resp.read())


@mcp.tool()
def launch_app() -> str:
    """Launch the SmartRecorder Electron desktop app.

    Runs launch-widget.bat in the background. The app takes a few seconds to start.
    After launching, call get_current_recording to confirm it is ready.
    """
    bat = os.path.join(APP_DIR, "launch-widget.bat")
    subprocess.Popen(
        ["cmd", "/c", bat],
        cwd=APP_DIR,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return "App launch initiated. Wait ~10 seconds for the server to start, then call get_current_recording to confirm."


@mcp.tool()
def start_recording(source: str = "mic", device_index: int = -1) -> str:
    """Start a new recording. Requires the app to be running.

    Args:
        source: Audio source — 'mic' (microphone only), 'app' (system audio / loopback),
                or 'both' (mic + system audio). Default: 'mic'.
        device_index: Microphone device index from list_devices. Pass -1 to use the default device.

    Returns a confirmation or error reason. Use get_current_recording to check status.
    """
    payload: dict = {"source": source}
    if device_index >= 0:
        payload["device_index"] = device_index
    try:
        result = _app_post("/api/record/start", payload)
    except urllib.error.URLError as exc:
        return f"Cannot reach app at {APP_URL} — is it running? Error: {exc}"
    if result.get("ok"):
        return f"Recording started (source: {source})."
    return f"Failed to start recording: {result.get('reason', 'unknown error')}"


@mcp.tool()
def get_current_recording() -> str:
    """Get the live recording state from the running app.

    Returns whether a recording is in progress and its start timestamp.
    Requires the app to be running (call launch_app first if needed).
    """
    try:
        status = _app_get("/api/recording/status")
    except urllib.error.URLError as exc:
        return f"Cannot reach app at {APP_URL} — is it running? Error: {exc}"
    if not status.get("recording"):
        return "No recording in progress."
    import datetime
    start_ts = status.get("recording_start")
    if start_ts:
        elapsed = datetime.datetime.now().timestamp() - start_ts
        mins, secs = divmod(int(elapsed), 60)
        return json.dumps({
            "recording": True,
            "recording_start": start_ts,
            "elapsed": f"{mins}m {secs}s",
        }, indent=2)
    return json.dumps(status, indent=2)


if __name__ == "__main__":
    mcp.run()
