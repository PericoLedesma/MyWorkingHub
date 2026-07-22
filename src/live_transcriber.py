import logging
import os
import tempfile
import threading

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

LIVE_WHISPER_MODEL = os.environ.get("LIVE_WHISPER_MODEL", "small")
INTERVAL_SEC = 20


class LiveTranscriber:
    """Runs faster-whisper on rolling audio chunks during a recording session."""

    def __init__(self):
        self._model = None
        self._model_lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from faster_whisper import WhisperModel
                    log.info("Loading faster-whisper '%s' for live transcription…", LIVE_WHISPER_MODEL)
                    self._model = WhisperModel(LIVE_WHISPER_MODEL, device="auto", compute_type="auto")
                    log.info("faster-whisper live model ready.")
        return self._model

    def start(self, stop_event: threading.Event, drain_fn, on_segment) -> None:
        """Start the polling loop in a daemon thread.

        drain_fn() -> [(label, chunks, rate, channels), ...]
        on_segment(timestamp: str, text: str, speaker: str)
        """
        threading.Thread(
            target=self._loop,
            args=(stop_event, drain_fn, on_segment),
            daemon=True,
        ).start()

    def _loop(self, stop_event: threading.Event, drain_fn, on_segment) -> None:
        log.info("Live transcriber thread started")
        try:
            model = self._get_model()
        except Exception:
            log.exception("Failed to load live transcription model — live transcript disabled")
            return
        log.info("Live transcriber model ready, polling every %ds", INTERVAL_SEC)
        # Per-source sample counters for computing absolute timestamps
        total_samples: dict[str, int] = {}

        while not stop_event.is_set():
            stop_event.wait(timeout=INTERVAL_SEC)

            sources = drain_fn()
            for speaker, chunks, rate, channels in sources:
                log.debug("Drain [%s]: %d chunks, rate=%d, channels=%d", speaker or "?", len(chunks), rate, channels)
                if not chunks or rate == 0:
                    log.debug("No audio to transcribe — skipping")
                    continue

                audio = np.concatenate(chunks).astype(np.float32)
                if channels > 1:
                    audio = audio.reshape(-1, channels).mean(axis=1)
                else:
                    audio = audio.flatten()

                duration_s = len(audio) / rate
                log.debug("Audio chunk: %.1f s (%d samples at %d Hz)", duration_s, len(audio), rate)
                if len(audio) < rate:  # skip if less than 1 second
                    log.debug("Chunk too short — skipping")
                    continue

                key = speaker or "default"
                chunk_start_sec = total_samples.get(key, 0) / rate
                total_samples[key] = total_samples.get(key, 0) + len(audio)

                fd, tmp = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                try:
                    sf.write(tmp, audio, rate)
                    log.info("Transcribing [%s] %.1f s chunk (offset %.0f s)…", speaker or "?", duration_s, chunk_start_sec)
                    segments, info = model.transcribe(tmp, beam_size=5)
                    n = 0
                    for seg in segments:
                        n += 1
                        abs_t = int(chunk_start_sec + seg.start)
                        h, rem = divmod(abs_t, 3600)
                        m, s = divmod(rem, 60)
                        text = seg.text.strip()
                        log.info("  Segment [%s]: [%02d:%02d:%02d] %s", speaker or "?", h, m, s, text)
                        on_segment(f"[{h:02d}:{m:02d}:{s:02d}]", text, speaker)
                    if n == 0:
                        log.info("  No speech detected in [%s] chunk (language=%s)", speaker or "?", getattr(info, 'language', '?'))
                except Exception:
                    log.exception("Live transcription chunk failed")
                finally:
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
        log.info("Live transcriber thread exiting")
