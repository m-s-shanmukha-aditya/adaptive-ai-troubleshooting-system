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
            # Covers JDBC URL ports paired with server status and explicit datasource-port assignments.
            ObservationRule(
                id="datasource_port",
                description="The report states a configured port.",
                patterns=[
                    r"\A(?=[\s\S]*\b(?:postgres(?:ql)?|pgadmin)\b.{0,120}\bserver\b.{0,30}\b(?:on|listening)\b)[\s\S]*?\bjdbc:postgresql://[^/\s:]+:(?P<port>\d{1,5})(?=/|\b)",
                    r"\bconfigured\s+to\s+use\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bdatasource(?:\s+URL)?\s+uses?\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bdatasource\s+(?:is\s+)?on\s+(?P<port>\d{1,5})\b",
                ],
            ),
            # Covers PostgreSQL and pgAdmin statements that identify the listener port.
            ObservationRule(
                id="listener_port",
                description="The report states a PostgreSQL listener port.",
                patterns=[
                    r"\bpostgres(?:ql)?\s+is\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bpostgres(?:ql)?\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bpostgres(?:ql)?\s+server\s+is\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    r"\b(?:postgres(?:ql)?|pgadmin)\b[^.;\n]{0,80}\bserver\s+(?:is\s+)?(?:running\s+on|listening\s+on|on)\s+(?:port\s+)?(?P<port>\d{1,5})\b",
                    r"\bpg\s+(?:is\s+)?on\s+(?:port\s+)?(?P<port>\d{1,5})\b",
                ],
            ),
            # Covers affirmative service/process failure states and explicit restart omissions.
            ObservationRule(
                id="service_stopped",
                description="The report asserts that the PostgreSQL service or process is stopped, down, or not started.",
                patterns=[
                    r"\bpostgres(?:ql)?\s+(?:(?:database|db)\s+)?(?:service|process|server)\s+(?:is\s+|was\s+)?(?:stopped|down|crashed|not\s+running|not\s+up|not\s+started|never\s+started)\b",
                    r"\bpostgres(?:ql)?\b[^.!?\n]{0,80}\b(?:stopped|down|crashed|not\s+running|not\s+up|not\s+started|never\s+started|forgot\s+to\s+start|shut\s+down)\b",
                    r"\b(?:forgot|forgotten)\s+to\s+start\s+(?:the\s+)?postgres(?:ql)?\b",
                    r"\bshut\s+(?:the\s+)?postgres(?:ql)?(?:\s+(?:database|db|service|process|server))?\s+down\b",
                    r"\bpostgres(?:ql)?\s+(?:isn't|isnt)\s+(?:up|running)\b",
                ],
                suppressed_by=[
                    r"\bnot\s+stopped\b",
                    r"\bnot\s+down\b",
                    r"\bisn't\s+down\b",
                    r"\bisnt\s+down\b",
                    r"\b(?:may|might|could|possibly)\s+(?:have\s+)?stopped\b",
                ],
            ),
            # Covers affirmative availability claims, healthy probes, and negated failure states.
            ObservationRule(
                id="service_running",
                description="The report asserts that the PostgreSQL service or process is available.",
                patterns=[
                    r"\bpostgres(?:ql)?\s+(?:(?:database|db)\s+)?(?:service|process|server)\s+(?:(?:seems?|appears?)\s+to\s+be\s+|is\s+(?:definitely\s+)?|has\s+)?(?:up|running|started)\b",
                    r"\bpostgres(?:ql)?\s+is\s+(?:definitely\s+)?(?:up|running|started)\b",
                    r"\b(?:pg_isready|postgres(?:ql)?)\b[^.!?\n]{0,80}\baccepting\s+connections\b",
                    r"\bpostgres(?:ql)?\b[^.!?\n]{0,60}\b(?:not\s+stopped|isn't\s+down|isnt\s+down|not\s+down)\b",
                    r"\b(?:not\s+stopped|isn't\s+down|isnt\s+down|not\s+down)\b[^.!?\n]{0,60}\bpostgres(?:ql)?\b",
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
            # Covers explicit success claims and casual "works now" or "connects fine" phrasing.
            ObservationRule(
                id="connection_success",
                description="The report states that the connection succeeded.",
                patterns=[
                    r"\bconnected successfully\b",
                    r"\bconnection successful\b",
                    r"\bconnection succeeds\b",
                    r"\b(?:postgres(?:ql)?|database|db)\b[^.!?\n]{0,60}\b(?:works?|working)\s+now\b",
                    r"\b(?:spring(?:\s+boot)?|application|app|postgres(?:ql)?)\b[^.!?\n]{0,80}\bconnects?\s+(?:to\s+(?:postgres(?:ql)?|the\s+database)\s+)?fine\b",
                ],
            ),
            # Covers timeout nouns and both common "timed out" and "timing out" forms.
            ObservationRule(
                id="connection_timeout",
                description="The report contains a connection timeout.",
                patterns=[
                    r"\btimeout\b",
                    r"\b(?:postgres(?:ql)?|database|db)\b.{0,100}\b(?:timed|timing)\s+out\b",
                    r"\b(?:timed|timing)\s+out\b.{0,100}\b(?:postgres(?:ql)?|database|db)\b",
                ],
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
