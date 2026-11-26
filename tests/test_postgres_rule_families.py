from app.services.rule_interpreter import RuleInterpreter
from tests.fixtures.postgres_rules import build_postgres_rules_blueprint


def interpret(issue: str):
    return RuleInterpreter().interpret(issue, build_postgres_rules_blueprint())


def observations_by_id(result):
    return {observation.id: observation for observation in result.observations}


def test_service_stopped_phrase_families_and_near_misses():
    stopped_reports = [
        "The PostgreSQL process was stopped after scheduled maintenance.",
        "I shut the postgres server down before moving the database.",
        "Postgres is not running after the nightly reboot.",
        "The PostgreSQL daemon crashed during failover.",
        "After the host reboot, PostgreSQL was never started.",
        "PostgreSQL deployment failed because the team forgot to start it.",
    ]
    for report in stopped_reports:
        assert "service_stopped" in observations_by_id(interpret(report)), report

    near_misses = [
        "PostgreSQL is not stopped after the health check.",
        "PostgreSQL is not down after the health check.",
        "PostgreSQL isn't down following the restart.",
        "The PostgreSQL service may have stopped, but nobody checked its state.",
    ]
    for report in near_misses:
        assert "service_stopped" not in observations_by_id(interpret(report)), report


def test_service_running_phrase_families_and_near_misses():
    running_reports = [
        "The PostgreSQL service is up after the restart.",
        "PostgreSQL is definitely running after the check.",
        "The pg_isready probe reports accepting connections for PostgreSQL.",
        "Postgres is not stopped after the health check.",
        "PostgreSQL isn't down after maintenance.",
    ]
    for report in running_reports:
        assert "service_running" in observations_by_id(interpret(report)), report

    near_misses = [
        "PostgreSQL is not running after the restart.",
        "PostgreSQL isn't up after maintenance.",
        "PostgreSQL isnt running following the reboot.",
    ]
    for report in near_misses:
        assert "service_running" not in observations_by_id(interpret(report)), report


def test_service_state_negation_and_contradiction():
    expected_only_running = [
        "PostgreSQL is not stopped.",
        "PostgreSQL is not down.",
        "PostgreSQL isn't down.",
        "PostgreSQL is definitely running.",
    ]
    for report in expected_only_running:
        assert set(observations_by_id(interpret(report))) == {"service_running"}, report

    expected_only_stopped = [
        "PostgreSQL is not running.",
        "PostgreSQL isn't up.",
        "PostgreSQL isnt running.",
    ]
    for report in expected_only_stopped:
        assert set(observations_by_id(interpret(report))) == {"service_stopped"}, report

    contradiction = "PostgreSQL service is running, but it is also not running."
    assert set(observations_by_id(interpret(contradiction))) == {
        "service_stopped",
        "service_running",
    }


def test_datasource_port_url_and_natural_language_families():
    reports = [
        "The datasource is on 5621 for the analytics cluster.",
        "Use jdbc:postgresql://db-lab:5622/ledger; "
        "PostgreSQL server is on port 5620.",
    ]
    for report, expected_port in zip(reports, ("5621", "5622")):
        observation = observations_by_id(interpret(report)).get("datasource_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_miss = "Use jdbc:mysql://db-lab:5623/ledger for the connection endpoint."
    assert "datasource_port" not in observations_by_id(interpret(near_miss))


def test_listener_port_server_phrases_and_non_database_near_miss():
    reports = [
        "The PostgreSQL server is on port 5624 during maintenance.",
        "pgAdmin shows that the server is listening on 5625.",
    ]
    for report, expected_port in zip(reports, ("5624", "5625")):
        observation = observations_by_id(interpret(report)).get("listener_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_miss = "The application server is listening on port 5626; database status is unknown."
    assert "listener_port" not in observations_by_id(interpret(near_miss))


def test_datasource_db_and_app_port_declarations_and_near_misses():
    reports = [
        "PostgreSQL datasource port is 5701 for the billing service.",
        "The PostgreSQL db port is 5702 during staging.",
        "Our PostgreSQL app port is 5703 for the reporting client.",
    ]
    for report, expected_port in zip(reports, ("5701", "5702", "5703")):
        observation = observations_by_id(interpret(report)).get("datasource_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_misses = [
        "MySQL db port is 3306.",
        "Redis app port is 6379.",
        "PostgreSQL app port has not been configured.",
    ]
    for report in near_misses:
        assert "datasource_port" not in observations_by_id(interpret(report)), report


def test_postgres_server_listens_on_or_at_port_and_near_misses():
    reports = [
        "The PostgreSQL server listens on 5801 during tests.",
        "Postgres listens at 5802 for the local instance.",
        "PostgreSQL is the selected engine, and the server listens at port 5803.",
    ]
    for report, expected_port in zip(reports, ("5801", "5802", "5803")):
        observation = observations_by_id(interpret(report)).get("listener_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_misses = [
        "MySQL server listens on 3306.",
        "Redis server listens at 6379.",
        "PostgreSQL cannot connect to port 5432.",
    ]
    for report in near_misses:
        assert "listener_port" not in observations_by_id(interpret(report)), report


def test_app_connection_success_families_and_near_misses():
    reports = [
        "The Spring Boot app now connects to PostgreSQL after the config reload.",
        "Our app now connected to postgres after the service restart.",
        "The app connects fine now to PostgreSQL from the worker.",
    ]
    for report in reports:
        assert "connection_success" in observations_by_id(interpret(report)), report

    near_misses = [
        "The MySQL app now connects to MySQL after the update.",
        "The Redis app now connected to Redis after the restart.",
        "The app does not connect to PostgreSQL after the config change.",
    ]
    for report in near_misses:
        assert "connection_success" not in observations_by_id(interpret(report)), report


def test_jdbc_port_comparison_fires_in_either_text_order():
    reports = [
        "URL jdbc:postgresql://node-a:5630/stock; pgAdmin says the server is listening on port 5631.",
        "PostgreSQL server is on 5641 before URL jdbc:postgresql://node-b:5640/stock.",
    ]
    for report in reports:
        observations = observations_by_id(interpret(report))
        assert {"datasource_port", "listener_port", "port_mismatch"} <= set(observations)

    equal_ports = observations_by_id(
        interpret(
            "PostgreSQL server is on port 5650; "
            "URL jdbc:postgresql://node-c:5650/stock."
        )
    )
    assert {"datasource_port", "listener_port"} <= set(equal_ports)
    assert "port_mismatch" not in equal_ports


def test_timeout_phrase_families_and_non_timeout_near_miss():
    reports = [
        "The PostgreSQL login timed out while traversing the mesh network.",
        "Connections are timing out to the postgres cluster.",
    ]
    for report in reports:
        assert "connection_timeout" in observations_by_id(interpret(report)), report

    near_miss = "The postgres login takes a long time but eventually finishes."
    assert "connection_timeout" not in observations_by_id(interpret(near_miss))


def test_casual_success_phrase_families_and_failure_near_miss():
    reports = [
        "PostgreSQL works now after the replica switch.",
        "Spring service connects fine to PostgreSQL since the update.",
    ]
    for report in reports:
        assert "connection_success" in observations_by_id(interpret(report)), report

    near_miss = "Spring Boot still cannot connect to PostgreSQL."
    assert "connection_success" not in observations_by_id(interpret(near_miss))
