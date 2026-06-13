import math
import os
import tempfile
from pathlib import Path
from typing import Any

import scipy.io.wavfile as wav
from openai import OpenAI

# ── Configuration ────────────────────────────────────────────────────────────
# Path to the audio file you want to transcribe.
# Supported formats: mp3, mp4, mpeg, mpga, m4a, wav, webm
AUDIO_FILE_PATH = r"C:\Users\p.ledesma.jimenez\OneDrive - Accenture\Desktop\Grabaciones Scottish Power\SpeachRedeia.wav"

# System prompt for the LLM correction pass.
# Add domain-specific context here to improve accuracy
# (e.g. "This is a phone call about energy supply contracts in Spain.").
CORRECTION_PROMPT = (
    "You are a transcription editor. "
    "You will receive a raw speech-to-text transcript that may contain errors caused by "
    "poor audio quality, background noise, or similar-sounding words. "
    "Your task is to correct spelling, grammar, and word substitution errors while "
    "preserving the original meaning, structure, and language of the transcript exactly. "
    "Do not add, remove, or paraphrase any information. Return only the corrected text."
)

# The API key is read from the OPENAI_API_KEY environment variable.
# Set it once in PowerShell with:
#   $env:OPENAI_API_KEY = "sk-..."
# Or permanently:
#   [System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")
# ─────────────────────────────────────────────────────────────────────────────

# Whisper API hard limit is 25 MB; we use 24 MB per chunk to stay safely under.
MAX_CHUNK_BYTES = 24 * 1024 * 1024


def split_wav(path: str) -> list[str]:
    """Split a WAV file into temp chunks that each fit under MAX_CHUNK_BYTES."""
    sample_rate, data = wav.read(path)
    bytes_per_sample = data.dtype.itemsize * (data.shape[1] if data.ndim > 1 else 1)
    max_samples = MAX_CHUNK_BYTES // bytes_per_sample
    num_chunks = math.ceil(len(data) / max_samples)

    print(f"  File is too large — splitting into {num_chunks} chunks...")
    chunk_paths = []
    for i in range(num_chunks):
        chunk_data = data[i * max_samples : (i + 1) * max_samples]
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav.write(tmp.name, sample_rate, chunk_data)
        tmp.close()
        chunk_paths.append(tmp.name)
    return chunk_paths


def _transcribe_file(openai_client: OpenAI, path: str) -> str:
    with open(path, "rb") as audio_file:
        result = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    return result.text


def transcribe(openai_client: OpenAI, path: str) -> str:
    """Transcribe an audio file, automatically splitting WAV files that exceed the API limit."""
    if os.path.getsize(path) <= MAX_CHUNK_BYTES:
        return _transcribe_file(openai_client, path)

    # File is too large — split into chunks (WAV only; other formats are usually already compressed)
    if not path.lower().endswith(".wav"):
        raise ValueError(f"File exceeds 25 MB. Convert it to WAV first to enable auto-splitting.")

    chunk_paths = split_wav(path)
    try:
        parts = []
        for i, chunk_path in enumerate(chunk_paths, 1):
            print(f"  Transcribing chunk {i}/{len(chunk_paths)}...")
            parts.append(_transcribe_file(openai_client, chunk_path))
        return " ".join(parts)
    finally:
        for chunk_path in chunk_paths:
            os.unlink(chunk_path)


def correct_transcript(openai_client: OpenAI, raw_text: str) -> str:
    """Use an LLM to fix errors in the raw Whisper transcript."""
    messages: list[Any] = [
        {"role": "system", "content": CORRECTION_PROMPT},
        {"role": "user", "content": raw_text},
    ]
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        stream=False,
        messages=messages,
    )
    content = response.choices[0].message.content
    return content if content is not None else raw_text


def save(text: str, path: Path) -> None:
    path.write_text(text, encoding="utf-8")
    print(f"Saved -> {path}")


if __name__ == "__main__":
    audio_path = Path(AUDIO_FILE_PATH)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Step 1: transcribe with Whisper
    print(f"Transcribing: {audio_path.name}")
    raw = transcribe(client, str(audio_path))
    save(raw, audio_path.with_name(audio_path.stem + "_raw.txt"))

    # Step 2: correct with LLM
    print("Correcting transcript with LLM...")
    corrected = correct_transcript(client, raw)
    save(corrected, audio_path.with_name(audio_path.stem + "_corrected.txt"))

    print("\n-- Raw transcript ------------------------------------------------------")
    print(raw)
    print("\n-- Corrected transcript ------------------------------------------------")
    print(corrected)
