import pytest
from pydantic import ValidationError

from app.models.blueprint import (
    ClarificationRule,
    ComparisonRule,
    ObservationRule,
    ProceduralBlueprint,
)
from app.services.blueprint_loader import load_blueprints


EXISTING_BLUEPRINT_HASHES = {
    "HTTP Gateway 502 and Unhealthy Upstream":
        "300a18e62d37f73fe76f34d3d252369011951b43b4c980d3308c6184e5edc1bf",
    "Spring Boot PostgreSQL Connection Failure":
        "c3f58a6aa712a2af5662084859a4970bba6c049dbfe7c5a80227bd14c0a5710a",
}


def make_blueprint(**updates) -> ProceduralBlueprint:
    values = {
        "title": "Analysis Rules Test",
        "problem_summary": "A test issue.",
        "category": "test",
        "symptoms": [],
        "steps": [
            {
                "step_number": 1,
                "instruction": "Inspect the issue.",
                "expected_result": "The issue is understood.",
            }
        ],
        "verification_checks": [
            {
                "description": "Check the result.",
                "success_condition": "The issue is resolved.",
            }
        ],
    }
    values.update(updates)
    return ProceduralBlueprint(**values)


def make_observation_rule(**updates) -> ObservationRule:
    values = {
        "id": "port_in_use",
        "description": "A requested port is already occupied.",
        "patterns": [r"port is already allocated"],
    }
    values.update(updates)
    return ObservationRule(**values)


def test_valid_analysis_rules_load():
    blueprint = make_blueprint(
        domain="docker",
        aliases=["docker", "container"],
        diagnoses={"port_conflict": {"description": "Port collision"}},
        observation_rules=[
            make_observation_rule(supports_diagnoses=["port_conflict"])
        ],
        clarification_rules=[
            ClarificationRule(
                id="ask_port",
                when_missing=["port_in_use"],
                question="Which host port are you publishing?",
            )
        ],
        extra_unsafe_phrases=["remove all container data"],
        unverified_notice="The container fix has not been verified.",
    )

    assert blueprint.domain == "docker"
    assert blueprint.observation_rules[0].polarity == "present"
    assert blueprint.observation_rules[0].supports_diagnoses == ["port_conflict"]
    assert blueprint.clarification_rules[0].when_missing == ["port_in_use"]


def test_analysis_fields_have_empty_or_none_defaults():
    blueprint = make_blueprint()

    assert blueprint.domain is None
    assert blueprint.aliases == []
    assert blueprint.observation_rules == []
    assert blueprint.clarification_rules == []
    assert blueprint.comparison_rules == []
    assert blueprint.extra_unsafe_phrases == []
    assert blueprint.unverified_notice is None
    assert make_observation_rule().suppressed_by == []


def test_docker_port_in_use_blueprint_validates_in_memory():
    blueprint = make_blueprint(
        blueprint_id="docker_port_in_use",
        domain="docker",
        aliases=["docker", "container"],
        diagnoses={"port_conflict": {"description": "The host port is occupied."}},
        observation_rules=[
            make_observation_rule(
                patterns=[r"port is already allocated"],
                supports_diagnoses=["port_conflict"],
            )
        ],
        clarification_rules=[
            ClarificationRule(
                id="identify_published_port",
                when_missing=["port_in_use"],
                question="Which host port is Docker trying to publish?",
            )
        ],
    )

    assert blueprint.blueprint_id == "docker_port_in_use"
    assert blueprint.domain == "docker"


@pytest.mark.parametrize("pattern", ["(", "a" * 201, "(a+)+", "(.*)*"])
def test_invalid_or_disallowed_regex_patterns_are_rejected(pattern):
    with pytest.raises(ValidationError):
        make_observation_rule(patterns=[pattern])


def test_observation_rule_rejects_more_than_twenty_patterns():
    with pytest.raises(ValidationError):
        make_observation_rule(patterns=["port"] * 21)


@pytest.mark.parametrize("pattern", ["(", "x" * 201, "(a+)+"])
def test_suppression_patterns_use_the_same_regex_validation(pattern):
    with pytest.raises(ValidationError):
        make_observation_rule(suppressed_by=[pattern])


def test_suppression_patterns_are_limited_to_twenty():
    with pytest.raises(ValidationError):
        make_observation_rule(suppressed_by=["maybe"] * 21)


def test_named_capture_groups_must_be_lowercase_identifiers():
    with pytest.raises(ValidationError):
        make_observation_rule(patterns=[r"(?P<Port>\d+)"])


def test_observation_rule_rejects_more_than_five_named_capture_groups():
    pattern = r"(?P<a>a)(?P<b>b)(?P<c>c)(?P<d>d)(?P<e>e)(?P<f>f)"
    with pytest.raises(ValidationError):
        make_observation_rule(patterns=[pattern])


def test_named_capture_limit_applies_across_all_patterns():
    with pytest.raises(ValidationError):
        make_observation_rule(patterns=[r"(?P<port>\d+)"] * 6)


@pytest.mark.parametrize(
    "rule_id",
    ["Upper_case", "has-hyphen", "has space", "_leading", "trailing_", "two__underscores"],
)
def test_observation_rule_requires_lowercase_snake_case_id(rule_id):
    with pytest.raises(ValidationError):
        make_observation_rule(id=rule_id)


def test_clarification_rule_requires_lowercase_snake_case_id():
    with pytest.raises(ValidationError):
        ClarificationRule(id="Ask_Port", when_missing=[], question="Which port?")


def test_observation_polarity_is_limited_to_present_or_absent():
    with pytest.raises(ValidationError):
        make_observation_rule(polarity="unknown")


def test_rule_ids_must_be_unique_across_both_rule_types():
    with pytest.raises(ValidationError):
        make_blueprint(
            observation_rules=[make_observation_rule(id="same_rule")],
            clarification_rules=[
                ClarificationRule(
                    id="same_rule",
                    when_missing=["same_rule"],
                    question="A question?",
                )
            ],
        )


def test_comparison_rule_id_must_be_unique_across_all_rule_types():
    port_rule = make_observation_rule(
        id="port",
        patterns=[r"(?P<value>\d+)"],
    )
    with pytest.raises(ValidationError):
        make_blueprint(
            observation_rules=[port_rule],
            comparison_rules=[
                ComparisonRule(
                    id="port",
                    description="Same ID.",
                    left="port.value",
                    right="port.value",
                    op="equal",
                )
            ],
        )


def test_comparison_rule_id_must_be_unique_with_clarification_rule_ids():
    port_rule = make_observation_rule(
        id="port",
        patterns=[r"(?P<value>\d+)"],
    )
    with pytest.raises(ValidationError):
        make_blueprint(
            observation_rules=[port_rule],
            clarification_rules=[
                ClarificationRule(
                    id="compare_ports",
                    when_missing=["port"],
                    question="Which port?",
                )
            ],
            comparison_rules=[
                ComparisonRule(
                    id="compare_ports",
                    description="Same ID.",
                    left="port.value",
                    right="port.value",
                    op="equal",
                )
            ],
        )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("unknown.value", "port.value"),
        ("port.missing", "port.value"),
        ("port", "port.value"),
    ],
)
def test_comparison_references_must_resolve_to_named_observation_groups(left, right):
    with pytest.raises(ValidationError):
        make_blueprint(
            observation_rules=[
                make_observation_rule(id="port", patterns=[r"(?P<value>\d+)"])
            ],
            comparison_rules=[
                ComparisonRule(
                    id="compare_ports",
                    description="Compare ports.",
                    left=left,
                    right=right,
                    op="not_equal",
                )
            ],
        )


def test_comparison_supports_diagnoses_must_reference_existing_keys():
    with pytest.raises(ValidationError):
        make_blueprint(
            diagnoses={"known_diagnosis": {}},
            observation_rules=[
                make_observation_rule(id="port", patterns=[r"(?P<value>\d+)"])
            ],
            comparison_rules=[
                ComparisonRule(
                    id="compare_ports",
                    description="Compare ports.",
                    left="port.value",
                    right="port.value",
                    op="not_equal",
                    supports_diagnoses=["unknown_diagnosis"],
                )
            ],
        )


def test_blueprint_rejects_more_than_twenty_comparison_rules():
    port_rule = make_observation_rule(id="port", patterns=[r"(?P<value>\d+)"])
    comparisons = [
        ComparisonRule(
            id=f"compare_{index}",
            description="Compare a port with itself.",
            left="port.value",
            right="port.value",
            op="equal",
        )
        for index in range(21)
    ]
    with pytest.raises(ValidationError):
        make_blueprint(
            observation_rules=[port_rule],
            comparison_rules=comparisons,
        )


def test_clarification_rule_must_reference_existing_observation_rules():
    with pytest.raises(ValidationError):
        make_blueprint(
            clarification_rules=[
                ClarificationRule(
                    id="ask_port",
                    when_missing=["missing_observation"],
                    question="Which port?",
                )
            ]
        )


def test_observation_rule_diagnoses_must_exist_when_blueprint_has_diagnoses():
    with pytest.raises(ValidationError):
        make_blueprint(
            diagnoses={"known_diagnosis": {}},
            observation_rules=[
                make_observation_rule(supports_diagnoses=["unknown_diagnosis"])
            ],
        )


def test_observation_rule_diagnosis_references_are_unchecked_when_none_exist():
    blueprint = make_blueprint(
        observation_rules=[
            make_observation_rule(supports_diagnoses=["future_diagnosis"])
        ]
    )
    assert blueprint.observation_rules[0].supports_diagnoses == ["future_diagnosis"]


def test_blueprint_rejects_more_than_fifty_observation_rules():
    rules = [
        make_observation_rule(id=f"observation_{index}")
        for index in range(51)
    ]
    with pytest.raises(ValidationError):
        make_blueprint(observation_rules=rules)


def test_blueprint_rejects_more_than_twenty_clarification_rules():
    rules = [
        ClarificationRule(
            id=f"clarification_{index}",
            when_missing=[],
            question="A question?",
        )
        for index in range(21)
    ]
    with pytest.raises(ValidationError):
        make_blueprint(clarification_rules=rules)


def test_existing_blueprints_load_and_preserve_pre_schema_content_hashes():
    loaded_hashes = {
        blueprint.title: blueprint.content_hash()
        for blueprint in load_blueprints()
    }

    assert {
        title: loaded_hashes[title]
        for title in EXISTING_BLUEPRINT_HASHES
    } == EXISTING_BLUEPRINT_HASHES


@pytest.mark.parametrize(
    "field",
    [
        "domain",
        "aliases",
        "observation_rules",
        "clarification_rules",
        "comparison_rules",
        "extra_unsafe_phrases",
        "unverified_notice",
    ],
)
def test_set_analysis_fields_are_included_in_content_hash(field):
    blueprint = load_blueprints()[0]
    values = {
        "domain": "web",
        "aliases": ["web service"],
        "observation_rules": [make_observation_rule()],
        "clarification_rules": [
            ClarificationRule(
                id="ask_context",
                when_missing=[],
                question="Which service is affected?",
            )
        ],
        "comparison_rules": [
            ComparisonRule(
                id="port_comparison",
                description="Compare ports.",
                left="configured.port",
                right="listener.port",
                op="not_equal",
                numeric=True,
            )
        ],
        "extra_unsafe_phrases": ["delete all data"],
        "unverified_notice": "This fix has not been verified.",
    }
    updated = blueprint.model_copy(update={field: values[field]})

    assert updated.content_hash() != blueprint.content_hash()


def test_set_suppressed_by_is_included_in_content_hash():
    without_suppression = make_blueprint(
        observation_rules=[make_observation_rule()]
    )
    with_suppression = make_blueprint(
        observation_rules=[make_observation_rule(suppressed_by=[r"\bmaybe\b"])]
    )

    assert with_suppression.content_hash() != without_suppression.content_hash()
