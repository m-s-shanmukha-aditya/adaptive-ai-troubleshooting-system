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


def test_credentials_claims_require_postgres_or_database_context():
    reports = [
        "PostgreSQL login confirms the database username or password is invalid.",
        "A psql check says the user name or password is wrong for this connection.",
        "Postgres application secrets show credentials in vault-map.yml are incorrect.",
    ]
    for report in reports:
        assert "credentials_state" in observations_by_id(interpret(report)), report

    near_misses = [
        "MongoDB login confirms the username or password is invalid.",
        "Cloud object storage credentials in keyring.yml are incorrect.",
        "Elasticsearch user name or password is wrong.",
    ]
    for report in near_misses:
        assert "credentials_state" not in observations_by_id(interpret(report)), report


def test_configured_to_use_port_requires_postgres_or_database_context():
    reports = [
        "PostgreSQL client is configured to use port 5910.",
        "The client is configured to use port 5911 for the PostgreSQL endpoint.",
        "The database connector is configured to use port 5912 in staging.",
    ]
    for report in reports:
        assert "datasource_port" in observations_by_id(interpret(report)), report

    near_misses = [
        "Elasticsearch client is configured to use port 5913.",
        "Redis client is configured to use port 5914.",
        "The generic service is configured to use port 5915.",
    ]
    for report in near_misses:
        assert "datasource_port" not in observations_by_id(interpret(report)), report


def test_missing_database_phrases_require_database_context():
    reports = [
        "PostgreSQL bootstrap cannot continue because database ledger does not exist.",
        "psql reports that the requested database does not exist in this cluster.",
        "The database named archive_store does not exist on PostgreSQL.",
    ]
    for report in reports:
        assert "database_not_found" in observations_by_id(interpret(report)), report

    near_misses = [
        "The Redis cache key does not exist after expiration.",
        "The MongoDB collection does not exist in this namespace.",
        "The filesystem path does not exist on the worker.",
    ]
    for report in near_misses:
        assert "database_not_found" not in observations_by_id(interpret(report)), report


def test_systemd_inactive_dead_status_phrases():
    reports = [
        "systemctl reports postgresql Active: inactive (dead) after maintenance.",
        "Active: inactive (dead) appears for the postgres daemon in system status.",
        "Service monitoring lists PostgreSQL active: inactive (dead) on node alpha.",
    ]
    for report in reports:
        assert "service_stopped" in observations_by_id(interpret(report)), report

    near_misses = [
        "systemctl reports nginx Active: inactive (dead) after maintenance.",
        "PostgreSQL Active: active (running) after maintenance.",
        "Redis status is Active: inactive (dead) on node alpha.",
    ]
    for report in near_misses:
        assert "service_stopped" not in observations_by_id(interpret(report)), report


def test_postgres_not_returned_after_restart_phrases():
    reports = [
        "PostgreSQL never came back up after the node reboot.",
        "Nobody restarted the postgres service after the host reboot.",
        "The PostgreSQL process did not return after reboot.",
    ]
    for report in reports:
        assert "service_stopped" in observations_by_id(interpret(report)), report

    near_misses = [
        "PostgreSQL came back up after the node reboot.",
        "The team restarted PostgreSQL after the host reboot.",
        "Nobody restarted the Redis service after reboot.",
    ]
    for report in near_misses:
        assert "service_stopped" not in observations_by_id(interpret(report)), report


def test_versioned_postgresql_service_running_phrases():
    reports = [
        "postgresql-x64-16 service is running after the patch.",
        "postgresql_x64_14 service running during the migration.",
        "The postgresql-x64-19 service is running on its host.",
    ]
    for report in reports:
        assert "service_running" in observations_by_id(interpret(report)), report

    near_misses = [
        "mysql-x64-16 service is running after the patch.",
        "PostgreSQL service version 16 is active on its host.",
        "PostgreSQL-x64-16 service is stopped after the patch.",
    ]
    for report in near_misses:
        assert "service_running" not in observations_by_id(interpret(report)), report


def test_times_out_verb_phrases_with_database_context():
    reports = [
        "The PostgreSQL pool times out during initialization.",
        "Database requests times out on the reporting node.",
        "Client requests to postgres times out after each retry.",
    ]
    for report in reports:
        assert "connection_timeout" in observations_by_id(interpret(report)), report

    near_misses = [
        "The SSH control session times out over the VPN.",
        "Redis reads times out during load.",
        "The PostgreSQL request waits and then succeeds.",
    ]
    for report in near_misses:
        assert "connection_timeout" not in observations_by_id(interpret(report)), report


def test_jdbc_url_port_requires_a_nearby_postgres_listener_status():
    reports = [
        "URL jdbc:postgresql://db-a:5916/inventory; PostgreSQL server listens at 5432.",
        "PostgreSQL server is listening on 5432 beside jdbc:postgresql://db-b:5917/store.",
        "PostgreSQL is listening on 5432 while endpoint jdbc:postgresql://db-c:5918/events serves traffic.",
    ]
    for report, expected_port in zip(reports, ("5916", "5917", "5918")):
        observation = observations_by_id(interpret(report)).get("datasource_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_misses = [
        "Use jdbc:mysql://db-a:5919/inventory; PostgreSQL server listens at 3306.",
        "The jdbc:postgresql://db-b:5920/shop URL is recorded, but no server state is available.",
        "PostgreSQL server listens at 5432, but no JDBC URL is configured.",
    ]
    for report in near_misses:
        assert "datasource_port" not in observations_by_id(interpret(report)), report


def test_database_configured_port_declaration_phrases():
    reports = [
        "PostgreSQL db port in the client settings is 5923 for ingest.",
        "The db port in the app profile is 5924, and PostgreSQL is the selected store.",
        "Postgres app port is 5925 for the batch service.",
    ]
    for report, expected_port in zip(reports, ("5923", "5924", "5925")):
        observation = observations_by_id(interpret(report)).get("datasource_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_misses = [
        "MySQL db port is 3306 for ingest.",
        "Redis app port is 6379 for the cache.",
        "The app port is 5926, and the database engine is not specified.",
    ]
    for report in near_misses:
        assert "datasource_port" not in observations_by_id(interpret(report)), report


def test_app_pointed_port_phrases_and_postgres_listener_binding():
    reports = [
        "App is pointed at port 5927; PG server is bound to 5432.",
        "The app is pointed to port 5928 while the postgres listener uses 5432.",
        "PG server is bound to port 5432, and the app points to port 5929.",
    ]
    for report, expected_port in zip(reports, ("5927", "5928", "5929")):
        observations = observations_by_id(interpret(report))
        datasource = observations.get("datasource_port")
        assert datasource is not None, report
        assert datasource.captures["port"] == expected_port

    near_misses = [
        "App is pointed at port 5930; Redis server is bound to 6379.",
        "App is pointed at port 5931, but no Redis endpoint was configured.",
        "The PG server is bound to port 5432, but the app has no configured port.",
    ]
    for report in near_misses:
        assert "datasource_port" not in observations_by_id(interpret(report)), report


def test_pg_server_bound_port_phrases():
    reports = [
        "PG server is bound to 5932 for the local instance.",
        "The pg server bound to port 5933 accepts connections.",
        "Postgres server is bound to 5934 during staging.",
    ]
    for report, expected_port in zip(reports, ("5932", "5933", "5934")):
        observation = observations_by_id(interpret(report)).get("listener_port")
        assert observation is not None, report
        assert observation.captures["port"] == expected_port

    near_misses = [
        "Redis server is bound to port 6379 for the local instance.",
        "The PG client is bound to port 5935.",
        "PostgreSQL application is bound to 5936, not a server listener.",
    ]
    for report in near_misses:
        assert "listener_port" not in observations_by_id(interpret(report)), report


def test_psql_host_translation_error_phrases():
    reports = [
        "psql reports it could not translate host name db-catalog to an address.",
        "psql error: cannot translate host name for the primary endpoint.",
        "psql is unable to translate host name during connection setup.",
    ]
    for report in reports:
        assert "hostname_unresolved" in observations_by_id(interpret(report)), report

    near_misses = [
        "The HTTP client could not translate host name for the worker.",
        "The DNS monitor cannot translate host name in its test fixture.",
        "A generic connector is unable to translate host name for this target.",
    ]
    for report in near_misses:
        assert "hostname_unresolved" not in observations_by_id(interpret(report)), report


def test_reconnected_postgres_app_success_phrases_and_near_misses():
    reports = [
        "Our Spring app reconnects to PostgreSQL again after the credential refresh.",
        "The app connected to postgres db again after the route change.",
        "The application is connecting to the PostgreSQL database again after restart.",
    ]
    for report in reports:
        assert "connection_success" in observations_by_id(interpret(report)), report

    near_misses = [
        "The Spring app reconnects to MongoDB again after the credential refresh.",
        "The app does not connect to postgres again after the route change.",
        "The application has not connected to the PostgreSQL database again.",
    ]
    for report in near_misses:
        assert "connection_success" not in observations_by_id(interpret(report)), report


def test_stopped_negation_contractions_and_qualifiers():
    reports = [
        "PostgreSQL isn't stopped; the readiness probe is green.",
        "Postgres is not currently stopped after the service check.",
        "PostgreSQL was stopped earlier, but it is not stopped now.",
    ]
    for report in reports:
        observations = observations_by_id(interpret(report))
        assert set(observations) == {"service_running"}, report

    near_misses = [
        "PostgreSQL isn't running after the host restart.",
        "Postgres is not currently running during maintenance.",
        "PostgreSQL may have stopped, but no status was confirmed.",
    ]
    for report in near_misses:
        observations = observations_by_id(interpret(report))
        assert "service_running" not in observations, report
