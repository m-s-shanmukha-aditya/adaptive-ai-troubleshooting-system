
import os
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DEFAULT_CLOUD_BASE_URL = "https://openrouter.ai/api/v1"


class CloudLLM:
    def __init__(
        self,
        model_name: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        key_env_name = (
            os.getenv("CLOUD_API_KEY_ENV") or "OPENROUTER_API_KEY"
        )
        api_key = os.getenv(key_env_name)

        if not api_key:
            raise ValueError(f"{key_env_name} is not configured.")

        self.model_name = (
            model_name
            or os.getenv("CLOUD_MODEL")
            or os.getenv("OPENROUTER_MODEL")
            or "openrouter/free"
        )
        base_url = os.getenv("CLOUD_BASE_URL", DEFAULT_CLOUD_BASE_URL)
        try:
            parsed_base_url = urlsplit(base_url)
            hostname = parsed_base_url.hostname
            parsed_base_url.port
        except ValueError:
            raise ValueError("CLOUD_BASE_URL must be a valid URL.") from None
        if not hostname or not (
            parsed_base_url.scheme == "https"
            or (
                parsed_base_url.scheme == "http"
                and hostname.lower() == "localhost"
            )
        ):
            raise ValueError(
                "CLOUD_BASE_URL must use https, except for localhost."
            )

        client_options: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
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
