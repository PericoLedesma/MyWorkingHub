from .. import db
from .base import BaseAgent, make_llm_client

_SYSTEM = (
    '''Eres un asistente de reuniones. Analiza el transcripto y llama a guardar_resumen` con un resumen conciso en español que incluya: 
    -temas clave discutidos, decisiones tomadas y puntos de acción. 
    - Usa viñetas que empiecen con '•'. Solo texto plano, sin encabezados markdown.
    '''
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
                        "description": "Resumen con viñetas en español.",
                    },
                },
                "required": ["resumen"],
            },
        },
    }
]


class SummarizationAgent(BaseAgent):
    system_prompt = _SYSTEM
    tools = _TOOLS

    def __init__(self):
        client, model = make_llm_client()
        self.model = model
        super().__init__(client)

    def summarize_async(self, filepath: str, transcript: str, on_done) -> None:
        """Trigger summarization in a background thread.

        on_done(filepath, resumen_text | None) is called when complete.
        """
        _result: list[str | None] = [None]

        def _guardar_resumen(resumen: str) -> str:
            db.save_summary(filepath, resumen)
            _result[0] = resumen
            return "ok"

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"Transcripto:\n{transcript}"},
        ]
        self.run_async(
            messages,
            tool_handlers={"guardar_resumen": _guardar_resumen},
            on_done=lambda _: on_done(filepath, _result[0]),
        )
