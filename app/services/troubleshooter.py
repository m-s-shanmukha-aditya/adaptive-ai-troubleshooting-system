
import json
import logging
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path

from fastapi import BackgroundTasks

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
from app.services import blueprint_loader
from app.services.blueprint_gate import provisional_gate
from app.services.cloud_learning_contract import Declined, Invalid, build_prompt, parse_output
from app.services.learning_checks import check_learned_blueprint
from app.services.redaction import redact, report_hash
from app.services.rule_interpreter import (
    RuleInterpreter,
    interpreter_applicable,
    observations_by_id,
)
from app.services.blueprint_reindex import register_troubleshooter, reindex

logger = logging.getLogger(__name__)

MAX_CLOUD_LEARNING_CALLS_PER_HOUR = 10
LEARNING_CLOUD_DEADLINE_SECONDS = 75
CLOUD_LEARNING_LOG_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "cloud_learning.jsonl"
)
_learning_lock = threading.RLock()
_cloud_learning_calls = deque()
_learning_hashes_in_flight: set[str] = set()
_learning_hashes_saved: set[str] = set()


def _write_learning_log(entry: dict) -> None:
    with _learning_lock:
        try:
            CLOUD_LEARNING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CLOUD_LEARNING_LOG_PATH.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            logger.warning("Cloud learning log write failed")


def _saved_learning_hashes() -> set[str]:
    saved = set()
    if not CLOUD_LEARNING_LOG_PATH.exists():
        return saved
    try:
        with CLOUD_LEARNING_LOG_PATH.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("saved") is True or entry.get("reason_code") in {
                    "saved",
                    "file_exists",
                }:
                    report_digest = entry.get("report_hash")
                    if isinstance(report_digest, str):
                        saved.add(report_digest)
    except OSError:
        logger.warning("Cloud learning log read failed")
    return saved


def _optional_critic_hook(blueprint) -> None:
    """NO-OP hook for an optional critic before provisional persistence."""


class Troubleshooter:
    MIN_RELEVANCE_SCORE = 0.60

    def __init__(self, max_repairs: int = 2, repository=None):
        blueprints = load_blueprints()
        self.blueprints = blueprints
        # Delay the embedding model load until after clarification gates. This
        # keeps simple/incomplete requests cheap and avoids loading ML stacks
        # for requests that do not enter retrieval.
        self.retriever = None
        self._retriever_lock = threading.RLock()
        self.llm = LocalLLM()
        self.max_repairs = max_repairs
        self.analyzer = IssueAnalyzer()
        self.test_generator = TestCaseGenerator()
        self.evaluator = SolutionEvaluator()
        self.diagnoser = FailureDiagnoser()
        self.selector = CandidateSelector()
        self.repository = repository or ExperienceRepository()
        register_troubleshooter(self)

    def replace_retriever(self, blueprints) -> None:
        with self._retriever_lock:
            model_name = (
                getattr(
                    self.retriever,
                    "model_name",
                    "sentence-transformers/all-MiniLM-L6-v2",
                )
                if self.retriever is not None
                else "sentence-transformers/all-MiniLM-L6-v2"
            )
        next_retriever = SemanticRetriever(blueprints, model_name=model_name)
        with self._retriever_lock:
            self.retriever = next_retriever

    def troubleshoot(
        self,
        issue: str,
        background_tasks: BackgroundTasks | None = None,
    ) -> str:
        context = self.analyzer.analyze(issue)
        if context.follow_up_questions:
            return "The available information is insufficient to identify one cause reliably. Please check:\n" + "\n".join(f"- {question}" for question in context.follow_up_questions) + "\nNo diagnosis or resolution has been verified."
        eligible_blueprints = load_blueprints(eligible_only=True)
        with self._retriever_lock:
            if self.retriever is None:
                self.retriever = SemanticRetriever(eligible_blueprints)
                retriever = self.retriever
                rebuild = False
            else:
                retriever = self.retriever
                rebuild = True
        if rebuild:
            retriever.rebuild(eligible_blueprints)
        results = retriever.retrieve(issue, top_k=5)

        if not results:
            return self._learn_on_retrieval_miss(issue, context, background_tasks)

        blueprint, score = results[0]

        if score < self.MIN_RELEVANCE_SCORE:
            return self._learn_on_retrieval_miss(issue, context, background_tasks)

        blueprint_version = self.repository.version(blueprint)
        related = [item for item, relevance in results[1:] if relevance >= self.MIN_RELEVANCE_SCORE]
        knowledge = blueprint.model_copy(deep=True)
        for item in related:
            knowledge.diagnoses.update(item.diagnoses)
            knowledge.actions.update(item.actions)
        if (
            os.getenv("ENABLE_RULE_OBSERVATIONS", "").strip().lower() in {"true", "1"}
            and interpreter_applicable(knowledge)
        ):
            interpreted = RuleInterpreter().interpret(issue, knowledge)
            context.observations = observations_by_id(interpreted)
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
        if blueprint.status.value == "provisional":
            prefix = "[Provisional blueprint: auto-generated, not yet reviewed]\n" + prefix
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

    def _learn_on_retrieval_miss(
        self,
        issue: str,
        context: IssueContext,
        background_tasks: BackgroundTasks | None,
    ) -> str:
        started = time.perf_counter()
        redact_started = time.perf_counter()
        redacted_report, redaction_counts = redact(issue)
        digest = report_hash(redacted_report)
        timings_ms = {
            "redaction": round((time.perf_counter() - redact_started) * 1000, 3),
            "cloud": 0.0,
            "checks": 0.0,
            "background": None,
        }
        blueprint_id = None

        def finish(reason_code: str) -> str:
            entry = {
                "report_hash": digest,
                "reason_code": reason_code,
                "blueprint_id": blueprint_id,
                "saved": reason_code in {"saved", "file_exists", "reindex_error"},
                "redaction_counts": redaction_counts,
                "timings_ms": {
                    **timings_ms,
                    "total": round((time.perf_counter() - started) * 1000, 3),
                },
            }
            _write_learning_log(entry)
            return self._insufficient_knowledge_response(context)

        disabled_reason = next(
            (
                reason
                for flag, reason in (
                    ("ENABLE_CLOUD_LEARNING", "cloud_learning_disabled"),
                    ("ENABLE_PROVISIONAL_BLUEPRINTS", "provisional_blueprints_disabled"),
                    ("ENABLE_RULE_OBSERVATIONS", "rule_observations_disabled"),
                )
                if os.getenv(flag) != "1"
            ),
            None,
        )
        if disabled_reason:
            return finish(disabled_reason)
        if len(issue) < 40:
            return finish("report_too_short")
        if background_tasks is None:
            return finish("background_tasks_unavailable")

        with _learning_lock:
            _learning_hashes_saved.update(_saved_learning_hashes())
            now = time.monotonic()
            while _cloud_learning_calls and now - _cloud_learning_calls[0] >= 3600:
                _cloud_learning_calls.popleft()
            if len(_cloud_learning_calls) >= MAX_CLOUD_LEARNING_CALLS_PER_HOUR:
                gate_reason = "rate_limited"
            elif digest in _learning_hashes_in_flight:
                gate_reason = "hash_in_flight"
            elif digest in _learning_hashes_saved:
                gate_reason = "hash_already_saved"
            else:
                gate_reason = None
                _learning_hashes_in_flight.add(digest)
                _cloud_learning_calls.append(now)
        if gate_reason:
            return finish(gate_reason)

        model_name = os.getenv("OPENROUTER_MODEL") or "openrouter/free"
        try:
            example = next(
                blueprint
                for blueprint in self.blueprints
                if blueprint.blueprint_id == "postgres_connection"
                or blueprint.title == "Spring Boot PostgreSQL Connection Failure"
            )
        except StopIteration:
            _release_learning_hash(digest)
            return finish("example_blueprint_unavailable")

        cloud_started = time.perf_counter()
        cloud_future: Future[str] = Future()
        prompt = build_prompt(redacted_report, example.model_dump(mode="json"))

        def call_learning_cloud() -> None:
            try:
                response = CloudLLM(
                    model_name=model_name,
                    timeout=60,
                    max_retries=0,
                ).generate_response(prompt)
                cloud_future.set_result(response)
            except Exception as exc:
                cloud_future.set_exception(exc)

        threading.Thread(target=call_learning_cloud, daemon=True).start()
        try:
            response_text = cloud_future.result(
                timeout=LEARNING_CLOUD_DEADLINE_SECONDS
            )
            timings_ms["cloud"] = round(
                (time.perf_counter() - cloud_started) * 1000, 3
            )
        except Exception as exc:
            timings_ms["cloud"] = round(
                (time.perf_counter() - cloud_started) * 1000, 3
            )
            _release_learning_hash(digest)
            if isinstance(exc, TimeoutError) or exc.__class__.__name__ in {
                "APITimeoutError",
                "ConnectTimeout",
                "ReadTimeout",
            }:
                return finish("cloud_timeout")
            return finish("cloud_error")

        parsed = parse_output(response_text)
        if isinstance(parsed, Declined):
            _release_learning_hash(digest)
            return finish("declined")
        if isinstance(parsed, Invalid):
            _release_learning_hash(digest)
            return finish(parsed.reason_code)

        check_started = time.perf_counter()
        try:
            existing_ids = {item.blueprint_id for item in self.blueprints}
            existing_ids.update(
                item.blueprint_id for item in blueprint_loader.load_blueprints()
            )
            for directory in (
                blueprint_loader.BLUEPRINT_DIR,
                blueprint_loader.PROVISIONAL_BLUEPRINT_DIR,
                blueprint_loader.REJECTED_BLUEPRINT_DIR,
            ):
                existing_ids.update(path.stem for path in directory.glob("*.json"))
            checked = check_learned_blueprint(
                issue,
                {"blueprint": parsed.blueprint, "answer": parsed.answer},
                model_name,
                existing_ids,
            )
        except Exception:
            timings_ms["checks"] = round(
                (time.perf_counter() - check_started) * 1000, 3
            )
            _release_learning_hash(digest)
            return finish("deterministic_check_error")
        timings_ms["checks"] = round((time.perf_counter() - check_started) * 1000, 3)
        if not checked.ok:
            blueprint_id = (
                checked.blueprint.blueprint_id if checked.blueprint is not None else None
            )
            _release_learning_hash(digest)
            return finish(checked.reason_code or "deterministic_check_failed")

        blueprint = checked.blueprint
        blueprint_id = blueprint.blueprint_id
        blueprint_json = blueprint.model_dump_json()
        if issue in blueprint_json or redacted_report in blueprint_json:
            _release_learning_hash(digest)
            return finish("report_content_detected")

        try:
            background_tasks.add_task(
                self._save_provisional_blueprint,
                blueprint,
                digest,
                redaction_counts,
                timings_ms,
                started,
            )
        except Exception:
            _release_learning_hash(digest)
            return finish("background_schedule_error")

        return (
            "[Cloud answer, unverified; no approved knowledge existed for this issue]"
            f"\n\n{checked.candidate_json}"
            "\n\nDiagnosis correctness: unknown. The database connection has not been verified."
        )

    @staticmethod
    def _save_provisional_blueprint(
        blueprint,
        digest: str,
        redaction_counts: dict,
        timings_ms: dict,
        started: float,
    ) -> None:
        blueprint_id = blueprint.blueprint_id
        reason_code = "background_error"
        background_started = time.perf_counter()
        try:
            if provisional_gate(blueprint):
                reason_code = "provisional_gate_failed"
                return

            _optional_critic_hook(blueprint)
            path = blueprint_loader.PROVISIONAL_BLUEPRINT_DIR / f"{blueprint_id}.json"
            serialized = json.dumps(
                blueprint.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                indent=2,
            )
            with _learning_lock:
                if path.exists():
                    _learning_hashes_saved.add(digest)
                    reason_code = "file_exists"
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with path.open("x", encoding="utf-8") as file:
                            file.write(serialized)
                    except FileExistsError:
                        _learning_hashes_saved.add(digest)
                        reason_code = "file_exists"
                    else:
                        _learning_hashes_saved.add(digest)
                        reason_code = "saved"
            if reason_code == "saved":
                try:
                    reindex()
                except Exception:
                    reason_code = "reindex_error"
        except Exception:
            reason_code = "background_error"
        finally:
            timings_ms["background"] = round(
                (time.perf_counter() - background_started) * 1000, 3
            )
            with _learning_lock:
                _learning_hashes_in_flight.discard(digest)
            _write_learning_log({
                "report_hash": digest,
                "reason_code": reason_code,
                "blueprint_id": blueprint_id,
                "saved": reason_code in {"saved", "file_exists", "reindex_error"},
                "redaction_counts": redaction_counts,
                "timings_ms": {
                    **timings_ms,
                    "total": round((time.perf_counter() - started) * 1000, 3),
                },
            })


def _release_learning_hash(digest: str) -> None:
    with _learning_lock:
        _learning_hashes_in_flight.discard(digest)
