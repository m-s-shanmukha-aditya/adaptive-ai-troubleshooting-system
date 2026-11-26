import re

from app.models.experience import IssueContext


class IssueAnalyzer:
    """Small deterministic extractor for the initial PostgreSQL domain."""

    def analyze(self, issue: str) -> IssueContext:
        text = issue.lower()
        env = {}
        if "spring boot" in text or "springboot" in text:
            env["framework"] = "Spring Boot"
        if "postgres" in text:
            env["database"] = "PostgreSQL"
        for os_name in ("windows", "linux", "macos", "mac os"):
            if os_name in text:
                env["operating_system"] = "macOS" if os_name.startswith("mac") else os_name.title()
        terms = {
            "connection refused": ("connection_refused", "Database service or configured port may be unavailable."),
            "timeout": ("connection_timeout", "The host, port, or network path may be unavailable."),
            "password authentication": ("authentication_failed", "Authentication failed; verify the configured credentials securely."),
            "does not exist": ("database_not_found", "The configured database name may not exist."),
            "unknownhost": ("hostname_unresolved", "The configured hostname did not resolve."),
        }
        matched = [(term, detail) for term, detail in terms.items() if term in text]
        causes = [detail[1] for _, detail in matched]
        observations = {detail[0]: term for term, detail in matched}
        # Preserve concrete, user-reported details even when they do not match a
        # known domain phrase. These are observations, not diagnoses.
        details = []
        patterns = (
            r"(?:error|exception|message|log)\s*[:=]\s*['\"]?([^\n'\"]{3,180})",
            r"\b(?:https?://|jdbc:[^\s,;]+|[\w.-]+\.ya?ml|[\w.-]+\.toml|[\w.-]+\.json)\b[^\s,;]*",
            r"\b(?:v?\d+\.\d+(?:\.\d+){0,2})\b",
            r"\b(?:localhost|\d{1,3}(?:\.\d{1,3}){3})\b",
        )
        for pattern in patterns:
            details.extend(re.findall(pattern, issue, re.IGNORECASE))
        for index, value in enumerate(dict.fromkeys(v.strip() for v in details if v.strip()), start=1):
            observations[f"reported_detail_{index}"] = value[:200]
        questions = []
        status = "uncertain"
        configured_ports, listener_ports = self._extract_role_ports(text)
        credential_claim = re.search(
            r"\b(?:database\s+)?(?:username|user\s+name)\s+or\s+password\s+(?:is|are)\s+(?:incorrect|invalid|wrong)\b|"
            r"\b(?:incorrect|invalid|wrong)\s+(?:database\s+)?(?:username|user\s+name|password|credentials)\b|"
            r"\bcredentials?\s+(?:is|are)\s+(?:incorrect|invalid|wrong)\b",
            text,
        )
        credential_lead_in = text[max(0, credential_claim.start() - 60):credential_claim.start()] if credential_claim else ""
        hedged_credential_claim = bool(re.search(
            r"\b(?:maybe|might|could|possibly|perhaps|unsure|uncertain|not\s+sure|don't\s+think|do\s+not\s+think)\b",
            credential_lead_in,
        ))
        explicit_invalid_credentials = credential_claim is not None and not hedged_credential_claim

        if len(configured_ports) > 1 or len(listener_ports) > 1:
            # Conflicting objective values cannot safely be resolved by semantic
            # interpretation; keep this narrow deterministic clarification gate.
            questions = ["The issue mentions conflicting port values. Which single port is configured in Spring Boot, and which single port is PostgreSQL listening on?"]
        elif configured_ports and listener_ports:
            configured_port = configured_ports[0]
            listener_port = listener_ports[0]
            observations["datasource_port"] = str(configured_port)
            observations["listener_port"] = str(listener_port)
            if configured_port != listener_port:
                observations["port_mismatch"] = f"configured={configured_port}; listener={listener_port}"
                status = "supported"

        service_running = bool(re.search(
            r"\bpostgres(?:ql)?\s+(?:(?:database|db)\s+)?service\s+(?:is\s+)?running\b|"
            r"\bpostgres(?:ql)?\s+is\s+running\b|\bservice\s+is\s+running\b",
            text,
        ))
        if service_running:
            observations["service_state"] = "running"

        host_port_confirmed = bool(re.search(
            r"\b(?:datasource\s+)?host\s+and\s+port\s+(?:are\s+)?(?:confirmed\s+)?(?:correct|valid|reachable)\b",
            text,
        ))
        if host_port_confirmed:
            observations["host_port_confirmed"] = "reported_correct"

        if not questions and explicit_invalid_credentials:
            observations["credentials_state"] = "reported_invalid"
            status = "supported"
        elif not questions and "password authentication" in text:
            observations["authentication_failure"] = "password_authentication_failed"
        elif not questions and any(value in text for value in ("service is stopped", "service stopped", "not running")):
            observations["service_state"] = "stopped"
            status = "supported"
        elif not questions and any(value in text for value in ("connected successfully", "connection successful", "connection succeeds")):
            observations["connection_success"] = "reported"
            status = "supported"
        # Semantic uncertainty and symptom interpretation belong to candidate
        # generation. It already receives the issue plus extracted observations,
        # must use canonical IDs, and emits targeted questions in the same schema.
        constraints = [part.strip() for part in issue.splitlines() if any(x in part.lower() for x in ("cannot", "must", "don't", "do not"))]
        return IssueContext(issue=issue, conditions=[term for term, _ in matched], constraints=constraints, environment=env, likely_causes=causes, observations=observations, follow_up_questions=questions, diagnostic_status=status)

    @staticmethod
    def _extract_role_ports(text: str) -> tuple[list[int], list[int]]:
        """Return only port numbers explicitly tied to datasource/config and listener roles."""
        port = r"\bport\s*[:=]?\s*(\d{1,5})\b"
        configured_patterns = (
            rf"\b(?:datasource|spring\.datasource(?:\.url)?)\b[^.;\n]{{0,100}}?{port}",
            rf"\bconfigured\b[^.;\n]{{0,100}}?{port}",
            rf"\bconnect(?:ion)?\s+(?:to\s+postgres(?:ql)?\s+)?[^.;\n]{{0,70}}?{port}",
        )
        listener_patterns = (
            rf"\bpostgres(?:ql)?\s+(?:(?:server|database)\s+)?(?:is\s+)?listening\b[^.;\n]{{0,70}}?{port}",
        )

        def collect(patterns: tuple[str, ...]) -> list[int]:
            matches = []
            for pattern in patterns:
                matches.extend(int(value) for value in re.findall(pattern, text))
            return list(dict.fromkeys(matches))

        return collect(configured_patterns), collect(listener_patterns)
