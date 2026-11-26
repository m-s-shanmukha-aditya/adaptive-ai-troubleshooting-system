from app.models.experience import IssueContext, ScenarioTest


class TestCaseGenerator:
    """Deterministic catalog of controlled Spring Boot/PostgreSQL cases."""
    SCENARIOS = [
        ScenarioTest(test_id="postgres_service_stopped", description="PostgreSQL service stopped", input_evidence="Connection refused; controlled fixture reports the PostgreSQL service stopped.", oracle="Diagnosis must identify the stopped PostgreSQL service.", observations={"connection": "refused", "service": "stopped"}, expected_diagnosis_id="postgres_service_stopped", expected_evidence_ids=["service_state"], allowed_action_ids=["start_postgres_service", "verify_service_status"]),
        ScenarioTest(test_id="datasource_port_incorrect", description="Incorrect datasource port", input_evidence="PostgreSQL is listening on the fixture port; datasource URL uses a different port.", oracle="Diagnosis must identify the datasource/listener port mismatch.", observations={"listener_port": "fixture_listener_port", "datasource_port": "fixture_different_port"}, expected_diagnosis_id="datasource_port_incorrect", expected_evidence_ids=["datasource_port", "listener_port"], allowed_action_ids=["align_datasource_port", "verify_listener_port"]),
        ScenarioTest(test_id="database_credentials_invalid", description="Invalid database credentials", input_evidence="Host, port, and database exist; controlled authentication attempt fails.", oracle="Diagnosis must identify invalid database credentials.", observations={"host_port_database": "reachable_and_exists", "authentication": "failed"}, expected_diagnosis_id="database_credentials_invalid", expected_evidence_ids=["authentication_result"], allowed_action_ids=["verify_credentials_securely", "rotate_credentials_if_needed"]),
        ScenarioTest(test_id="database_name_incorrect", description="Incorrect database name", input_evidence="Host and port are reachable; requested database does not exist in the fixture.", oracle="Diagnosis must identify the incorrect datasource database name.", observations={"host_port": "reachable", "requested_database": "not_found"}, expected_diagnosis_id="database_name_incorrect", expected_evidence_ids=["database_lookup"], allowed_action_ids=["align_database_name", "verify_database_exists"]),
        ScenarioTest(test_id="connection_successful", description="Correct configuration and successful connection", input_evidence="Controlled connection succeeds with the configured service, host, port, database, and credentials.", oracle="Diagnosis must identify valid configuration/no fault.", observations={"connection": "successful", "configuration": "valid"}, expected_diagnosis_id="connection_successful", expected_evidence_ids=["connection_result"], allowed_action_ids=["make_no_configuration_change", "confirm_application_health"]),
    ]

    def controlled_scenarios(self) -> list[ScenarioTest]:
        return [test.model_copy(deep=True) for test in self.SCENARIOS]

    def generate(self, context: IssueContext | None = None, candidate: str = "", historical_patterns: list[str] | None = None) -> list[ScenarioTest]:
        """Backward-compatible alias for the controlled catalog; runtime must not call it."""
        del context, candidate, historical_patterns
        return self.controlled_scenarios()
