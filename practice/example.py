"""
Run with: python example.py  (needs DEEPSEEK_API_KEY)
"""

import os
import threading
from openai import OpenAI
from base_agent import BaseAgent


class CalculatorAgent(BaseAgent):
    system_prompt = "Eres una calculadora. Usa `sumar` para responder."
    tools = [
        {
            "type": "function",
            "function": {
                "name": "sumar",
                "description": "Suma dos números.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
            },
        }
    ]

    def ask_async(self, question, on_done):
        print("Llamada a ask_async")
        self.run_async(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user",   "content": question},
            ],
            tool_handlers={"sumar": lambda a, b: a + b},
            on_done=on_done,
        )


done = threading.Event()
answer = []

def on_done(text):
    answer.append(text)
    done.set()

agent = CalculatorAgent(OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                               base_url="https://api.deepseek.com"))
agent.ask_async("Cuánto es 7 + 5?", on_done)

done.wait()
print(answer[0])
