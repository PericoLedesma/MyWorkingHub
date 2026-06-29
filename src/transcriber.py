import io
import logging
import os
import threading
import urllib.request
import wave
import zipfile

from openai import OpenAI

from . import db

log = logging.getLogger(__name__)

_MAX_BYTES = 24 * 1024 * 1024  # 24 MB — Whisper API hard limit is 25 MB

# Three interchangeable transcription engines:
ENGINE_WHISPERX = "whisperx"  # free, local, fast (CTranslate2 + batching) — DEFAULT
ENGINE_FASTER   = "faster"    # free, local, simpler/slower fallback (no ffmpeg dependency)
ENGINE_API      = "api"       # paid, OpenAI Whisper API

ENGINES = {
    ENGINE_WHISPERX: "Gratis — Local rápido (WhisperX)",
    ENGINE_FASTER:   "Gratis — Local simple (faster-whisper)",
    ENGINE_API:      "Pago — OpenAI API",
}

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG_DIR   = os.path.join(_PROJECT_ROOT, "tools", "ffmpeg")
_FFMPEG_EXE   = os.path.join(_FFMPEG_DIR, "ffmpeg.exe")
_FFMPEG_URL   = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

_faster_model = None    # lazy singleton for faster-whisper
_whisperx_model = None  # lazy singleton for whisperx
_ffmpeg_ready = False


def _ensure_ffmpeg() -> None:
    """Download a portable ffmpeg.exe into tools/ffmpeg/ if missing, and add it to PATH.

    WhisperX shells out to ffmpeg for audio decoding — it's not bundled with pip.
    """
    global _ffmpeg_ready
    if _ffmpeg_ready:
        return
    if not os.path.exists(_FFMPEG_EXE):
        log.info("ffmpeg not found — downloading portable build (one-time, ~110 MB)...")
        os.makedirs(_FFMPEG_DIR, exist_ok=True)
        zip_path = os.path.join(_FFMPEG_DIR, "_ffmpeg.zip")
        urllib.request.urlretrieve(_FFMPEG_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            exe_member = next(n for n in zf.namelist() if n.endswith("bin/ffmpeg.exe"))
            with zf.open(exe_member) as src, open(_FFMPEG_EXE, "wb") as dst:
                dst.write(src.read())
        os.remove(zip_path)
        log.info("ffmpeg downloaded to %s", _FFMPEG_EXE)
    if _FFMPEG_DIR not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    _ffmpeg_ready = True


def _get_faster_model():
    """Lazily load the faster-whisper model (downloads on first use)."""
    global _faster_model
    if _faster_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading faster-whisper model (first run downloads it)...")
        _faster_model = WhisperModel("small", device="cpu", compute_type="int8")
    return _faster_model


def _get_whisperx_model():
    """Lazily load the WhisperX model — 'base' size with int8 quantization and a
    batch size scaled to the CPU's core count gives a good speed/quality balance
    on CPU-only machines (~5x realtime, vs ~2x with default settings)."""
    global _whisperx_model
    if _whisperx_model is None:
        _ensure_ffmpeg()
        import whisperx
        log.info("Loading WhisperX model (first run downloads it)...")
        _whisperx_model = whisperx.load_model("base", "cpu", compute_type="int8")
    return _whisperx_model


def _split_wav(filepath: str) -> list[bytes]:
    """Split a WAV file into chunks of at most _MAX_BYTES.

    Returns a list of in-memory WAV byte blobs (each valid, playable WAV).
    For files under the limit a single-element list is returned.
    """
    size = os.path.getsize(filepath)
    if size <= _MAX_BYTES:
        with open(filepath, "rb") as f:
            return [f.read()]

    chunks: list[bytes] = []
    with wave.open(filepath, "rb") as wf:
        frame_rate   = wf.getframerate()
        sample_width = wf.getsampwidth()
        n_channels   = wf.getnchannels()
        total_frames = wf.getnframes()

        bytes_per_frame = sample_width * n_channels
        frames_per_chunk = _MAX_BYTES // bytes_per_frame

        wf.rewind()
        frames_read = 0
        while frames_read < total_frames:
            to_read = min(frames_per_chunk, total_frames - frames_read)
            raw = wf.readframes(to_read)
            frames_read += to_read

            buf = io.BytesIO()
            with wave.open(buf, "wb") as out:
                out.setnchannels(n_channels)
                out.setsampwidth(sample_width)
                out.setframerate(frame_rate)
                out.writeframes(raw)
            chunks.append(buf.getvalue())

    log.info("Split %s (%.1f MB) into %d chunks", os.path.basename(filepath),
             size / 1024 / 1024, len(chunks))
    return chunks


class Transcriber:
    """Transcribes audio/video files via one of three engines:
    WhisperX (free, local, fast), faster-whisper (free, local, simple),
    or the OpenAI Whisper API (paid). All three auto-detect the spoken
    language — Spanish, English, and ~95 others are supported natively."""

    def __init__(self):
        self._client = OpenAI()  # reads OPENAI_API_KEY from environment; only used for ENGINE_API

    def transcribe_async(self, filepath: str, on_done, engine: str = ENGINE_WHISPERX) -> None:
        """Kick off transcription in a background thread.

        Calls on_done(filepath, text) on success or on_done(filepath, error) on failure.
        """
        threading.Thread(
            target=self._transcribe,
            args=(filepath, on_done, engine),
            daemon=True,
        ).start()

    def _transcribe(self, filepath: str, on_done, engine: str) -> None:
        try:
            if engine == ENGINE_WHISPERX:
                text = self._transcribe_whisperx(filepath)
            elif engine == ENGINE_FASTER:
                text = self._transcribe_faster(filepath)
            else:
                text = self._transcribe_api(filepath)

            db.save_transcript(filepath, text)  # persist before notifying the UI
            on_done(filepath, text)

        except Exception as exc:
            log.exception("Transcription failed for %s (engine=%s)", filepath, engine)
            on_done(filepath, exc)  # pass the real exception so the UI can show it

    def _transcribe_whisperx(self, filepath: str) -> str:
        import whisperx
        model = _get_whisperx_model()
        audio = whisperx.load_audio(filepath)
        result = model.transcribe(audio, batch_size=min(16, os.cpu_count() or 4))
        return " ".join(seg["text"].strip() for seg in result["segments"])

    def _transcribe_faster(self, filepath: str) -> str:
        model = _get_faster_model()
        segments, _info = model.transcribe(filepath)  # language=None → auto-detect
        return " ".join(seg.text.strip() for seg in segments)

    def _transcribe_api(self, filepath: str) -> str:
        ext = os.path.splitext(filepath)[1].lower()

        if ext != ".wav":
            # Video/other formats: Whisper API accepts mp4/mpeg/webm/m4a/mp3 directly,
            # but we can't chunk them the way we chunk raw WAV frames.
            size = os.path.getsize(filepath)
            if size > _MAX_BYTES:
                raise ValueError(
                    f"El archivo pesa {size / 1024 / 1024:.0f} MB; el límite de la API de "
                    f"OpenAI es 25 MB. Usa uno de los motores gratuitos (local) para archivos grandes."
                )
            with open(filepath, "rb") as f:
                result = self._client.audio.transcriptions.create(model="whisper-1", file=f)
            return result.text

        chunks = _split_wav(filepath)
        texts: list[str] = []
        for i, chunk_bytes in enumerate(chunks, 1):
            if len(chunks) > 1:
                log.info("Transcribing chunk %d/%d  (%s)", i, len(chunks),
                         os.path.basename(filepath))
            # Wrap bytes as a named file-like so the OpenAI SDK accepts it
            buf = io.BytesIO(chunk_bytes)
            buf.name = f"chunk_{i}.wav"
            result = self._client.audio.transcriptions.create(model="whisper-1", file=buf)
            texts.append(result.text)
        return " ".join(texts)
