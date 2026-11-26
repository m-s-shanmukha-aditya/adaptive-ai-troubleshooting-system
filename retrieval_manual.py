
from app.services.blueprint_loader import load_blueprints
from app.retrieval.semantic_retriever import SemanticRetriever

blueprints = load_blueprints()
retriever = SemanticRetriever(blueprints)

queries = [
    "My Spring Boot application cannot connect to PostgreSQL.",
    "I am getting a Git merge conflict while merging branches.",
    "My Python application has a missing module error.",
    "PostgreSQL connection refused error in Java.",
]

for query in queries:
    results = retriever.retrieve(query, top_k=1)

    print(f"\nQuery: {query}")

    if results:
        blueprint, score = results[0]
        print(f"Retrieved: {blueprint.title}")
        print(f"Similarity: {score:.4f}")
    else:
        print("No blueprint found.")
