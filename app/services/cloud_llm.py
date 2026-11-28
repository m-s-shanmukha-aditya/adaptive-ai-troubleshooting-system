
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class CloudLLM:
    def __init__(
        self,
        model_name: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        api_key = os.getenv("OPENROUTER_API_KEY")

        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured.")

        self.model_name = model_name or os.getenv("OPENROUTER_MODEL") or "openrouter/free"

        client_options: dict[str, Any] = {
            "api_key": api_key,
            "base_url": "https://openrouter.ai/api/v1",
        }
        if timeout is not None:
            client_options["timeout"] = timeout
        if max_retries is not None:
            client_options["max_retries"] = max_retries
        self.client = OpenAI(**client_options)

    def generate_response(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        content = response.choices[0].message.content

        if not content:
            raise RuntimeError("Cloud LLM returned an empty response.")

        return content
