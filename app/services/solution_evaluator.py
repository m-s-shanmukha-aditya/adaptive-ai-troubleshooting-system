import json
import logging
import re

from pydantic import ValidationError

from app.models.experience import CandidateEvaluation, ScenarioTest, StructuredCandidate, TestResult

logger = logging.getLogger(__name__)


class SolutionEvaluator:
    """Shared deterministic validation with isolated controlled/runtime oracles."""

    EVALUATION_VERSION = "domain-grounded-diagnostic-v3"
    DIAGNOSIS_MEANINGS = {
        "postgres_service_stopped": "PostgreSQL service is stopped",
        "datasource_port_incorrect": "Datasource port differs from the PostgreSQL listener port",
        "database_credentials_invalid": "Configured database credentials are rejected",
        "database_name_incorrect": "Datasource names a database that is not the intended/existing database",
        "connection_successful": "Connection succeeds and no configuration fault is indicated",
        "postgres_connection_issue_uncertain": "Available evidence does not identify one cause",
    }
    ACTION_MEANINGS = {
        "start_postgres_service": "Start PostgreSQL if confirmed stopped",
        "verify_service_status": "Check whether the PostgreSQL service is running",
        "align_datasource_port": "Set datasource port to the confirmed listener port",
        "verify_listener_port": "Check which port PostgreSQL listens on and whether it is reachable",
        "verify_credentials_securely": "Check configured username/password without disclosing secrets",
        "rotate_credentials_if_needed": "Rotate credentials through the approved secret-management process if needed",
        "align_database_name": "Correct the datasource database name using confirmed configuration",
        "verify_database_exists": "Check whether the intended database exists on the target server",
        "make_no_configuration_change": "Avoid changes when the connection already succeeds",
        "confirm_application_health": "Confirm application health after checks; do not imply it was already verified",
        "check_host_reachability": "Check reachability of the configured database host",
        "verify_datasource_url": "Inspect JDBC URL host, port, and database name",
        "inspect_database_error": "Inspect the exact PostgreSQL/application error",
        "request_connection_details": "Ask for relevant host/port/database details with credentials redacted",
    }
    DIAGNOSIS_IDS = set(DIAGNOSIS_MEANINGS)
    ACTION_IDS = set(ACTION_MEANINGS)
    UNSAFE_PHRASES = ("disable authentication", "disable firewall", "share password", "send password", "drop database")

    def parse(self, candidate: str) -> StructuredCandidate | None:
        try:
            return StructuredCandidate.model_validate(json.loads(candidate))
        except json.JSONDecodeError as exc:
            logger.warning(
                "Candidate parse failure: invalid_json line=%s column=%s output_chars=%s shape=unparsed",
                exc.lineno, exc.colno, len(candidate) if isinstance(candidate, str) else 0,
            )
            return None
        except ValidationError as exc:
            errors = [
                {"location": ".".join(str(part) for part in item.get("loc", ())), "type": item.get("type")}
                for item in exc.errors(include_input=False, include_context=False, include_url=False)
            ]
            logger.warning(
                "Candidate parse failure: schema_validation errors=%s output_chars=%s shape=%s",
                errors, len(candidate) if isinstance(candidate, str) else 0,
                self._safe_shape(candidate),
            )
            return None
        except TypeError:
            logger.warning("Candidate parse failure: invalid_output_type")
            return None

    @staticmethod
    def _safe_shape(candidate: str) -> dict:
        """Describe JSON structure without logging candidate text or field values."""
        try:
            document = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return {"root": "unparsed"}
        if not isinstance(document, dict):
            return {"root": type(document).__name__}

        allowed = {"schema_version", "diagnosis", "evidence", "recommended_actions", "resolution_claim", "follow_up_questions"}
        shape = {
            "root": "object",
            "known_keys": sorted(set(document).intersection(allowed)),
            "unknown_key_count": len(set(document).difference(allowed)),
        }
        diagnosis = document.get("diagnosis")
        if isinstance(diagnosis, dict):
            diagnosis_fields = {"id", "summary", "status"}
            shape["diagnosis"] = {
                "known_keys": sorted(set(diagnosis).intersection(diagnosis_fields)),
                "unknown_key_count": len(set(diagnosis).difference(diagnosis_fields)),
            }
        else:
            shape["diagnosis"] = type(diagnosis).__name__
        for field in ("evidence", "recommended_actions", "follow_up_questions"):
            value = document.get(field)
            shape[field] = type(value).__name__
        return shape

    def evaluate_controlled(self, candidate: str, scenario: ScenarioTest) -> CandidateEvaluation:
        """Evaluate against exactly one fixture. Lists are intentionally unsupported."""
        if not isinstance(scenario, ScenarioTest):
            raise TypeError("Controlled evaluation requires exactly one ScenarioTest fixture.")
        parsed = self.parse(candidate)
        if parsed is None:
            row = TestResult(test_id=scenario.test_id, passed=None,
                             evidence="Malformed candidate or schema violation.",
                             evaluation_kind="controlled", diagnosis_correct=None)
            return CandidateEvaluation(candidate=candidate, results=[row], score=0,
                                       schema_valid=False, mode="controlled",
                                       repair_reasons=["malformed_or_invalid_schema"])

        diagnosis_ok = parsed.diagnosis.id == scenario.expected_diagnosis_id
        cited = {item.observation_id for item in parsed.evidence}
        evidence_ok = bool(scenario.expected_evidence_ids) and set(scenario.expected_evidence_ids).issubset(cited)
        action_ids = {item.action_id for item in parsed.recommended_actions}
        action_ok = bool(action_ids) and action_ids.issubset(set(scenario.allowed_action_ids)) and self._actions_safe(parsed)
        passed = diagnosis_ok and evidence_ok and action_ok
        row = TestResult(test_id=scenario.test_id, passed=passed,
                         evidence=f"diagnosis_correct={diagnosis_ok}; evidence_consistent={evidence_ok}; action_quality={action_ok}.",
                         evaluation_kind="controlled", diagnosis_correct=diagnosis_ok,
                         evidence_consistent=evidence_ok, action_quality=action_ok,
                         resolution_verified=False)
        return CandidateEvaluation(candidate=candidate, results=[row], score=float(diagnosis_ok),
                                   diagnosis_correct=diagnosis_ok, evidence_consistent=evidence_ok,
                                   action_quality=action_ok, resolution_verified=False,
                                   verified=False, mode="controlled", schema_valid=True)

    def evaluate_runtime(self, candidate: str, available_observations: dict[str, str], blueprint=None) -> CandidateEvaluation:
        """Validate structure, evidence references, and safe action IDs without an answer key."""
        parsed = self.parse(candidate)
        reasons = []
        if parsed is None:
            reasons.append("malformed_or_invalid_schema")
            row = TestResult(test_id="runtime_validation", passed=False,
                             evidence="Malformed candidate or schema violation.",
                             evaluation_kind="runtime", diagnosis_correct=None,
                             action_quality=None, evidence_consistent=False)
            return CandidateEvaluation(candidate=candidate, results=[row], score=0,
                                       diagnosis_correct=None, action_quality=None,
                                       evidence_consistent=False, resolution_verified=False,
                                       verified=False, schema_valid=False, mode="runtime",
                                       repair_reasons=reasons)

        diagnosis_defs = blueprint.diagnoses if blueprint is not None else {}
        action_defs = blueprint.actions if blueprint is not None else {}
        diagnosis_ids = set(diagnosis_defs) or self.DIAGNOSIS_IDS
        action_ids_allowed = set(action_defs) or self.ACTION_IDS
        if parsed.diagnosis.id not in diagnosis_ids:
            reasons.append("unknown_diagnosis_id")
        cited_ids = {item.observation_id for item in parsed.evidence}
        evidence_ok = cited_ids.issubset(set(available_observations))
        if not evidence_ok:
            reasons.append("invalid_evidence_references")
        action_ids = {item.action_id for item in parsed.recommended_actions}
        action_ok = action_ids.issubset(action_ids_allowed) and self._actions_safe(parsed)
        if not action_ok:
            reasons.append("unsafe_or_unknown_action")
        if diagnosis_defs and parsed.diagnosis.id in diagnosis_defs:
            definition = diagnosis_defs[parsed.diagnosis.id]
            required = set(definition.get("supporting_evidence", []))
            if parsed.diagnosis.status == "supported" and (not required or not (required & cited_ids)):
                reasons.append("diagnosis_not_supported_by_cited_observations")
        if action_defs and parsed.diagnosis.id in diagnosis_ids:
            relevant = {key for key, definition in action_defs.items()
                        if parsed.diagnosis.id in definition.get("diagnosis_ids", [])}
            if action_ids and not action_ids.intersection(relevant):
                reasons.append("actions_not_relevant_to_diagnosis")
        if parsed.resolution_claim.lower() not in {"unverified", "not_verified", "unknown"}:
            reasons.append("unsupported_resolution_claim")
        if re.search(r"\b\d{1,3}\s*%|\b\d+(?:\.\d+)?\s*(?:probability|confidence)\b", candidate, re.IGNORECASE):
            reasons.append("quantified_confidence_not_allowed")

        # Diagnosis correctness is unknown in runtime mode, even for a plausible diagnosis.
        row = TestResult(test_id="runtime_validation", passed=not reasons,
                         evidence="Runtime structure/evidence/action checks passed." if not reasons else "; ".join(reasons),
                         evaluation_kind="runtime", diagnosis_correct=None,
                         evidence_consistent=evidence_ok, action_quality=action_ok,
                         resolution_verified=False)
        return CandidateEvaluation(candidate=candidate, results=[row], score=0,
                                   diagnosis_correct=None, evidence_consistent=evidence_ok,
                                   action_quality=action_ok, resolution_verified=False,
                                   verified=False, schema_valid=True, mode="runtime",
                                   repair_reasons=reasons)

    def evaluate(self, candidate: str, scenario: ScenarioTest, environment: dict[str, str] | None = None) -> CandidateEvaluation:
        """Compatibility alias. It deliberately accepts one fixture, never a suite."""
        del environment
        if isinstance(scenario, list):
            if len(scenario) != 1:
                raise ValueError("Controlled evaluation accepts exactly one scenario fixture.")
            scenario = scenario[0]
        return self.evaluate_controlled(candidate, scenario)

    def _actions_safe(self, parsed: StructuredCandidate) -> bool:
        return not any(phrase in (action.description + " " + action.action_id).lower()
                       for action in parsed.recommended_actions for phrase in self.UNSAFE_PHRASES)
