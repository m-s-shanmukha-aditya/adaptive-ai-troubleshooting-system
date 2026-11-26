import ollama
from app.models.experience import StructuredCandidate


class LocalLLM:
    def __init__(self, model_name: str = "mistral"):
        self.model_name = model_name

    def generate_response(self, prompt: str) -> str:
        response = ollama.chat(
            model=self.model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            format=StructuredCandidate.model_json_schema(),
            options={"temperature": 0},
        )

        return response["message"]["content"]
