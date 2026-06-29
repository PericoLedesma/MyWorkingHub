import json
import logging
import os
import threading

from openai import OpenAI

log = logging.getLogger(__name__)


def make_llm_client() -> tuple[OpenAI, str]:
    """Build an OpenAI-compatible client from whichever provider key is available.

    Some networks block DeepSeek, others don't issue OpenAI credit — so each
    teammate may only have one of the two keys. Preference order:
    DeepSeek first (cheaper), falling back to OpenAI if no DeepSeek key is set.

    Returns (client, model_name).
    """
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if deepseek_key:
        return OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com"), "deepseek-chat"

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return OpenAI(api_key=openai_key), "gpt-4o-mini"

    # Neither key set — let calls fail naturally with a clear auth error.
    return OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com"), "deepseek-chat"


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
