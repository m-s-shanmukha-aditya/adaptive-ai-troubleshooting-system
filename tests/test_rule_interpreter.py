import re

import pytest

from app.models.blueprint import (
    ClarificationRule,
    ComparisonRule,
    ObservationRule,
    ProceduralBlueprint,
)
from app.services import rule_interpreter as rule_interpreter_module
from app.services.rule_interpreter import (
    MAX_MATCHED_TEXT_CHARS,
    MAX_REPORT_CHARS,
    RuleInterpretation,
    RuleInterpreter,
)


def make_blueprint(
    *,
    blueprint_id: str,
    observation_rules: list[ObservationRule] | None = None,
    clarification_rules: list[ClarificationRule] | None = None,
    comparison_rules: list[ComparisonRule] | None = None,
    diagnoses: dict[str, dict] | None = None,
) -> ProceduralBlueprint:
    return ProceduralBlueprint(
        blueprint_id=blueprint_id,
        title=blueprint_id,
        problem_summary="Test blueprint.",
        category="test",
        symptoms=[],
        steps=[
            {
                "step_number": 1,
                "instruction": "Inspect the issue.",
                "expected_result": "The issue is understood.",
            }
        ],
        verification_checks=[
            {
                "description": "Check the outcome.",
                "success_condition": "The issue is resolved.",
            }
        ],
        diagnoses=diagnoses or {},
        observation_rules=observation_rules or [],
        clarification_rules=clarification_rules or [],
        comparison_rules=comparison_rules or [],
    )


def make_observation(
    rule_id: str,
    description: str,
    patterns: list[str],
    *,
    supports_diagnoses: list[str] | None = None,
) -> ObservationRule:
    return ObservationRule(
        id=rule_id,
        description=description,
        patterns=patterns,
        supports_diagnoses=supports_diagnoses or [],
    )


def docker_blueprint() -> ProceduralBlueprint:
    return make_blueprint(
        blueprint_id="docker_port_allocated",
        diagnoses={"port_conflict": {"description": "The host port is occupied."}},
        observation_rules=[
            make_observation(
                "docker_port_allocated",
                "Docker reports that the requested host port is occupied.",
                [r"port is already allocated"],
                supports_diagnoses=["port_conflict"],
            )
        ],
        clarification_rules=[
            ClarificationRule(
                id="ask_docker_port",
                when_missing=["docker_port_allocated"],
                question="Which host port is Docker trying to publish?",
            )
        ],
    )


def nginx_blueprint() -> ProceduralBlueprint:
    return make_blueprint(
        blueprint_id="nginx_502",
        observation_rules=[
            make_observation(
                "nginx_502",
                "The report contains an nginx 502 response.",
                [r"502\s+bad gateway"],
            )
        ],
        clarification_rules=[
            ClarificationRule(
                id="ask_nginx_upstream",
                when_missing=["nginx_502"],
                question="What upstream response appears in the nginx error log?",
            )
        ],
    )


def redis_blueprint() -> ProceduralBlueprint:
    return make_blueprint(
        blueprint_id="redis_connection_refused",
        observation_rules=[
            make_observation(
                "redis_connection_refused",
                "The report says the Redis connection was refused.",
                [r"redis.{0,30}connection refused"],
            )
        ],
        clarification_rules=[
            ClarificationRule(
                id="ask_redis_endpoint",
                when_missing=["redis_connection_refused"],
                question="Which Redis host and port is the client using?",
            )
        ],
    )


def port_comparison_blueprint(
    *,
    op: str = "not_equal",
    numeric: bool = True,
    configured_pattern: str = r"configured port (?P<port>\d+)",
    listener_pattern: str = r"listener port (?P<port>\d+)",
) -> ProceduralBlueprint:
    return make_blueprint(
        blueprint_id="port_comparison",
        observation_rules=[
            make_observation(
                "configured_port",
                "The configured port.",
                [configured_pattern],
            ),
            make_observation(
                "listener_port",
                "The listener port.",
                [listener_pattern],
            ),
        ],
        comparison_rules=[
            ComparisonRule(
                id="port_mismatch",
                description="The configured and listener ports differ.",
                left="configured_port.port",
                right="listener_port.port",
                op=op,
                numeric=numeric,
            )
        ],
    )


@pytest.mark.parametrize(
    ("blueprint", "report", "observation_id"),
    [
        (
            docker_blueprint(),
            "Docker failed to start: port is already allocated.",
            "docker_port_allocated",
        ),
        (
            nginx_blueprint(),
            "nginx returned 502 Bad Gateway.",
            "nginx_502",
        ),
        (
            redis_blueprint(),
            "Redis connection refused by the server.",
            "redis_connection_refused",
        ),
    ],
)
def test_domain_blueprints_interpret_matching_reports(blueprint, report, observation_id):
    result = RuleInterpreter().interpret(report, blueprint)

    assert [observation.id for observation in result.observations] == [observation_id]
    assert result.clarification_questions == []
    assert result.matched_rule_ids == [observation_id]
    assert result.clarification_rule_ids == []


def test_absent_polarity_is_carried_without_changing_rule_match_semantics():
    blueprint = make_blueprint(
        blueprint_id="absent_polarity",
        observation_rules=[
            ObservationRule(
                id="service_not_responding",
                description="The report contains the no-response indicator.",
                patterns=["no response"],
                polarity="absent",
            )
        ],
    )

    result = RuleInterpreter().interpret("The log says no response.", blueprint)

    assert len(result.observations) == 1
    assert result.observations[0].polarity == "absent"


def test_docker_report_does_not_match_nginx_blueprint():
    result = RuleInterpreter().interpret(
        "Docker failed to start: port is already allocated.",
        nginx_blueprint(),
    )

    assert result.observations == []


def test_redis_report_does_not_match_docker_blueprint():
    result = RuleInterpreter().interpret(
        "Redis connection refused by the server.",
        docker_blueprint(),
    )

    assert result.observations == []


def test_unmatched_report_returns_no_observations_and_asks_for_missing_evidence():
    result = RuleInterpreter().interpret(
        "The application is showing an unfamiliar problem.",
        docker_blueprint(),
    )

    assert result.observations == []
    assert result.clarification_questions == [
        "Which host port is Docker trying to publish?"
    ]


def test_clarification_only_fires_when_all_listed_observations_are_missing():
    blueprint = make_blueprint(
        blueprint_id="multiple_observations",
        observation_rules=[
            make_observation("port_in_use", "Port occupied.", ["port occupied"]),
            make_observation("container_stopped", "Container stopped.", ["container stopped"]),
        ],
        clarification_rules=[
            ClarificationRule(
                id="ask_missing",
                when_missing=["port_in_use", "container_stopped"],
                question="Which condition is present?",
            )
        ],
    )
    interpreter = RuleInterpreter()

    missing = interpreter.interpret("No relevant details.", blueprint)
    present = interpreter.interpret("The container stopped.", blueprint)

    assert missing.clarification_questions == ["Which condition is present?"]
    assert present.clarification_questions == []


def test_empty_when_missing_never_fires_and_duplicate_questions_are_removed():
    blueprint = make_blueprint(
        blueprint_id="duplicate_questions",
        observation_rules=[
            make_observation("missing_signal", "Signal.", ["present signal"])
        ],
        clarification_rules=[
            ClarificationRule(id="ask_one", when_missing=[], question="Same question?"),
            ClarificationRule(
                id="ask_two",
                when_missing=["missing_signal"],
                question="Same question?",
            ),
        ],
    )

    result = RuleInterpreter().interpret("No signal here.", blueprint)

    assert result.clarification_questions == ["Same question?"]
    assert result.matched_rule_ids == []
    assert result.clarification_rule_ids == ["ask_two"]


def test_interpretation_is_deterministic():
    blueprint = docker_blueprint()
    interpreter = RuleInterpreter()

    first = interpreter.interpret(
        "Docker failed to start: port is already allocated.",
        blueprint,
    )
    second = interpreter.interpret(
        "Docker failed to start: port is already allocated.",
        blueprint,
    )

    assert first == second


def test_report_is_truncated_and_patterns_only_see_first_four_thousand_characters():
    first_marker = "inside_marker"
    beyond_marker = "outside_marker"
    report = (
        ("x" * (MAX_REPORT_CHARS - len(first_marker)))
        + first_marker
        + ("x" * (10000 - MAX_REPORT_CHARS - len(beyond_marker)))
        + beyond_marker
    )
    blueprint = make_blueprint(
        blueprint_id="truncation",
        observation_rules=[
            make_observation("inside", "Within scan limit.", [first_marker]),
            make_observation("outside", "Beyond scan limit.", [beyond_marker]),
        ],
    )

    result = RuleInterpreter().interpret(report, blueprint)

    assert len(report) == 10000
    assert result.truncated is True
    assert [observation.id for observation in result.observations] == ["inside"]


def test_matched_text_is_capped():
    blueprint = make_blueprint(
        blueprint_id="matched_text_cap",
        observation_rules=[
            make_observation("long_match", "Long match.", [r"a{121}"])
        ],
    )

    result = RuleInterpreter().interpret("a" * 200, blueprint)

    assert len(result.observations[0].matched_text) == MAX_MATCHED_TEXT_CHARS


@pytest.mark.parametrize("exception_type", [re.error, RuntimeError])
def test_pattern_exception_is_recorded_and_other_patterns_still_run(
    monkeypatch,
    exception_type,
):
    blueprint = make_blueprint(
        blueprint_id="skipped_pattern",
        observation_rules=[
            make_observation(
                "recoverable_rule",
                "The working alternative matched.",
                ["raise_during_search", "working_pattern"],
            )
        ],
    )
    original_search = re.search

    def search_with_failure(pattern, *args, **kwargs):
        if pattern == "raise_during_search":
            raise exception_type("simulated search failure")
        return original_search(pattern, *args, **kwargs)

    monkeypatch.setattr(rule_interpreter_module.re, "search", search_with_failure)

    result = RuleInterpreter().interpret("working_pattern appears.", blueprint)

    assert [observation.id for observation in result.observations] == ["recoverable_rule"]
    assert len(result.skipped_patterns) == 1
    assert result.skipped_patterns[0].rule_id == "recoverable_rule"
    assert "simulated search failure" in result.skipped_patterns[0].reason


def test_blueprint_without_observation_rules_returns_empty_interpretation():
    result = RuleInterpreter().interpret(
        "Any report text.",
        make_blueprint(blueprint_id="empty"),
    )

    assert result == RuleInterpretation()


def test_supports_diagnoses_are_carried_into_observation():
    result = RuleInterpreter().interpret(
        "Docker reports that port is already allocated.",
        docker_blueprint(),
    )

    assert result.observations[0].supports_diagnoses == ["port_conflict"]


def test_suppressed_by_suppresses_a_matched_rule_using_the_whole_scanned_text():
    blueprint = make_blueprint(
        blueprint_id="credential_suppression",
        observation_rules=[
            ObservationRule(
                id="credentials_state",
                description="Credentials are reported wrong.",
                patterns=[r"credentials are wrong"],
                suppressed_by=[r"\bi\s+think\b"],
            )
        ],
    )
    interpreter = RuleInterpreter()

    asserted = interpreter.interpret("Credentials are wrong.", blueprint)
    hedged = interpreter.interpret(
        "Credentials are wrong. " + ("additional context " * 10) + "I think.",
        blueprint,
    )

    assert [observation.id for observation in asserted.observations] == [
        "credentials_state"
    ]
    assert hedged.observations == []
    assert hedged.matched_rule_ids == []
    assert hedged.suppressed_rule_ids == ["credentials_state"]


def test_comparison_emits_when_numeric_port_values_differ():
    result = RuleInterpreter().interpret(
        "configured port 5433; listener port 5432",
        port_comparison_blueprint(),
    )

    assert [observation.id for observation in result.observations] == [
        "configured_port",
        "listener_port",
        "port_mismatch",
    ]
    assert result.observations[-1].captures == {"left": "5433", "right": "5432"}
    assert result.observations[-1].polarity == "present"
    assert result.matched_rule_ids == ["configured_port", "listener_port"]


def test_comparison_does_not_emit_when_values_are_equal():
    result = RuleInterpreter().interpret(
        "configured port 5432; listener port 5432",
        port_comparison_blueprint(),
    )

    assert [observation.id for observation in result.observations] == [
        "configured_port",
        "listener_port",
    ]
    assert result.skipped_comparisons == []


def test_equal_comparison_emits_when_values_are_equal():
    result = RuleInterpreter().interpret(
        "configured port 5432; listener port 5432",
        port_comparison_blueprint(op="equal"),
    )

    assert [observation.id for observation in result.observations] == [
        "configured_port",
        "listener_port",
        "port_mismatch",
    ]


def test_numeric_comparison_with_non_integer_values_is_skipped():
    blueprint = port_comparison_blueprint(
        configured_pattern=r"configured port (?P<port>[a-z]+)",
        listener_pattern=r"listener port (?P<port>[a-z]+)",
    )
    result = RuleInterpreter().interpret(
        "configured port alpha; listener port beta",
        blueprint,
    )

    assert [observation.id for observation in result.observations] == [
        "configured_port",
        "listener_port",
    ]
    assert len(result.skipped_comparisons) == 1
    assert result.skipped_comparisons[0].id == "port_mismatch"
    assert "integer" in result.skipped_comparisons[0].reason


def test_suppression_pattern_exception_is_recorded_and_rule_still_fires(monkeypatch):
    blueprint = make_blueprint(
        blueprint_id="bad_suppression_pattern",
        observation_rules=[
            ObservationRule(
                id="signal",
                description="The report contains a signal.",
                patterns=["signal"],
                suppressed_by=["raise_during_suppression"],
            )
        ],
    )
    original_search = re.search

    def search_with_failure(pattern, *args, **kwargs):
        if pattern == "raise_during_suppression":
            raise RuntimeError("simulated suppression failure")
        return original_search(pattern, *args, **kwargs)

    monkeypatch.setattr(rule_interpreter_module.re, "search", search_with_failure)

    result = RuleInterpreter().interpret("signal appears.", blueprint)

    assert [observation.id for observation in result.observations] == ["signal"]
    assert result.suppressed_rule_ids == []
    assert result.skipped_patterns[0].rule_id == "signal"
    assert "simulated suppression failure" in result.skipped_patterns[0].reason


def test_comparison_does_not_emit_if_either_observation_did_not_fire():
    result = RuleInterpreter().interpret(
        "configured port 5433; no listener value was reported",
        port_comparison_blueprint(),
    )

    assert [observation.id for observation in result.observations] == [
        "configured_port"
    ]
    assert result.skipped_comparisons == []


def test_named_capture_values_are_capped_at_sixty_four_characters():
    long_value = "x" * 100
    blueprint = make_blueprint(
        blueprint_id="capture_limit",
        observation_rules=[
            make_observation(
                "captured_value",
                "A long captured value.",
                [r"(?P<value>x{100})"],
            )
        ],
    )

    result = RuleInterpreter().interpret(long_value, blueprint)

    assert result.observations[0].captures == {"value": "x" * 64}
