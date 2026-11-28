"""Pure prompt and response-contract helpers for cloud learning."""

from dataclasses import dataclass
import json
from json import JSONDecodeError, JSONDecoder
import re
from typing import get_origin

from app.models.blueprint import ProceduralBlueprint
from app.services.redaction import redact


MAX_OUTPUT_CHARS = 60_000

INVALID_JSON = "invalid_json"
DECLINED = "declined"
MISSING_BLUEPRINT = "missing_blueprint"
MISSING_ANSWER = "missing_answer"
OVERSIZE = "oversize"
INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class Declined:
    reason: str


@dataclass(frozen=True)
class Learned:
    blueprint: dict
    answer: dict


@dataclass(frozen=True)
class Invalid:
    reason_code: str


def build_prompt(redacted_report: str, example_blueprint_dict: dict) -> str:
    """Build a single prompt, with instructions before the example and report."""
    required_fields = [
        name
        for name, field in ProceduralBlueprint.model_fields.items()
        if field.is_required()
    ]
    required_checklist = []
    for name in required_fields:
        annotation = ProceduralBlueprint.model_fields[name].annotation
        if annotation is str:
            field_type = "string"
        elif get_origin(annotation) is list:
            field_type = "list"
        else:
            field_type = "JSON value"
        required_checklist.append(f"{name} ({field_type})")
    instructions = """Return JSON only, with exactly one of these shapes:
{"declined": "<reason>"} ONLY when the report names no identifiable technology, product, tool, service, or error message/code. For example, "something is wrong with my database" may be declined. Do not decline reports naming Kubernetes, Docker, a service, or an error code; create a blueprint for them even if details are limited.
{"blueprint": <ProceduralBlueprint JSON>, "answer": {"diagnosis_id": str, "status": "supported"|"uncertain"|"insufficient_evidence", "evidence_ids": [...], "interpretations": {evidence_id: str}, "action_ids": [...], "follow_up_questions": [...]}}.

Every steps item needs an integer step_number, starting at 1.
Every diagnoses.*.supporting_evidence ID must exactly match an observation or comparison rule ID you define.
The top-level JSON must contain both "blueprint" and "answer"; answer.diagnosis_id must be one of blueprint.diagnoses.
Each observation rule's patterns must literally match words from the report.
The report is redacted. Do not infer or reconstruct removed secrets.
blueprint_id may be any short string; the system replaces it with a safe ID.
Required ProceduralBlueprint fields: """ + ", ".join(required_checklist) + """. Strings must be plain JSON strings, and lists must be JSON lists.
The blueprint must describe the CLASS of problem, not only this report. Provide a specific title, rich problem_summary, 4 to 8 symptoms, category, and 4 to 8 useful retrieval tags. Retrieval embeds title, problem_summary, symptoms, category, and tags, so choose tags that help a similar later report match. Include title, domain, and aliases; several distinct diagnoses, including a dedicated uncertain/fallback diagnosis with a summary and evidence requirements; read-only actions; canonical lowercase snake_case IDs; and observation rules that follow the ProceduralBlueprint schema. Ensure supporting_evidence IDs can actually be emitted by the blueprint's observation or comparison rules. When selected, the uncertain/fallback diagnosis must use answer status "uncertain".
Every action ID must start with verify_, inspect_, check_, confirm_, collect_, or request_. Actions must be safe and read-only; do not recommend remediation, configuration changes, or disclosure of credentials.
Do not include fields outside the stated response shape."""
    example = json.dumps(
        example_blueprint_dict,
        ensure_ascii=False,
        sort_keys=True,
    )
    return (
        f"{instructions}\n\n"
        f"EXAMPLE BLUEPRINT:\n{example}\n\n"
        f"REDACTED REPORT:\n{redacted_report}"
    )


def build_repair_prompt(
    redacted_report: str,
    example_blueprint_dict: dict,
    previous_json: str,
    schema_errors: list[str] | tuple[str, ...] = (),
    repair_feedback: str | None = None,
) -> str:
    prompt = build_prompt(redacted_report, example_blueprint_dict)
    if repair_feedback is None:
        errors = "\n".join(f"- {error}" for error in schema_errors[:10])
        repair_instruction = (
            "The previous JSON failed schema validation. Return a corrected response "
            "in the required response shape. Fix these fields:\n"
            f"{errors}"
        )
    else:
        repair_instruction = (
            "The previous response needs repair. Return a corrected response in the "
            "required response shape.\n"
            f"REPAIR FEEDBACK: {repair_feedback}"
        )
    return (
        f"{prompt}\n\n"
        f"{repair_instruction}\n\n"
        f"PREVIOUS JSON:\n{previous_json}"
    )


def _decoded_objects(text: str):
    decoder = JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, index)
        except (JSONDecodeError, RecursionError, ValueError):
            continue
        if isinstance(value, dict):
            yield value


def output_document(text: str) -> dict | None:
    """Extract a top-level response object without retaining surrounding prose."""
    if not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARS:
        return None
    try:
        document = json.loads(text.strip())
    except (JSONDecodeError, RecursionError, ValueError):
        document = None
    if isinstance(document, dict):
        return document
    return next(_decoded_objects(text), None)


def output_keys(text: str) -> list[str]:
    """Return only bounded, syntactically safe response keys."""
    document = output_document(text)
    if document is None:
        return []
    keys = []
    for key in document:
        if (
            isinstance(key, str)
            and re.fullmatch(r"[a-z_]{1,40}", key)
            and redact(key)[0] == key
        ):
            keys.append(key)
            if len(keys) == 10:
                break
    return keys


def parse_output(text: str) -> Declined | Learned | Invalid:
    """Parse one response object from plain, fenced, or prose-wrapped output."""
    if not isinstance(text, str):
        return Invalid(INVALID_RESPONSE)
    if len(text) > MAX_OUTPUT_CHARS:
        return Invalid(OVERSIZE)
    document = output_document(text)
    return _interpret_document(document) if document is not None else Invalid(INVALID_JSON)


def _interpret_document(document: dict) -> Declined | Learned | Invalid:
    has_declined = "declined" in document
    has_blueprint = "blueprint" in document
    has_answer = "answer" in document
    if has_declined:
        reason = document["declined"]
        if has_blueprint or has_answer or not isinstance(reason, str) or not reason.strip():
            return Invalid(INVALID_RESPONSE)
        return Declined(reason.strip())
    if not has_blueprint:
        return Invalid(MISSING_BLUEPRINT)
    if not has_answer:
        return Invalid(MISSING_ANSWER)
    if not isinstance(document["blueprint"], dict) or not isinstance(document["answer"], dict):
        return Invalid(INVALID_RESPONSE)
    return Learned(document["blueprint"], document["answer"])
