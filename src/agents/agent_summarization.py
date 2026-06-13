import os

from openai import OpenAI

from .. import db
from .base import BaseAgent


def _build_system_prompt(language: str) -> str:
    if language == "english":
        return (
            "You are a meeting assistant. Analyze the transcript and call `guardar_resumen` "
            "with a concise summary in English that includes: "
            "key topics discussed, decisions made, and action points. "
            "Use bullet points starting with '•'. Plain text only, no markdown headers."
        )
    return (
        "Eres un asistente de reuniones. Analiza el transcripto y llama a `guardar_resumen` "
        "con un resumen conciso en español que incluya: "
        "temas clave discutidos, decisiones tomadas y puntos de acción. "
        "Usa viñetas que empiecen con '•'. Solo texto plano, sin encabezados markdown."
    )

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "guardar_resumen",
            "description": "Guarda el resumen generado de la grabación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resumen": {
                        "type": "string",
                        "description": "Resumen con viñetas.",
                    },
                },
                "required": ["resumen"],
            },
        },
    }
]


class SummarizationAgent(BaseAgent):
    tools = _TOOLS

    def __init__(self):
        super().__init__(OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        ))

    def summarize_async(self, recording_id: int, transcript: str, language: str, on_done) -> None:
        """Trigger summarization in a background thread.

        on_done(recording_id, resumen_text | None) is called when complete.
        """
        _result: list[str | None] = [None]

        def _guardar_resumen(resumen: str) -> str:
            db.save_summary(recording_id, resumen)
            _result[0] = resumen
            return "ok"

        system_prompt = _build_system_prompt(language)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcripto:\n{transcript}"},
        ]
        self.run_async(
            messages,
            tool_handlers={"guardar_resumen": _guardar_resumen},
            on_done=lambda _: on_done(recording_id, _result[0]),
        )
