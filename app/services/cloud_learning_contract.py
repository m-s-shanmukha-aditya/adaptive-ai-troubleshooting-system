"""Pure prompt and response-contract helpers for cloud learning."""

from dataclasses import dataclass
import json
from json import JSONDecodeError, JSONDecoder


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
    instructions = """Return JSON only, with exactly one of these shapes:
{"declined": "<reason>"} when the report is too vague to name a specific technical domain; or
{"blueprint": <ProceduralBlueprint JSON>, "answer": {"diagnosis_id": str, "status": "supported"|"uncertain"|"insufficient_evidence", "evidence_ids": [...], "interpretations": {evidence_id: str}, "action_ids": [...], "follow_up_questions": [...]}}.

The report is redacted. Do not infer or reconstruct removed secrets.
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


def parse_output(text: str) -> Declined | Learned | Invalid:
    """Parse one response object from plain, fenced, or prose-wrapped output."""
    if not isinstance(text, str):
        return Invalid(INVALID_RESPONSE)
    if len(text) > MAX_OUTPUT_CHARS:
        return Invalid(OVERSIZE)

    candidates = [text.strip()]
    # A direct decode handles the common case without scanning arbitrary prose.
    for candidate in candidates:
        try:
            document = json.loads(candidate)
        except (JSONDecodeError, RecursionError, ValueError):
            continue
        if isinstance(document, dict):
            return _interpret_document(document)

    for document in _decoded_objects(text):
        return _interpret_document(document)
    return Invalid(INVALID_JSON)


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
