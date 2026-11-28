import hashlib
import json

import pytest

from app.models.blueprint import ProceduralBlueprint
from app.services.blueprint_gate import provisional_gate
from app.services.cloud_learning_contract import (
    Declined,
    Invalid,
    Learned,
    build_prompt,
    parse_output,
)
from app.services.learning_checks import check_learned_blueprint
from app.services.redaction import (
    MAX_REPORT_CHARS,
    REDACTION_PATTERNS,
    redact,
    report_hash,
)
from app.services.rule_interpreter import validate_patterns


def _blueprint_dict(**updates):
    blueprint = {
        "blueprint_id": "redis_connection_refused",
        "title": "Redis connection refused",
        "problem_summary": (
            "A client cannot establish a TCP connection to a Redis endpoint. "
            "This class of problem can result from service availability, endpoint "
            "configuration, or network reachability."
        ),
        "category": "redis",
        "symptoms": [
            "Redis client reports connection refused",
            "Application cannot open a Redis socket",
            "Connection attempts fail before authentication",
            "The configured Redis endpoint may not accept connections",
        ],
        "steps": [
            {
                "step_number": 1,
                "instruction": "Check Redis service status.",
                "expected_result": "Status is reported.",
            }
        ],
        "verification_checks": [
            {
                "description": "Check connection status.",
                "success_condition": "A connection result is available.",
            }
        ],
        "tags": ["redis", "connection refused", "tcp", "service availability"],
        "domain": "redis_connectivity",
        "aliases": ["redis tcp connection"],
        "diagnoses": {
            "redis_connection_refused": {
                "summary": "The Redis endpoint refused a connection.",
                "supporting_evidence": ["redis_refused"],
            }
        },
        "actions": {
            "verify_redis_service": {
                "meaning": "Check whether the Redis service is listening.",
                "diagnosis_ids": ["redis_connection_refused"],
            }
        },
        "observation_rules": [
            {
                "id": "redis_refused",
                "description": "The report states that the Redis connection was refused.",
                "patterns": [r"\bconnection refused\b"],
                "supports_diagnoses": ["redis_connection_refused"],
            }
        ],
    }
    blueprint.update(updates)
    return blueprint


def _answer(**updates):
    answer = {
        "diagnosis_id": "redis_connection_refused",
        "status": "supported",
        "evidence_ids": ["redis_refused"],
        "interpretations": {
            "redis_refused": "The report says the connection was refused."
        },
        "action_ids": ["verify_redis_service"],
        "follow_up_questions": [],
    }
    answer.update(updates)
    return answer


def _learned(blueprint=None, answer=None):
    return {
        "blueprint": blueprint if blueprint is not None else _blueprint_dict(),
        "answer": answer if answer is not None else _answer(),
    }


@pytest.mark.parametrize(
    ("text", "category", "secret"),
    [
        (
            "key=sk-abcdefghijklmnopqrstuvwxyz012345",
            "keys_tokens",
            "sk-abcdefghijklmnopqrstuvwxyz012345",
        ),
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789",
            "keys_tokens",
            "abcdefghijklmnopqrstuvwxyz0123456789",
        ),
        ("AWS AKIA1234567890ABCDEF", "keys_tokens", "AKIA1234567890ABCDEF"),
        (
            "hex 0123456789abcdef0123456789abcdef",
            "keys_tokens",
            "0123456789abcdef0123456789abcdef",
        ),
        (
            "base64 AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
            "keys_tokens",
            "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        ),
        ("password: mySecretPassword", "passwords", "mySecretPassword"),
        ("DB_PASSWORD: short-value", "passwords", "short-value"),
        ("OPENROUTER_API_KEY=short-key", "keys_tokens", "short-key"),
        (
            "postgres://redisuser:redispass@cache.local:6379/0",
            "url_credentials",
            "redisuser:redispass",
        ),
        ("contact admin@example.test", "emails", "admin@example.test"),
        ("cache is at 192.0.2.15", "ips", "192.0.2.15"),
        ("cache is at 2001:db8::1", "ips", "2001:db8::1"),
    ],
)
def test_redact_removes_secret_categories(text, category, secret):
    result, counts = redact(text)
    assert secret not in result
    assert counts[category] == 1


def test_redact_assignment_secret_and_key_value_token():
    result, counts = redact(
        "secret='value hidden'; api_key: abcdefghijklmnopqrstuvwxyz012345"
    )
    assert "value hidden" not in result
    assert "abcdefghijklmnopqrstuvwxyz012345" not in result
    assert result == "secret=[PASSWORD]; api_key: [REDACTED_KEY]"
    assert counts["passwords"] == 1
    assert counts["keys_tokens"] == 1


def test_redact_is_idempotent_and_preserves_harmless_text():
    harmless = "Redis cannot connect: connection refused on localhost."
    assert redact(harmless) == (
        harmless,
        {
            "keys_tokens": 0,
            "passwords": 0,
            "url_credentials": 0,
            "emails": 0,
            "ips": 0,
        },
    )
    first, _ = redact("user@example.test at 192.0.2.9 password=hunter2")
    assert redact(first)[0] == first


def test_redact_caps_input_and_counts_matches():
    result, counts = redact("a " * (MAX_REPORT_CHARS // 2) + "must-not-appear")
    assert len(result) == MAX_REPORT_CHARS
    assert "must-not-appear" not in result
    assert counts == {
        "keys_tokens": 0,
        "passwords": 0,
        "url_credentials": 0,
        "emails": 0,
        "ips": 0,
    }
    repeated, repeated_counts = redact("a@b.test and c@d.test")
    assert repeated_counts["emails"] == 2
    assert repeated.count("[EMAIL]") == 2


def test_redaction_patterns_pass_blueprint_interpreter_safety_checks():
    blueprint = ProceduralBlueprint.model_validate(
        _blueprint_dict(
            observation_rules=[
                {
                    "id": "safe_patterns",
                    "description": "redaction patterns safety check",
                    "patterns": list(REDACTION_PATTERNS),
                }
            ],
            diagnoses={},
            actions={},
        )
    )
    assert validate_patterns(blueprint) == []


def test_report_hash_uses_first_16_sha256_hex_characters():
    text = "redacted report"
    assert report_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_build_prompt_orders_instructions_example_then_redacted_report():
    example = {"title": "Example"}
    prompt = build_prompt("Redis connection refused", example)
    assert prompt.index("Return JSON only") < prompt.index("EXAMPLE BLUEPRINT:")
    assert prompt.index("EXAMPLE BLUEPRINT:") < prompt.index("REDACTED REPORT:")
    assert json.dumps(example, ensure_ascii=False, sort_keys=True) in prompt
    assert "4 to 8 symptoms" in prompt
    assert "4 to 8 useful retrieval tags" in prompt
    assert "report is redacted" in prompt


def test_parse_output_declined_valid_fenced_and_prose_wrapped():
    assert parse_output('{"declined":"too vague"}') == Declined("too vague")
    expected = {
        "blueprint": {"blueprint_id": "redis_blueprint"},
        "answer": {"status": "uncertain"},
    }
    encoded = json.dumps(expected)
    parsed = Learned(expected["blueprint"], expected["answer"])
    assert parse_output(encoded) == parsed
    assert parse_output(f"```json\n{encoded}\n```") == parsed
    assert parse_output(f"Here is the result:\n{encoded}\nThanks.") == parsed


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "invalid_json"),
        ('{"answer":{}}', "missing_blueprint"),
        ('{"blueprint":{}}', "missing_answer"),
        ('{"blueprint":{},"answer":[]}', "invalid_response"),
    ],
)
def test_parse_output_rejects_malformed_contracts(text, reason):
    result = parse_output(text)
    assert isinstance(result, Invalid)
    assert result.reason_code == reason


def test_parse_output_caps_oversize_response():
    assert parse_output(" " * 60_001) == Invalid("oversize")


def test_good_learned_blueprint_passes_checks_end_to_end_and_forces_metadata():
    model_blueprint = _blueprint_dict(
        status="approved",
        active=False,
        source="model",
        generated_by="model supplied",
        generated_on="2000-01-01",
        reviewed_by="someone",
        reviewed_on="2000-01-02",
    )
    result = check_learned_blueprint(
        "Redis connection refused by the endpoint.",
        _learned(model_blueprint),
        "test-model",
        set(),
    )
    assert result.ok
    assert result.reason_code is None
    assert result.blueprint.status.value == "provisional"
    assert result.blueprint.active is True
    assert result.blueprint.source == "cloud"
    assert result.blueprint.generated_by == "test-model"
    assert result.blueprint.reviewed_by is None
    assert result.observations == {"redis_refused": "connection refused"}
    assert json.loads(result.candidate_json)["resolution_claim"] == "unverified"
    assert provisional_gate(result.blueprint) == []


@pytest.mark.parametrize(
    ("blueprint", "answer", "existing_ids", "reason"),
    [
        (
            _blueprint_dict(
                actions={
                    "repair_redis": {
                        "meaning": "Restart Redis.",
                        "diagnosis_ids": ["redis_connection_refused"],
                    }
                }
            ),
            _answer(action_ids=["repair_redis"]),
            set(),
            "action_id_not_prefixed",
        ),
        (
            _blueprint_dict(
                actions={
                    "verify_share_password": {
                        "meaning": "Share password",
                        "diagnosis_ids": ["redis_connection_refused"],
                    }
                }
            ),
            _answer(action_ids=["verify_share_password"]),
            set(),
            "unsafe_action",
        ),
        (
            _blueprint_dict(
                diagnoses={
                    "redis_connection_refused": {
                        "summary": "Refused.",
                        "supporting_evidence": ["redis_auth_failed"],
                    }
                },
                observation_rules=[
                    {
                        "id": "redis_refused",
                        "description": "Refused",
                        "patterns": [r"\bconnection refused\b"],
                        "supports_diagnoses": ["redis_connection_refused"],
                    },
                    {
                        "id": "redis_auth_failed",
                        "description": "Authentication failure",
                        "patterns": [r"\bauthentication failed\b"],
                        "supports_diagnoses": ["redis_connection_refused"],
                    },
                ],
            ),
            _answer(),
            set(),
            "diagnosis_not_supported_by_cited_observations",
        ),
        (
            _blueprint_dict(
                diagnoses={
                    "redis_connection_refused": {
                        "summary": "Refused.",
                        "supporting_evidence": ["missing_rule"],
                    }
                }
            ),
            _answer(),
            set(),
            "interpreter_not_applicable",
        ),
        (_blueprint_dict(), _answer(), {"redis_connection_refused"}, "id_collision"),
        (
            _blueprint_dict(
                observation_rules=[
                    {
                        "id": f"rule_{index}",
                        "description": "A harmless rule",
                        "patterns": [f"never_match_{index}"],
                    }
                    for index in range(51)
                ]
            ),
            _answer(),
            set(),
            "too_many_rules",
        ),
        (
            _blueprint_dict(steps=[{"step_number": 0, "instruction": "Check.", "expected_result": "Done."}]),
            _answer(),
            set(),
            "invalid_schema",
        ),
    ],
)
def test_learned_blueprint_failure_reasons(blueprint, answer, existing_ids, reason):
    result = check_learned_blueprint(
        "Redis connection refused by the endpoint.",
        _learned(blueprint, answer),
        "test-model",
        existing_ids,
    )
    assert not result.ok
    assert result.reason_code == reason


def test_learned_blueprint_rejects_too_many_diagnoses():
    blueprint = _blueprint_dict(
        diagnoses={
            f"diagnosis_{index}": {
                "summary": "Not applicable.",
                "supporting_evidence": ["redis_refused"],
            }
            for index in range(11)
        }
    )
    result = check_learned_blueprint(
        "Redis connection refused.",
        _learned(blueprint, _answer(diagnosis_id="diagnosis_0")),
        "test-model",
        set(),
    )
    assert result.reason_code == "too_many_diagnoses"
