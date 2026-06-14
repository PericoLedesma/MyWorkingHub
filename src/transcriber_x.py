import logging
import os
import threading

from . import db

log = logging.getLogger(__name__)


def _format_diarized(segments: list) -> str:
    """Merge consecutive same-speaker segments into readable labeled blocks."""
    lines = []
    current_speaker = None
    current_texts: list[str] = []

    for seg in segments:
        speaker = seg.get("speaker") or "UNKNOWN"
        text = seg.get("text", "").strip()
        if not text:
            continue
        if speaker != current_speaker:
            if current_texts and current_speaker:
                lines.append(f"[{current_speaker}] {' '.join(current_texts)}")
            current_speaker = speaker
            current_texts = [text]
        else:
            current_texts.append(text)

    if current_texts and current_speaker:
        lines.append(f"[{current_speaker}] {' '.join(current_texts)}")

    return "\n\n".join(lines)


class TranscriberX:
    """Local WhisperX transcription with speaker diarization via pyannote."""

    def transcribe_async(self, recording_id: int, filepath: str, on_done) -> None:
        threading.Thread(
            target=self._transcribe,
            args=(recording_id, filepath, on_done),
            daemon=True,
        ).start()

    def _transcribe(self, recording_id: int, filepath: str, on_done) -> None:
        try:
            os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")  # suppress otel.pyannote.ai telemetry spam
            import torch
            import whisperx

            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "float32"
            hf_token = os.environ.get("HF_TOKEN", "")

            log.info("WhisperX loading model  device=%s  file=%s", device, os.path.basename(filepath))
            model = whisperx.load_model("base", device, compute_type=compute_type)
            audio = whisperx.load_audio(filepath)

            result = model.transcribe(audio, batch_size=16 if device == "cuda" else 4)
            log.info("WhisperX transcribed  language=%s  segments=%d", result.get("language"), len(result.get("segments", [])))

            model_a, metadata = whisperx.load_align_model(
                language_code=result["language"], device=device
            )
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, device,
                return_char_alignments=False,
            )

            if hf_token:
                log.info("WhisperX diarizing  file=%s", os.path.basename(filepath))
                diarize_model = whisperx.diarize.DiarizationPipeline(use_auth_token=hf_token, device=device)
                diarize_segments = diarize_model(audio)
                result = whisperx.assign_word_speakers(diarize_segments, result)
            else:
                log.warning("HF_TOKEN not set — speaker diarization skipped; set HF_TOKEN in your .env")

            text = _format_diarized(result["segments"])
            db.save_transcript_x(recording_id, text)
            on_done(recording_id, text)
        except ImportError:
            log.error("whisperx not installed — run: pip install whisperx")
            on_done(recording_id, None)
        except Exception:
            log.exception("WhisperX transcription failed for %s", filepath)
            on_done(recording_id, None)
