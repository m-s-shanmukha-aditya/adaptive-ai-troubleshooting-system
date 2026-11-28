
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class CloudLLM:
    def __init__(
        self,
        model_name: str | None = None,
        timeout: float | None = None,
    ):
        api_key = os.getenv("OPENROUTER_API_KEY")

        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured.")

        self.model_name = model_name or os.getenv("OPENROUTER_MODEL") or "openrouter/free"

        if timeout is None:
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        else:
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=timeout,
            )

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
