from app.models.blueprint import (
    ClarificationRule,
    ComparisonRule,
    ObservationRule,
    ProceduralBlueprint,
)


def build_postgres_rules_blueprint() -> ProceduralBlueprint:
    return ProceduralBlueprint(
        blueprint_id="postgres_rule_parity",
        domain="postgresql",
        title="PostgreSQL analyzer parity rules",
        problem_summary="Pattern rules sampled from the current IssueAnalyzer.",
        category="database",
        symptoms=[],
        steps=[
            {
                "step_number": 1,
                "instruction": "Review the reported evidence.",
                "expected_result": "The relevant PostgreSQL observations are identified.",
            }
        ],
        verification_checks=[
            {
                "description": "Check the reported evidence.",
                "success_condition": "The report's matched evidence is listed.",
            }
        ],
        observation_rules=[
            ObservationRule(
                id="authentication_failed",
                description="The report contains the password-authentication phrase.",
                patterns=[r"password authentication"],
            ),
            ObservationRule(
                id="credentials_state",
                description="The report states that database credentials are invalid.",
                patterns=[
                    r"\b(?:database\s+)?(?:username|user\s+name)\s+or\s+password\s+(?:is|are)\s+(?:incorrect|invalid|wrong)\b",
                    r"\b(?:incorrect|invalid|wrong)\s+(?:database\s+)?(?:username|user\s+name|password|credentials)\b",
                    r"\bcredentials?\s+(?:is|are)\s+(?:incorrect|invalid|wrong)\b",
                ],
                suppressed_by=[
                    r"\bmaybe\b",
                    r"\bmight\b",
                    r"\bcould\b",
                    r"\bpossibly\b",
                    r"\bperhaps\b",
                    r"\bunsure\b",
                    r"\buncertain\b",
                    r"\bnot\s+sure\b",
                    r"\bdon't\s+think\b",
                    r"\bdo\s+not\s+think\b",
                    r"\bi\s+think\b",
                ],
            ),
            ObservationRule(
                id="datasource_port",
                description="The report states a configured port.",
                patterns=[
                    r"\bconfigured\s+to\s+use\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bdatasource(?:\s+URL)?\s+uses?\s+port\s+(?P<port>\d{1,5})\b",
                ],
            ),
            ObservationRule(
                id="listener_port",
                description="The report states a PostgreSQL listener port.",
                patterns=[
                    r"\bpostgres(?:ql)?\s+is\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bpostgres(?:ql)?\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bpostgres(?:ql)?\s+server\s+is\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                ],
            ),
            ObservationRule(
                id="service_state",
                description="The report states that the PostgreSQL service is running or stopped.",
                patterns=[
                    r"\bpostgres(?:ql)?\s+(?:(?:database|db)\s+)?service\s+(?:is\s+)?(?:running|stopped)\b",
                    r"\bpostgres(?:ql)?\s+is\s+running\b",
                    r"\bpostgres(?:ql)?\s+is\s+not\s+running\b",
                ],
            ),
            ObservationRule(
                id="authentication_failure",
                description="Password authentication failed without the explicit invalid-credentials claim.",
                patterns=[
                    r"^(?!.*\busername\s+or\s+password\s+is\s+(?:incorrect|invalid|wrong)\b).*password authentication"
                ],
            ),
            ObservationRule(
                id="database_not_found",
                description="The report says that the database does not exist.",
                patterns=[r"does not exist"],
            ),
            ObservationRule(
                id="hostname_unresolved",
                description="The report contains the unknown-host indicator.",
                patterns=[r"unknownhost"],
            ),
            ObservationRule(
                id="connection_success",
                description="The report states that the connection succeeded.",
                patterns=[
                    r"\bconnected successfully\b",
                    r"\bconnection successful\b",
                    r"\bconnection succeeds\b",
                ],
            ),
            ObservationRule(
                id="connection_timeout",
                description="The report contains a connection timeout.",
                patterns=[r"\btimeout\b"],
            ),
        ],
        comparison_rules=[
            ComparisonRule(
                id="port_mismatch",
                description="The configured datasource port differs from the PostgreSQL listener port.",
                left="datasource_port.port",
                right="listener_port.port",
                op="not_equal",
                numeric=True,
            )
        ],
        clarification_rules=[
            # The sampled analyzer emits no questions; its conflict question needs value comparison.
            ClarificationRule(
                id="clarify_port_conflict",
                when_missing=[],
                question=(
                    "The issue mentions conflicting port values. Which single port "
                    "is configured in Spring Boot, and which single port is "
                    "PostgreSQL listening on?"
                ),
            )
        ],
    )
