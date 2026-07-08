import logging
import os
import tempfile
import threading

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

LIVE_WHISPER_MODEL = os.environ.get("LIVE_WHISPER_MODEL", "small")
INTERVAL_SEC = 10


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

        drain_fn() -> (chunks: list[np.ndarray], rate: int, channels: int)
        on_segment(timestamp: str, text: str)
        """
        threading.Thread(
            target=self._loop,
            args=(stop_event, drain_fn, on_segment),
            daemon=True,
        ).start()

    def _loop(self, stop_event: threading.Event, drain_fn, on_segment) -> None:
        model = self._get_model()
        total_samples = 0

        while not stop_event.is_set():
            stop_event.wait(timeout=INTERVAL_SEC)

            chunks, rate, channels = drain_fn()
            if not chunks or rate == 0:
                continue

            audio = np.concatenate(chunks).astype(np.float32)
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            else:
                audio = audio.flatten()

            if len(audio) < rate:  # skip if less than 1 second
                continue

            chunk_start_sec = total_samples / rate
            total_samples += len(audio)

            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                sf.write(tmp, audio, rate)
                segments, _ = model.transcribe(tmp, beam_size=5)
                for seg in segments:
                    abs_t = int(chunk_start_sec + seg.start)
                    h, rem = divmod(abs_t, 3600)
                    m, s = divmod(rem, 60)
                    on_segment(f"[{h:02d}:{m:02d}:{s:02d}]", seg.text.strip())
            except Exception:
                log.exception("Live transcription chunk failed")
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
