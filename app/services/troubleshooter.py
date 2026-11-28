
import json
import logging
import os
import re

from app.models.generated_response import (
    GeneratedTroubleshootingResponse,
)
from app.services.blueprint_adherence_validator import (
    BlueprintAdherenceValidator,
)
from app.services.blueprint_loader import load_blueprints
from app.retrieval.semantic_retriever import SemanticRetriever
from app.services.local_llm import LocalLLM
from app.services.cloud_llm import CloudLLM
from app.models.experience import IssueContext, StructuredCandidate
from app.services.issue_analyzer import IssueAnalyzer
from app.services.test_case_generator import TestCaseGenerator
from app.services.solution_evaluator import SolutionEvaluator
from app.services.failure_diagnoser import FailureDiagnoser
from app.services.experience_repository import ExperienceRepository
from app.services.candidate_selector import CandidateSelector
from app.services.rule_interpreter import (
    RuleInterpreter,
    interpreter_applicable,
)

logger = logging.getLogger(__name__)


class Troubleshooter:
    MIN_RELEVANCE_SCORE = 0.60

    def __init__(self, max_repairs: int = 2, repository=None):
        blueprints = load_blueprints()
        self.blueprints = blueprints
        # Delay the embedding model load until after clarification gates. This
        # keeps simple/incomplete requests cheap and avoids loading ML stacks
        # for requests that do not enter retrieval.
        self.retriever = None
        self.llm = LocalLLM()
        self.max_repairs = max_repairs
        self.analyzer = IssueAnalyzer()
        self.test_generator = TestCaseGenerator()
        self.evaluator = SolutionEvaluator()
        self.diagnoser = FailureDiagnoser()
        self.selector = CandidateSelector()
        self.repository = repository or ExperienceRepository()

    def troubleshoot(self, issue: str) -> str:
        context = self.analyzer.analyze(issue)
        if context.follow_up_questions:
            return "The available information is insufficient to identify one cause reliably. Please check:\n" + "\n".join(f"- {question}" for question in context.follow_up_questions) + "\nNo diagnosis or resolution has been verified."
        eligible_blueprints = load_blueprints(eligible_only=True)
        if self.retriever is None:
            self.retriever = SemanticRetriever(eligible_blueprints)
        else:
            self.retriever.rebuild(eligible_blueprints)
        results = self.retriever.retrieve(issue, top_k=5)

        if not results:
            return self._insufficient_knowledge_response(context)

        blueprint, score = results[0]

        if score < self.MIN_RELEVANCE_SCORE:
            return self._insufficient_knowledge_response(context)

        blueprint_version = self.repository.version(blueprint)
        related = [item for item, relevance in results[1:] if relevance >= self.MIN_RELEVANCE_SCORE]
        knowledge = blueprint.model_copy(deep=True)
        for item in related:
            knowledge.diagnoses.update(item.diagnoses)
            knowledge.actions.update(item.actions)
        if (
            os.getenv("ENABLE_RULE_OBSERVATIONS", "").strip().lower() == "true"
            and interpreter_applicable(knowledge)
        ):
            interpreted = RuleInterpreter().interpret(issue, knowledge)
            context.observations = {
                observation.id: observation.matched_text
                for observation in interpreted.observations
            }
        # Runtime mode receives issue evidence only. Controlled fixtures and answer keys
        # remain in TestCaseGenerator and are never loaded into this prompt or evaluator.
        prompt = self._candidate_prompt(issue, context, knowledge)
        if related:
            prompt += "\nRELATED KNOWLEDGE (lower ranked; use only when relevant):\n" + "\n".join(
                f"- {item.title}: {item.problem_summary}; symptoms={'; '.join(item.symptoms)}; "
                f"diagnoses={json.dumps(item.diagnoses)}; actions={json.dumps(item.actions)}"
                for item in related
            )
        candidate = ""
        evaluations = []
        try:
            candidate = self.llm.generate_response(prompt)
            logger.info("Local candidate generated chars=%s", len(candidate))
        except Exception:
            logger.exception("Local candidate generation failed")

        for iteration in range(self.max_repairs + 1):
            if not candidate:
                break
            evaluation = self.evaluator.evaluate_runtime(candidate, context.observations, knowledge)
            evaluations.append(evaluation)
            logger.info("Runtime output validation iteration=%s repair_reasons=%s", iteration, evaluation.repair_reasons)
            if not evaluation.repair_reasons:
                break
            if iteration == self.max_repairs:
                break
            parsed_candidate = self.evaluator.parse(candidate)
            allowed_action_ids = (
                self._allowed_action_ids_for_diagnosis(knowledge, parsed_candidate.diagnosis.id)
                if parsed_candidate else []
            )
            repair_prompt = ("Repair only the deterministic output defects listed below. Do not change a diagnosis merely because its correctness is unknown. Return the full JSON candidate.\n"
                             f"ISSUE: {issue}\nAVAILABLE OBSERVATIONS: {json.dumps(context.observations)}\n"
                             f"KNOWLEDGE DIAGNOSES AND ACTION IDs:\n{self._knowledge_id_guidance(knowledge)}\n"
                             f"ALLOWED ACTION IDs FOR SELECTED DIAGNOSIS: {json.dumps(allowed_action_ids)}\n"
                             f"CANDIDATE:\n{candidate}\nREPAIR DEFECTS: {json.dumps(evaluation.repair_reasons)}")
            try:
                candidate = self.llm.generate_response(repair_prompt)
                logger.info("Local repair generated chars=%s", len(candidate))
            except Exception:
                logger.exception("Local repair failed")
                candidate = ""
                break

        valid_local = bool(evaluations and not evaluations[-1].repair_reasons)
        if valid_local:
            selected = evaluations[-1]
            source = "Local"
        else:
            logger.info("Runtime output has deterministic defects; escalating candidate generation to cloud")
            try:
                cloud_response = CloudLLM().generate_response(prompt if not candidate else (
                    f"{prompt}\n\nRepair deterministic defects only: {json.dumps(evaluations[-1].repair_reasons if evaluations else ['empty_candidate'])}\n"
                    f"LOCAL CANDIDATE:\n{candidate}"))
            except Exception as exc:
                logger.exception("Cloud escalation failed")
                failure_reasons = evaluations[-1].repair_reasons if evaluations else ["empty_candidate"]
                return ("No candidate passed runtime validation. "
                        f"Validation issues: {', '.join(failure_reasons)}. "
                        "No diagnosis was validated and no resolution was verified.")
            else:
                cloud_evaluation = self.evaluator.evaluate_runtime(cloud_response, context.observations, knowledge)
                selected = self.selector.select_runtime(evaluations + [cloud_evaluation])
                if selected is None:
                    failure_reasons = sorted({reason for result in evaluations + [cloud_evaluation] for reason in result.repair_reasons})
                    details = ", ".join(failure_reasons) if failure_reasons else "no valid candidate was produced"
                    return (f"No candidate passed runtime validation ({details}). "
                            "Please provide the requested diagnostic information and try again. "
                            "No diagnosis was validated and no resolution was verified.")
                source = "Cloud" if selected is cloud_evaluation else "Local"

        self.repository.save_evaluation(
            mode="runtime", issue=issue, blueprint_version=blueprint_version,
            candidate=selected.candidate, diagnosis_correct=None,
            action_quality=selected.action_quality,
            evidence_consistent=selected.evidence_consistent,
            resolution_verified=False,
            evaluator_version=self.evaluator.EVALUATION_VERSION,
            repair_reasons=selected.repair_reasons,
            diagnostic_status=context.diagnostic_status,
        )
        prefix = f"[{source} candidate selected; runtime diagnosis is not ground-truth verified]"
        return prefix + "\n\n" + selected.candidate + "\n\nDiagnosis correctness: unknown. The database connection has not been verified."

    @staticmethod
    def _validate_generated_response(
        response: str,
        blueprint,
        operating_system: str,
    ) -> GeneratedTroubleshootingResponse | None:

        # Validate environment compatibility.
        if not Troubleshooter.validate_response(
            response, operating_system
        ):
            return None

        # Parse the JSON response into the Pydantic model.
        try:
            generated = GeneratedTroubleshootingResponse.model_validate(
                json.loads(response)
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

        # Validate step count and numbering against the blueprint.
        if not BlueprintAdherenceValidator.validate(
            generated, blueprint
        ):
            return None

        return generated

    @staticmethod
    def _format_response(
        response: GeneratedTroubleshootingResponse,
    ) -> str:

        formatted_steps = []

        for step in response.steps:
            formatted_steps.append(
                f"Step {step.step_number}: {step.instruction}\n"
                f"Expected result: {step.expected_result}"
            )

        steps_text = "\n\n".join(formatted_steps)

        return (
            f"{steps_text}\n\n"
            f"Final Verification:\n{response.final_verification}"
        )

    @staticmethod
    def validate_response(
        response: str,
        operating_system: str,
    ) -> bool:

        if operating_system.lower() == "windows":
            forbidden_commands = [
                r"\bsudo\b",
                r"\bsystemctl\b",
                r"\bapt\b",
            ]

            for command in forbidden_commands:
                if re.search(command, response, re.IGNORECASE):
                    return False

        return True

    @staticmethod
    def _candidate_prompt(issue: str, context: IssueContext, blueprint, scenarios=None) -> str:
        steps = "\n".join(f"{s.step_number}. {s.instruction} Expected: {s.expected_result}" for s in blueprint.steps)
        return ("You are a developer troubleshooting assistant in runtime mode. Use only the user's issue and listed observations. "
                "Observations are extracted claims from the user's report, not independently verified facts. Treat likely_causes as hypotheses only. "
                "Keep a diagnosis uncertain when it is inferred, and ask targeted questions for facts that are missing or ambiguous. "
                "If evidence does not distinguish a cause, use an uncertain or insufficient_evidence status and ask targeted follow-up questions. "
                "Do not report probabilities or claim the issue is resolved. Return exactly one valid JSON object, with no markdown fences or surrounding prose, matching this complete schema: "
                '{"schema_version":"1","diagnosis":{"id":"<one canonical diagnosis ID>","summary":"...","status":"uncertain"},'
                '"evidence":[{"observation_id":"<ID from observations>","interpretation":"..."}],'
                '"recommended_actions":[{"action_id":"<canonical action ID>","description":"..."}],'
                '"resolution_claim":"unverified","follow_up_questions":[]}. Diagnosis status must be one of: '
                "supported, uncertain, or insufficient_evidence. Use only diagnosis/action IDs from supplied knowledge. "
                "Do not invent environment values or ask users to reveal secrets.\n"
                f"KNOWLEDGE DIAGNOSES AND ACTION IDs:\n{Troubleshooter._knowledge_id_guidance(blueprint)}\n"
                f"OBSERVATIONS: {json.dumps(context.observations)}\n"
                f"ISSUE: {issue}\nCONTEXT: {context.model_dump_json()}\nBLUEPRINT: {blueprint.title}\n{steps}\n"
                "Clearly distinguish checks the user must perform from established facts.")

    @staticmethod
    def _canonical_id_guidance() -> str:
        diagnoses = "\n".join(f"- {key}: {meaning}" for key, meaning in SolutionEvaluator.DIAGNOSIS_MEANINGS.items())
        actions = "\n".join(f"- {key}: {meaning}" for key, meaning in SolutionEvaluator.ACTION_MEANINGS.items())
        return f"Diagnoses (choose exactly one):\n{diagnoses}\nActions (choose only applicable IDs):\n{actions}"

    @staticmethod
    def _knowledge_id_guidance(blueprint) -> str:
        if not blueprint.diagnoses and not blueprint.actions:
            return Troubleshooter._canonical_id_guidance()
        diagnoses = "\n".join(f"- {key}: {value.get('meaning', '')}; supporting evidence: {value.get('supporting_evidence', [])}"
                               for key, value in blueprint.diagnoses.items())
        actions = "\n".join(f"- {key}: {value.get('meaning', '')}; relevant diagnoses: {value.get('diagnosis_ids', [])}"
                             for key, value in blueprint.actions.items())
        allowed_actions = "\n".join(
            f"- {diagnosis_id}: {', '.join(Troubleshooter._allowed_action_ids_for_diagnosis(blueprint, diagnosis_id)) or '(none)'}"
            for diagnosis_id in blueprint.diagnoses
        )
        return (f"Diagnoses (choose one):\n{diagnoses}\nActions (choose only relevant IDs):\n{actions}\n"
                f"Allowed action IDs for each diagnosis:\n{allowed_actions}")

    @staticmethod
    def _allowed_action_ids_for_diagnosis(blueprint, diagnosis_id: str) -> list[str]:
        return sorted(
            action_id
            for action_id, definition in blueprint.actions.items()
            if diagnosis_id in definition.get("diagnosis_ids", [])
        )

    @staticmethod
    def _insufficient_knowledge_response(context: IssueContext) -> str:
        """Return a deterministic abstention without invoking either LLM."""
        questions = []
        has_reported_detail = any(key.startswith("reported_detail_") for key in context.observations)
        if not has_reported_detail:
            questions.append(
                "What exact error message or relevant log excerpt do you see? Redact credentials, tokens, and other secrets."
            )
        if not context.environment:
            questions.append(
                "Which application, service, or tool is affected, and what version and platform is it running on?"
            )
        if not context.constraints:
            questions.append(
                "What changed shortly before the issue began, and what steps reproduce it?"
            )
        if not questions:
            questions.append("What result did you expect, and what result did you observe?")

        candidate = StructuredCandidate(
            schema_version="1",
            diagnosis={
                "id": "undetermined",
                "summary": "No relevant approved troubleshooting blueprint was available; the diagnosis is undetermined.",
                "status": "insufficient_evidence",
            },
            resolution_claim="unverified",
            follow_up_questions=questions,
        )
        return candidate.model_dump_json()
