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

CORRECTION_PROMPT = (
    "You are a transcription corrector. "
    "You will receive a raw speech-to-text transcript that may contain errors caused by "
    "poor audio quality, background noise, or similar-sounding words being mistranscribed. "
    "Fix ONLY clear transcription errors: wrong homophones, garbled words, or obvious mishearings. "
    "Do NOT: rephrase, restructure, improve style, fix grammar, add punctuation, "
    "change word order, or alter anything that is not a transcription error. "
    "If a word could be correct, leave it exactly as-is. "
    "The output must be word-for-word identical to the input except for corrected mishearings. "
    "Preserve any [HH:MM:SS] timestamp markers exactly as they appear. "
    "Return only the corrected text, nothing else."
)


def _split_wav(path: str) -> list[tuple[str, float]]:
    """Split a WAV file into temp chunks; return (path, start_seconds) pairs."""
    sample_rate, data = wav.read(path)
    bytes_per_sample = data.dtype.itemsize * (data.shape[1] if data.ndim > 1 else 1)
    max_samples = MAX_CHUNK_BYTES // bytes_per_sample
    num_chunks = math.ceil(len(data) / max_samples)

    log.info("File exceeds 24 MB — splitting into %d chunks", num_chunks)
    chunks = []
    for i in range(num_chunks):
        chunk_data = data[i * max_samples : (i + 1) * max_samples]
        start_sec = (i * max_samples) / sample_rate
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav.write(tmp.name, sample_rate, chunk_data)
        tmp.close()
        chunks.append((tmp.name, start_sec))
    return chunks


class Transcriber:
    """Sends WAV files to OpenAI Whisper for transcription, with chunking and LLM correction."""

    def __init__(self):
        self._client = OpenAI()  # reads OPENAI_API_KEY from environment

    def transcribe_async(self, recording_id: int, filepath: str, on_done) -> None:
        """Kick off transcription in a background thread.

        Calls on_done(recording_id, text) on success or on_done(recording_id, None) on failure.
        """
        threading.Thread(
            target=self._transcribe,
            args=(recording_id, filepath, on_done),
            daemon=True,
        ).start()

    def _transcribe_file(self, path: str) -> tuple[str, str]:
        with open(path, "rb") as f:
            resp = self._client.audio.transcriptions.create(
                model="whisper-1", file=f, response_format="verbose_json"
            )
        return resp.text, resp.language

    def _transcribe_with_chunking(self, filepath: str) -> tuple[str, str]:
        if os.path.getsize(filepath) <= MAX_CHUNK_BYTES:
            return self._transcribe_file(filepath)

        if not filepath.lower().endswith(".wav"):
            raise ValueError("File exceeds 25 MB and is not a WAV — cannot auto-split.")

        chunks = _split_wav(filepath)
        try:
            parts = []
            detected_language = "unknown"
            for i, (chunk_path, start_sec) in enumerate(chunks, 1):
                log.info("Transcribing chunk %d/%d", i, len(chunks))
                text, lang = self._transcribe_file(chunk_path)
                h, rem = divmod(int(start_sec), 3600)
                m, s = divmod(rem, 60)
                parts.append(f"[{h:02d}:{m:02d}:{s:02d}] {text}")
                if i == 1:
                    detected_language = lang
            return "\n\n".join(parts), detected_language
        finally:
            for chunk_path, _ in chunks:
                os.unlink(chunk_path)

    def _correct(self, raw_text: str) -> str:
        response = self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": CORRECTION_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )
        content = response.choices[0].message.content
        return content if content is not None else raw_text

    def _transcribe(self, recording_id: int, filepath: str, on_done) -> None:
        """Transcribe with Whisper (chunked if needed), correct with LLM, persist, and notify."""
        try:
            raw, language = self._transcribe_with_chunking(filepath)
            log.info("Detected language: %s  id=%d", language, recording_id)
            db.save_language(recording_id, language)
            db.save_transcript_raw(recording_id, raw)
            text = self._correct(raw)
            db.save_transcript(recording_id, text)
            on_done(recording_id, text, language)
        except Exception:
            log.exception("Transcription failed for %s", filepath)
            on_done(recording_id, None, "unknown")
