import json
import threading


class BaseAgent:
    system_prompt: str = ""
    tools: list = []

    def __init__(self, client):
        self._client = client
        print("Iniciando agente")

    def run_async(self, messages, tool_handlers, on_done):
        print("Llamada a run_async")
        threading.Thread(target=self._run_loop,
                         args=(list(messages), tool_handlers, on_done),
                         daemon=True).start()

    def _run_loop(self, messages, tool_handlers, on_done):
        print("Llamada a run_loop")
        try:
            while True:
                kwargs = {"model": "deepseek-chat", "messages": messages}
                if self.tools:                       # subclass tools land here
                    kwargs["tools"] = self.tools
                    kwargs["tool_choice"] = "auto"

                msg = self._client.chat.completions.create(**kwargs).choices[0].message

                if not getattr(msg, "tool_calls", None):
                    on_done(msg.content)
                    return

                messages.append(msg)
                for call in msg.tool_calls:
                    args = json.loads(call.function.arguments)
                    result = tool_handlers[call.function.name](**args)
                    messages.append({"role": "tool",
                                     "tool_call_id": call.id,
                                     "content": json.dumps(result)})
        except Exception:
            on_done(None)
