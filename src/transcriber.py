import logging
import math
import os
import tempfile
import threading

import scipy.io.wavfile as wav
from openai import OpenAI

from . import db

log = logging.getLogger(__name__)

MAX_CHUNK_BYTES = 24 * 1024 * 1024


def _split_wav(path: str) -> list[tuple[str, float]]:
    """Split a WAV file into <24 MB temp chunks; returns list of (temp_path, start_sec)."""
    rate, data = wav.read(path)
    bytes_per_sample = data.dtype.itemsize * (data.ndim if data.ndim > 1 else 1)
    samples_per_chunk = MAX_CHUNK_BYTES // bytes_per_sample
    total_samples = data.shape[0]
    n_chunks = math.ceil(total_samples / samples_per_chunk)

    chunks: list[tuple[str, float]] = []
    for i in range(n_chunks):
        start = i * samples_per_chunk
        end = min(start + samples_per_chunk, total_samples)
        chunk_data = data[start:end]
        start_sec = start / rate
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        wav.write(tmp_path, rate, chunk_data)
        chunks.append((tmp_path, start_sec))
    return chunks


class Transcriber:

    def __init__(self):
        self._client = OpenAI()

    def _transcribe_file(self, path: str, offset_sec: float = 0.0) -> tuple[list[str], str]:
        with open(path, "rb") as f:
            resp = self._client.audio.transcriptions.create(
                model="whisper-1", file=f, response_format="verbose_json"
            )
        parts: list[str] = []
        for seg in resp.segments:
            text = seg.text.strip()
            if not text:
                continue
            t = int(seg.start) + int(offset_sec)
            h, rem = divmod(t, 3600)
            m, s = divmod(rem, 60)
            parts.append(f"[{h:02d}:{m:02d}:{s:02d}] {text}")
        return parts, resp.language

    def _transcribe_audio(self, filepath: str) -> tuple[str, str]:
        if os.path.getsize(filepath) <= MAX_CHUNK_BYTES:
            parts, language = self._transcribe_file(filepath)
            return "\n".join(parts), language

        chunks = _split_wav(filepath)
        try:
            all_parts: list[str] = []
            language = "unknown"
            for i, (chunk_path, start_sec) in enumerate(chunks, 1):
                log.info("Transcribing chunk %d/%d", i, len(chunks))
                parts, lang = self._transcribe_file(chunk_path, offset_sec=start_sec)
                all_parts.extend(parts)
                if i == 1:
                    language = lang
            return "\n".join(all_parts), language
        finally:
            for chunk_path, _ in chunks:
                os.unlink(chunk_path)

    def transcribe_async(self, recording_id: int, filepath: str, on_done) -> None:
        threading.Thread(
            target=self._transcribe,
            args=(recording_id, filepath, on_done),
            daemon=True,
        ).start()

    def _transcribe(self, recording_id: int, filepath: str, on_done) -> None:
        try:
            raw, language = self._transcribe_audio(filepath)
            log.info("Detected language: %s  id=%d", language, recording_id)
            db.save_language(recording_id, language)
            db.save_transcript_raw(recording_id, raw)
            db.save_transcript(recording_id, raw)
            on_done(recording_id, raw, language)
        except Exception:
            log.exception("Transcription failed for %s", filepath)
            on_done(recording_id, None, "unknown")
