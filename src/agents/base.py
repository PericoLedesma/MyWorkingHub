import json
import logging
import threading

from openai import OpenAI

log = logging.getLogger(__name__)


class BaseAgent:
    """Tool-calling agent base. Subclasses declare `system_prompt`, `tools`, and `model`."""

    system_prompt: str = ""
    tools: list = []
    model: str = "deepseek-chat"

    def __init__(self, client: OpenAI):
        self._client = client

    def run_async(self, messages: list[dict], tool_handlers: dict, on_done) -> None:
        """Run the agentic loop in a background daemon thread.

        on_done(text | None) is called with the final assistant text, or None on error.
        """
        threading.Thread(
            target=self._run_loop,
            args=(list(messages), tool_handlers, on_done),
            daemon=True,
        ).start()

    def _run_loop(self, messages: list[dict], tool_handlers: dict, on_done) -> None:
        try:
            while True:
                kwargs: dict = {"model": self.model, "messages": messages}
                if self.tools:
                    kwargs["tools"] = self.tools
                    kwargs["tool_choice"] = "auto"

                resp = self._client.chat.completions.create(**kwargs)
                msg = resp.choices[0].message

                if not getattr(msg, "tool_calls", None):
                    on_done(msg.content)
                    return

                messages.append(msg)

                for call in msg.tool_calls:
                    name = call.function.name
                    args = json.loads(call.function.arguments)
                    handler = tool_handlers.get(name)
                    if handler:
                        result = handler(**args)
                    else:
                        result = f"tool {name!r} not available"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

        except Exception:
            log.exception("Agent loop failed (model=%s)", self.model)
            on_done(None)
