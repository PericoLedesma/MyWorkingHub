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

    def run_stream_async(self, messages: list[dict], tool_handlers: dict, on_token, on_done) -> None:
        """Like run_async but streams content tokens to on_token(str) as they arrive.

        on_done(full_text | None) is called once the response (including any tool
        call round-trips) is complete.  Tool-call turns are still done
        non-streaming so tool results can feed back into the next completion.
        """
        threading.Thread(
            target=self._run_stream_loop,
            args=(list(messages), tool_handlers, on_token, on_done),
            daemon=True,
        ).start()

    def _run_stream_loop(
        self,
        messages: list[dict],
        tool_handlers: dict,
        on_token,
        on_done,
    ) -> None:
        try:
            while True:
                kwargs: dict = {"model": self.model, "messages": messages, "stream": True}
                if self.tools:
                    kwargs["tools"] = self.tools
                    kwargs["tool_choice"] = "auto"

                stream = self._client.chat.completions.create(**kwargs)

                content_parts: list[str] = []
                tool_calls_acc: dict[int, dict] = {}

                for chunk in stream:
                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta.content:
                        content_parts.append(delta.content)
                        on_token(delta.content)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function and tc.function.name:
                                tool_calls_acc[idx]["name"] += tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments

                if tool_calls_acc:
                    tool_calls = [
                        {
                            "id": tool_calls_acc[i]["id"],
                            "type": "function",
                            "function": {
                                "name": tool_calls_acc[i]["name"],
                                "arguments": tool_calls_acc[i]["arguments"],
                            },
                        }
                        for i in sorted(tool_calls_acc)
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": "".join(content_parts) or None,
                        "tool_calls": tool_calls,
                    })
                    for tc in tool_calls:
                        name = tc["function"]["name"]
                        args = json.loads(tc["function"]["arguments"])
                        handler = tool_handlers.get(name)
                        result = handler(**args) if handler else f"tool {name!r} not available"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        })
                else:
                    on_done("".join(content_parts))
                    return

        except Exception:
            log.exception("Agent stream loop failed (model=%s)", self.model)
            on_done(None)
