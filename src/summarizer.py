import os
import threading

from openai import OpenAI

from . import db

_SYSTEM_PROMPT = (
    "Eres un asistente de reuniones. Dado un transcripto, produce un resumen conciso "
    "en español con puntos cubriendo: temas clave discutidos, decisiones tomadas y puntos de acción. "
    "Usa texto plano — sin encabezados markdown, solo viñetas que empiecen con '•'."
)


class Summarizer:
    """Generates a summary for a transcript using the DeepSeek API."""

    def __init__(self):
        self._client = OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        )

    def summarize_async(self, filepath: str, transcript: str, on_done) -> None:
        """Kick off summarization in a background thread.

        Calls on_done(filepath, summary_text) on success or on_done(filepath, None) on failure.
        """
        threading.Thread(
            target=self._summarize,
            args=(filepath, transcript, on_done),
            daemon=True,
        ).start()

    def _summarize(self, filepath: str, transcript: str, on_done) -> None:
        try:
            resp = self._client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Transcript:\n{transcript}"},
                ],
            )
            summary = resp.choices[0].message.content
            db.save_summary(filepath, summary)
            on_done(filepath, summary)
        except Exception:
            on_done(filepath, None)
