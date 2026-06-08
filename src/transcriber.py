import logging
import threading

from openai import OpenAI

from . import db

log = logging.getLogger(__name__)


class Transcriber:
    """Sends WAV files to OpenAI Whisper for transcription."""

    def __init__(self):
        self._client = OpenAI()  # reads OPENAI_API_KEY from environment

    def transcribe_async(self, filepath: str, on_done) -> None:
        """Kick off transcription in a background thread.

        Calls on_done(filepath, text) on success or on_done(filepath, None) on failure.
        """
        threading.Thread(
            target=self._transcribe,
            args=(filepath, on_done),
            daemon=True,
        ).start()

    def _transcribe(self, filepath: str, on_done) -> None:
        """Call Whisper API, persist the result, and notify on_done."""
        try:
            with open(filepath, "rb") as f:
                result = self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
            text = result.text
            db.save_transcript(filepath, text)  # persist before notifying the UI
            on_done(filepath, text)
        except Exception:
            log.exception("Transcription failed for %s", filepath)
            on_done(filepath, None)
