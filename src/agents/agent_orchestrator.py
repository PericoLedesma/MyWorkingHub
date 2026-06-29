from .. import db
from .base import BaseAgent, make_llm_client

_SYSTEM = (
    '''
    Rol:Eres el asistente de SmartRecorder, orquestador de varios agentes. 
    SmartRecorder es una aplicación que hace grabaciones que hace transcripciones.
    
    Instrucciones: 
    - Ayuda al usuario respondiendo preguntas en base a la base de conocimiento de grabaciones de reuniones y con acciones a realizar en la aplicación.
    - Responde siempre en español
    - Si no sabes o no puedes hacer la acción, dilo
    '''
)


_TOOLS: list = [
    {
        "type": "function",
        "function": {
            "name": "buscar_grabaciones",
            "description": "Busca en los transcriptos de grabaciones por palabras clave y devuelve fragmentos relevantes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {
                        "type": "string",
                        "description": "Palabra o frase a buscar en los transcriptos.",
                    },
                },
                "required": ["consulta"],
            },
        },
    },
]


class ChatAgent(BaseAgent):
    system_prompt = _SYSTEM
    tools = _TOOLS

    def __init__(self):
        client, model = make_llm_client()
        self.model = model
        super().__init__(client)

    def send_async(self, messages: list[dict], on_response) -> None:
        def _buscar_grabaciones(consulta: str) -> list[dict]:
            return db.search_transcripts(consulta)

        self.run_async(messages, {"buscar_grabaciones": _buscar_grabaciones}, on_response)
