from app.models.blueprint import (
    ClarificationRule,
    ComparisonRule,
    ObservationRule,
    ProceduralBlueprint,
)


PG_DATABASE_CONTEXT = (
    r"(?:postgres(?:ql)?|psql|pg_[a-z0-9_]{1,24}|"
    r"jdbc:postgresql|database)"
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
                    # Gate username/password assertions on nearby PostgreSQL or database context.
                    rf"\b{PG_DATABASE_CONTEXT}\b[\s\S]{{0,100}}\b(?:username|user\s+name)\s+or\s+password\s+(?:is|are)\s+(?:incorrect|invalid|wrong)\b",
                    # Also allow the context to follow a username/password assertion.
                    rf"\b(?:username|user\s+name)\s+or\s+password\s+(?:is|are)\s+(?:incorrect|invalid|wrong)\b[\s\S]{{0,100}}\b{PG_DATABASE_CONTEXT}\b",
                    # Gate reversed invalid-credential claims on nearby PostgreSQL context.
                    rf"\b{PG_DATABASE_CONTEXT}\b[\s\S]{{0,100}}\b(?:incorrect|invalid|wrong)\s+(?:database\s+)?(?:username|user\s+name|password|credentials)\b",
                    # Also allow PostgreSQL context to follow a reversed invalid-credential claim.
                    rf"\b(?:incorrect|invalid|wrong)\s+(?:database\s+)?(?:username|user\s+name|password|credentials)\b[\s\S]{{0,100}}\b{PG_DATABASE_CONTEXT}\b",
                    # Allow bounded configuration details within an explicit credentials assertion.
                    rf"\b{PG_DATABASE_CONTEXT}\b[\s\S]{{0,100}}\bcredentials?\b[^!?\n]{{0,80}}\b(?:is|are)\s+(?:incorrect|invalid|wrong)\b",
                    # Also allow PostgreSQL context after an explicit credentials assertion.
                    rf"\bcredentials?\b[^!?\n]{{0,80}}\b(?:is|are)\s+(?:incorrect|invalid|wrong)\b[\s\S]{{0,100}}\b{PG_DATABASE_CONTEXT}\b",
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
            # Covers JDBC URL ports paired with bounded PostgreSQL server context.
            ObservationRule(
                id="datasource_port",
                description="The report states a configured port.",
                patterns=[
                    # Preserve the PostgreSQL listener-context gate around JDBC URL ports.
                    r"(?=\A[\s\S]{0,1000}\b(?:postgres(?:ql)?|pg)[\s\S]{0,20}\b(?:server[\s\S]{0,8}\b(?:running|listening|on|bound)\b|listen(?:ing|s?))\b)[\s\S]{0,1000}\bjdbc:postgresql://[^/\s:]+:(?P<port>\d{1,5})(?=/|\b)",
                    # Require nearby PostgreSQL/database context for generic configured-port wording.
                    rf"\b{PG_DATABASE_CONTEXT}\b[\s\S]{{0,100}}\bconfigured\s+to\s+use\s+port\s+(?P<port>\d{{1,5}})\b",
                    # Also allow PostgreSQL/database context after configured-port wording.
                    rf"\bconfigured\s+to\s+use\s+port\s+(?P<port>\d{{1,5}})\b[\s\S]{{0,100}}\b{PG_DATABASE_CONTEXT}\b",
                    # Preserve existing datasource-use and datasource-on port declarations.
                    r"\bdatasource(?:\s+URL)?\s+(?:(?:uses?\s+port|(?:is\s+)?on(?:\s+port)?)\s*|port\b[^.!?\n]{0,40}\b(?:is|=)\s*)(?P<port>\d{1,5})\b",
                    # Capture app/database and app-pointed ports with a bounded PostgreSQL-context check.
                    r"\A(?=[\s\S]{0,99}\b(?:postgres|pg))[\s\S]{0,999}\b(?:db|app(?:lication)?)\b[^.!?\n]{0,20}\b(?:port\b[^.!?\n]{0,30}\b(?:is|=)\s*|(?:is\s+)?point(?:ed|s)\s+(?:at|to)\s+port\s+)(?P<port>\d{1,5})\b",
                ],
            ),
            # Covers PostgreSQL and pgAdmin listener-port statements, including "listens on/at".
            ObservationRule(
                id="listener_port",
                description="The report states a PostgreSQL listener port.",
                patterns=[
                    r"\bpostgres(?:ql)?\s+(?:server\s+)?(?:is\s+)?(?:listening|listens?)\s+(?:on|at)\s+(?:port\s+)?(?P<port>\d{1,5})\b",
                    r"\bpostgres(?:ql)?\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    r"\bpostgres(?:ql)?\s+server\s+is\s+listening\s+on\s+port\s+(?P<port>\d{1,5})\b",
                    # Include abbreviated PG and bound-to listener statements.
                    r"\b(?:postgres(?:ql)?|pgadmin|pg)\b[^.;\n]{0,80}\bserver\s+(?:(?:is\s+)?(?:running\s+on|listening\s+on|bound\s+to|on)|listens?\s+(?:on|at))\s+(?:port\s+)?(?P<port>\d{1,5})\b",
                    r"\bpg\s+(?:is\s+)?on\s+(?:port\s+)?(?P<port>\d{1,5})\b",
                ],
            ),
            # Covers affirmative service/process failure states and explicit restart omissions.
            ObservationRule(
                id="service_stopped",
                description="The report asserts that the PostgreSQL service or process is stopped, down, or not started.",
                patterns=[
                    # Recognize PostgreSQL's systemd inactive/dead service status.
                    r"\bpostgres(?:ql)?\b[\s\S]{0,80}\bactive:\s*inactive\s*\(dead\)",
                    # Allow the systemd status to precede the PostgreSQL service name.
                    r"\bactive:\s*inactive\s*\(dead\)[\s\S]{0,80}\bpostgres(?:ql)?\b",
                    r"\bpostgres(?:ql)?\s+(?:(?:database|db)\s+)?(?:service|process|server)\s+(?:is\s+|was\s+)?(?:stopped|down|crashed|not\s+running|not\s+up|not\s+started|never\s+started)\b",
                    r"\bpostgres(?:ql)?\b[^.!?\n]{0,80}\b(?:stopped|down|crashed|not\s+running|not\s+up|not\s+started|never\s+started|forgot\s+to\s+start|shut\s+down)\b",
                    # Recognize a PostgreSQL service that never returned after a restart.
                    r"\bpostgres(?:ql)?\b[^.!?\n]{0,80}\bnever\s+came\s+back\s+up\b",
                    # Recognize reports that nobody restarted or started PostgreSQL.
                    r"\bnobody\s+(?:started|restarted)\b[^.!?\n]{0,80}\bpostgres(?:ql)?\b",
                    # Recognize PostgreSQL that failed to start or return after reboot.
                    r"\bpostgres(?:ql)?\b[^.!?\n]{0,80}\b(?:didn't|did\s+not|never)\s+(?:return|restart|start)\b[^.!?\n]{0,40}\bafter\b[^.!?\n]{0,20}\breboot\b",
                    r"\b(?:forgot|forgotten)\s+to\s+start\s+(?:the\s+)?postgres(?:ql)?\b",
                    r"\bshut\s+(?:the\s+)?postgres(?:ql)?(?:\s+(?:database|db|service|process|server))?\s+down\b",
                    r"\bpostgres(?:ql)?\s+(?:isn't|isnt)\s+(?:up|running)\b",
                ],
                suppressed_by=[
                    r"\bnot\s+stopped\b",
                    # Treat a contracted negated stopped state as suppression.
                    r"\bisn't\s+stopped\b",
                    # Treat the uncontracted negated stopped state as suppression.
                    r"\bisnt\s+stopped\b",
                    # Allow a bounded current-state qualifier in stopped negation.
                    r"\bnot\s+(?:currently\s+)?stopped\b",
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
                    # Recognize availability status on versioned PostgreSQL Windows services.
                    r"\bpostgresql[-_]x64[-_]?\d{1,3}\s+service\b[^.!?\n]{0,40}\brunning\b",
                    r"\bpostgres(?:ql)?\s+is\s+(?:definitely\s+)?(?:up|running|started)\b",
                    r"\b(?:pg_isready|postgres(?:ql)?)\b[^.!?\n]{0,80}\baccepting\s+connections\b",
                    r"\bpostgres(?:ql)?\b[^.!?\n]{0,60}\b(?:not\s+stopped|isn't\s+down|isnt\s+down|not\s+down)\b",
                    r"\b(?:not\s+stopped|isn't\s+down|isnt\s+down|not\s+down)\b[^.!?\n]{0,60}\bpostgres(?:ql)?\b",
                    # Treat contracted negation of stopped as affirmative availability evidence.
                    r"\bpostgres(?:ql)?\s+(?:isn't|isnt)\s+stopped\b",
                    # Treat a bounded qualifier in a negated stopped state as availability evidence.
                    r"\bpostgres(?:ql)?\s+is\s+not\s+(?:currently\s+)?stopped\b",
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
                patterns=[
                    # Require nearby PostgreSQL/database context before a missing-database claim.
                    rf"\b{PG_DATABASE_CONTEXT}\b[\s\S]{{0,100}}\bdoes\s+not\s+exist\b",
                    # Also allow PostgreSQL/database context after a missing-database claim.
                    rf"\bdoes\s+not\s+exist\b[\s\S]{{0,100}}\b{PG_DATABASE_CONTEXT}\b",
                ],
            ),
            ObservationRule(
                id="hostname_unresolved",
                description="The report contains the unknown-host indicator.",
                patterns=[
                    r"unknownhost",
                    # Recognize psql host-translation errors with bounded local wording.
                    r"\bpsql\b[^.!?\n]{0,80}\b(?:could\s+not|cannot|unable\s+to)\s+translate\s+host\s+name\b",
                ],
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
                    # Covers an app now connecting or newly connected to PostgreSQL.
                    r"\A(?=[\s\S]*\bpostgres(?:ql)?\b)[\s\S]*?\b(?:spring\s+boot\s+)?app\s+now\s+connect(?:s|ed)\s+to\s+postgres(?:ql)?\b",
                    # Covers apps that connect fine now to PostgreSQL.
                    r"\A(?=[\s\S]*\bpostgres(?:ql)?\b)[\s\S]*?\b(?:spring\s+boot\s+)?app\s+connects?\s+fine\s+now\b",
                    # Recognize a recovered app connection to PostgreSQL with bounded negation checks.
                    r"\b(?:app|application)\b(?![^\n.!?]{0,24}\bnot\b)[^\n.!?]{0,60}\b(?:re)?connect(?:s|ed|ing)?\s+to\s+(?:the\s+)?postgres(?:ql)?(?:\s+(?:db|database))?\b[^\n.!?]{0,24}\bagain\b",
                ],
            ),
            # Covers timeout nouns and both common "timed out" and "timing out" forms.
            ObservationRule(
                id="connection_timeout",
                description="The report contains a connection timeout.",
                patterns=[
                    r"\btimeout\b",
                    # Recognize a PostgreSQL/database endpoint that times out.
                    r"\b(?:postgres(?:ql)?|database|db)\b[\s\S]{0,120}\btimes\s+out\b",
                    # Also recognize timeout wording before nearby PostgreSQL/database context.
                    r"\btimes\s+out\b[\s\S]{0,120}\b(?:postgres(?:ql)?|database|db)\b",
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
