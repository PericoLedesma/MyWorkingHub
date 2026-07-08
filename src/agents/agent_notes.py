import os

from openai import OpenAI

from .. import db
from .base import BaseAgent


def _build_system_prompt(language: str) -> str:
    if language == "english":
        return (
            "You are a meeting assistant. Analyze the transcript and call `guardar_notas` "
            "with 3-7 bullet-point highlights in English using exactly this format — "
            "bullets starting with '•'. Plain text only, no markdown, no section headers.\n\n"
            "Focus on: key decisions made, action items (with owner/deadline if mentioned), "
            "and main topics discussed."
        )
    return (
        "Eres un asistente de reuniones. Analiza el transcripto y llama a `guardar_notas` "
        "con 3-7 puntos destacados en español usando exactamente este formato — "
        "viñetas que empiecen con '•'. Solo texto plano, sin markdown, sin encabezados.\n\n"
        "Enfócate en: decisiones clave tomadas, puntos de acción (con responsable/fecha si se mencionan), "
        "y temas principales tratados."
    )


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "guardar_notas",
            "description": "Guarda las notas con puntos destacados de la grabación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "notas": {
                        "type": "string",
                        "description": "Notas con viñetas de puntos clave.",
                    },
                },
                "required": ["notas"],
            },
        },
    }
]


class NotesAgent(BaseAgent):
    tools = _TOOLS

    def __init__(self):
        super().__init__(client=None)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com",
            )
        return self._client

    def generate_notes_async(self, recording_id: int, transcript: str, language: str, on_done) -> None:
        """Trigger notes generation in a background thread.

        on_done(recording_id, notes_text | None) is called when complete.
        """
        _result: list[str | None] = [None]

        def _guardar_notas(notas: str) -> str:
            db.save_notes(recording_id, notas)
            _result[0] = notas
            return "ok"

        system_prompt = _build_system_prompt(language)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcripto:\n{transcript}"},
        ]
        self.run_async(
            messages,
            tool_handlers={"guardar_notas": _guardar_notas},
            on_done=lambda _: on_done(recording_id, _result[0]),
        )
