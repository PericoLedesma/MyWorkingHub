import os

from openai import OpenAI

from .. import db
from .base import BaseAgent


def _build_system_prompt(language: str) -> str:
    if language == "english":
        return (
            "You are a meeting assistant. Analyze the transcript and call `guardar_resumen` "
            "with a structured summary in English using exactly this format — "
            "three ALL-CAPS section headers, each followed by bullet points starting with '•', "
            "sections separated by a blank line. Plain text only, no markdown.\n\n"
            "KEY TOPICS\n"
            "• ...\n\n"
            "DECISIONS\n"
            "• ...\n\n"
            "ACTION ITEMS\n"
            "• ..."
        )
    return (
        "Eres un asistente de reuniones. Analiza el transcripto y llama a `guardar_resumen` "
        "con un resumen estructurado en español usando exactamente este formato — "
        "tres encabezados en MAYÚSCULAS, cada uno seguido de viñetas que empiecen con '•', "
        "secciones separadas por una línea en blanco. Solo texto plano, sin markdown.\n\n"
        "TEMAS CLAVE\n"
        "• ...\n\n"
        "DECISIONES\n"
        "• ...\n\n"
        "PUNTOS DE ACCIÓN\n"
        "• ..."
    )

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "guardar_resumen",
            "description": "Guarda el resumen estructurado de la grabación.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resumen": {
                        "type": "string",
                        "description": "Resumen con secciones en mayúsculas y viñetas.",
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
        super().__init__(client=None)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com",
            )
        return self._client

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
