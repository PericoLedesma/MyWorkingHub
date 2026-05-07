import os
import threading

from openai import OpenAI


class ChatAgent:
    """Sends chat messages to the DeepSeek API asynchronously."""

    def __init__(self):
        self._client = OpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        )

    def send_async(self, messages: list[dict], on_response):
        """Send messages in a background thread; calls on_response(text) when done."""
        threading.Thread(target=self._send, args=(messages, on_response), daemon=True).start()

    def _send(self, messages: list[dict], on_response):
        """Call the chat API; passes None to on_response on failure."""
        try:
            resp = self._client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
            )
            on_response(resp.choices[0].message.content)
        except Exception:
            on_response(None)
