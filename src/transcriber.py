import logging
import os
import threading

from openai import OpenAI

from . import db

log = logging.getLogger(__name__)

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")

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


class Transcriber:
    """Transcribes audio locally with faster-whisper, then corrects with an LLM."""

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()
        self._openai: OpenAI | None = None

    def _get_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel
                    log.info("Loading faster-whisper model '%s'…", WHISPER_MODEL)
                    self._model = WhisperModel(
                        WHISPER_MODEL,
                        device="auto",
                        compute_type="auto",
                    )
                    log.info("faster-whisper model ready.")
        return self._model

    def _get_openai(self) -> OpenAI:
        if self._openai is None:
            self._openai = OpenAI()
        return self._openai

    def transcribe_async(self, recording_id: int, filepath: str, on_done) -> None:
        threading.Thread(
            target=self._transcribe,
            args=(recording_id, filepath, on_done),
            daemon=True,
        ).start()

    def _transcribe_audio(self, filepath: str) -> tuple[str, str]:
        model = self._get_model()
        segments, info = model.transcribe(filepath, beam_size=5)

        parts = []
        for seg in segments:
            h, rem = divmod(int(seg.start), 3600)
            m, s = divmod(rem, 60)
            parts.append(f"[{h:02d}:{m:02d}:{s:02d}] {seg.text.strip()}")

        raw = "\n".join(parts)
        language = info.language
        return raw, language

    def _correct(self, raw_text: str) -> str:
        response = self._get_openai().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": CORRECTION_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        )
        content = response.choices[0].message.content
        return content if content is not None else raw_text

    def _transcribe(self, recording_id: int, filepath: str, on_done) -> None:
        try:
            raw, language = self._transcribe_audio(filepath)
            log.info("Detected language: %s  id=%d", language, recording_id)
            db.save_language(recording_id, language)
            db.save_transcript_raw(recording_id, raw)
            text = self._correct(raw)
            db.save_transcript(recording_id, text)
            on_done(recording_id, text, language)
        except Exception:
            log.exception("Transcription failed for %s", filepath)
            on_done(recording_id, None, "unknown")
