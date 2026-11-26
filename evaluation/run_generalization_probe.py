"""Run a small, explicitly non-generalizable Ollama probe outside the PG fixtures."""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.blueprint_loader import load_blueprints
from app.services.issue_analyzer import IssueAnalyzer
from app.services.local_llm import LocalLLM
from app.services.solution_evaluator import SolutionEvaluator
from app.services.troubleshooter import Troubleshooter


def main():
    issue = "Our public endpoint returns error: no healthy upstream targets are registered."
    blueprint = next(item for item in load_blueprints() if item.category == "web_infrastructure")
    context = IssueAnalyzer().analyze(issue)
    prompt = Troubleshooter._candidate_prompt(issue, context, blueprint)
    candidate = LocalLLM("mistral").generate_response(prompt)
    checked = SolutionEvaluator().evaluate_runtime(candidate, context.observations, blueprint)
    parsed = SolutionEvaluator().parse(candidate)
    result = {
        "evaluation_kind": "single_out_of_domain_probe",
        "model": "mistral",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scenario": issue,
        "domain": blueprint.category,
        "observation_ids": sorted(context.observations),
        "candidate": candidate,
        "runtime_validation_passed": not checked.repair_reasons,
        "runtime_validation_reasons": checked.repair_reasons,
        "expected_diagnosis_match": parsed is not None and parsed.diagnosis.id == "gateway_no_healthy_upstream",
        "evidence_grounding_ids": [item.observation_id for item in parsed.evidence] if parsed else [],
        "actions": [item.action_id for item in parsed.recommended_actions] if parsed else [],
        "uncertainty_status": parsed.diagnosis.status if parsed else None,
        "clarification_questions": parsed.follow_up_questions if parsed else [],
        "limitations": ["one authored probe", "expected answer is a human-authored label", "not a held-out benchmark", "runtime validation does not prove resolution"],
    }
    target = Path(__file__).with_name("generalization_probe.json")
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("domain", "runtime_validation_passed", "runtime_validation_reasons", "expected_diagnosis_match", "evidence_grounding_ids", "actions")}, indent=2))


if __name__ == "__main__":
    main()
