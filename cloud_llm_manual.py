
from app.services.cloud_llm import CloudLLM

llm = CloudLLM()

response = llm.generate_response(
    "Explain what a PostgreSQL connection refused error means "
    "in two sentences."
)

print("\n--- OpenRouter Response ---\n")
print(response)
