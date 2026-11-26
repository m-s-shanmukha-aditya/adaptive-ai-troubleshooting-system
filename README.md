# What it is

A Python/FastAPI prototype that analyzes technical issue reports and retrieves an active, approved troubleshooting blueprint with sentence-transformer embeddings and FAISS. It asks a local Ollama model for a structured diagnostic candidate, allows bounded local repairs, and can call a cloud model when local validation fails.

# Status

work in progress
The two blueprints in data/blueprints/ are drafts and are not loaded at runtime. Until a blueprint is approved and active, /troubleshoot returns an insufficient-knowledge response.

# How it works

The `/troubleshoot` endpoint sends the issue through deterministic issue analysis, retrieves relevant blueprints, and prompts Ollama to generate a structured candidate. The runtime validates the candidate, retries local generation up to two times to repair validation defects, and may then use OpenRouter; successful candidates are returned and appended to the runtime experience JSONL file.

# Requirements

- Python 3.12.8 (the version in the inspected virtual environment).
- Ollama installed and running, with Mistral available: `ollama pull mistral`.
- Cloud fallback requires an OpenRouter key; local generation does not.

# Setup

From the project root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If you do not already have a local environment file, copy `.env.example` to `.env`. Set the key there only if cloud fallback is needed.

# Run

From the project root:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

# Run tests

From the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
```

# Run the evaluation

From the project root:

```powershell
python -m app.controlled_evaluation --output evaluation/my_run.json
```
Without --output, results are written to evaluation/controlled_results.json, which is tracked in git.

# Project layout

- `app/`: FastAPI app, troubleshooting orchestration, retrieval, models, and services.
- `data/blueprints/`: troubleshooting blueprint JSON files.
- `evaluation/`: saved evaluation results and a generalization probe script.
- `tests/`: unit and behavior tests.

# Limitations

Validation checks structure, IDs and rule-based consistency, not causal correctness. The unsafe-action filter is a short phrase list. Evaluation uses five fixed scenarios and results are in `evaluation/`. Cloud fallback sends the prompt to OpenRouter and needs a key.
