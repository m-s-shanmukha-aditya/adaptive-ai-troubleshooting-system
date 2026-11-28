import json
import threading
import time
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main_module
import app.services.blueprint_loader as blueprint_loader
import app.services.troubleshooter as troubleshooter_module
from app.models.blueprint import BlueprintStatus
from app.services.learning_checks import check_learned_blueprint
from app.services.redaction import redact, report_hash
from app.services.troubleshooter import Troubleshooter
from app.services.blueprint_loader import load_blueprints


POSTGRES_EXAMPLE = next(
    item
    for item in load_blueprints()
    if item.title == "Spring Boot PostgreSQL Connection Failure"
)


def _blueprint_dict():
    return {
        "blueprint_id": "redis_connection_refused",
        "title": "Redis connection refused",
        "problem_summary": (
            "A Redis client cannot establish a TCP connection to its endpoint. "
            "This class of problem can result from service availability, endpoint "
            "configuration, or network reachability."
        ),
        "category": "redis",
        "symptoms": [
            "A Redis client reports connection refused",
            "The application cannot open a Redis socket",
            "Connection attempts fail before authentication",
            "The configured Redis endpoint may not accept connections",
        ],
        "steps": [
            {
                "step_number": 1,
                "instruction": "Check the Redis service status.",
                "expected_result": "The reported status is recorded.",
            }
        ],
        "verification_checks": [
            {
                "description": "Check the connection status.",
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


def _answer():
    return {
        "diagnosis_id": "redis_connection_refused",
        "status": "supported",
        "evidence_ids": ["redis_refused"],
        "interpretations": {
            "redis_refused": "The report says the connection was refused."
        },
        "action_ids": ["verify_redis_service"],
        "follow_up_questions": [],
    }


def _report():
    return "Redis connection refused while checking the endpoint during requests."


def _setup(monkeypatch, tmp_path):
    for flag in (
        "ENABLE_CLOUD_LEARNING",
        "ENABLE_PROVISIONAL_BLUEPRINTS",
        "ENABLE_RULE_OBSERVATIONS",
    ):
        monkeypatch.setenv(flag, "1")

    approved_dir = tmp_path / "blueprints"
    provisional_dir = tmp_path / "blueprints_provisional"
    rejected_dir = tmp_path / "blueprints_rejected"
    approved_dir.mkdir(parents=True)
    provisional_dir.mkdir(parents=True)
    rejected_dir.mkdir(parents=True)
    monkeypatch.setattr(blueprint_loader, "BLUEPRINT_DIR", approved_dir)
    monkeypatch.setattr(blueprint_loader, "PROVISIONAL_BLUEPRINT_DIR", provisional_dir)
    monkeypatch.setattr(blueprint_loader, "REJECTED_BLUEPRINT_DIR", rejected_dir)
    monkeypatch.setattr(
        troubleshooter_module,
        "CLOUD_LEARNING_LOG_PATH",
        tmp_path / "cloud_learning.jsonl",
    )
    with troubleshooter_module._learning_lock:
        troubleshooter_module._cloud_learning_calls.clear()
        troubleshooter_module._learning_hashes_in_flight.clear()
        troubleshooter_module._learning_hashes_saved.clear()

    class FakeRetriever:
        def __init__(self, blueprints, model_name=None):
            self.blueprints = list(blueprints)
            self.model_name = model_name

        def rebuild(self, blueprints):
            self.blueprints = list(blueprints)

        def retrieve(self, _query, top_k=5):
            return [(blueprint, 1.0) for blueprint in self.blueprints[:top_k]]

    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: MagicMock())
    service = Troubleshooter(repository=MagicMock())
    service.blueprints = [POSTGRES_EXAMPLE]
    monkeypatch.setattr(main_module, "troubleshooter", service)
    monkeypatch.setattr(
        troubleshooter_module,
        "reindex",
        lambda: service.replace_retriever(
            blueprint_loader.load_blueprints(eligible_only=True)
        ),
    )
    return service, provisional_dir


def _make_client():
    app = FastAPI()
    app.add_api_route(
        "/troubleshoot",
        main_module.troubleshoot,
        methods=["POST"],
        response_model=main_module.TroubleshootResponse,
    )
    return TestClient(app)


def _request(service, issue):
    main_module.troubleshooter = service
    with _make_client() as client:
        return client.post("/troubleshoot", json={"issue": issue})


def _mock_cloud(monkeypatch, output=None, error=None):
    captured = {}

    class FakeCloud:
        def generate_response(self, prompt):
            captured["prompt"] = prompt
            if error is not None:
                raise error
            return output

    def factory(*, model_name, timeout, max_retries):
        captured["model_name"] = model_name
        captured["timeout"] = timeout
        captured["max_retries"] = max_retries
        return FakeCloud()

    monkeypatch.setattr(troubleshooter_module, "CloudLLM", factory)
    return captured


def _set_learned_cloud(monkeypatch, issue=None):
    issue = issue or _report()
    learned = {"blueprint": _blueprint_dict(), "answer": _answer()}
    candidate = check_learned_blueprint(
        issue,
        learned,
        "openrouter/free",
        {POSTGRES_EXAMPLE.blueprint_id},
    ).candidate_json
    return _mock_cloud(
        monkeypatch,
        json.dumps(learned),
    ), candidate


def _log_entries(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_each_learning_flag_off_preserves_existing_miss_response(monkeypatch, tmp_path):
    disabled_flags = (
        "ENABLE_CLOUD_LEARNING",
        "ENABLE_PROVISIONAL_BLUEPRINTS",
        "ENABLE_RULE_OBSERVATIONS",
    )
    for flag in disabled_flags:
        service, _ = _setup(monkeypatch, tmp_path / flag)
        monkeypatch.setenv(flag, "0")
        cloud = _mock_cloud(
            monkeypatch,
            error=AssertionError("cloud must stay unused"),
        )
        issue = _report()
        expected = service._insufficient_knowledge_response(
            service.analyzer.analyze(issue)
        )

        response = _request(service, issue)

        assert response.status_code == 200
        assert response.json() == {"issue": issue, "solution": expected}
        assert _log_entries(
            troubleshooter_module.CLOUD_LEARNING_LOG_PATH
        )[0]["reason_code"] in {
            "cloud_learning_disabled",
            "provisional_blueprints_disabled",
            "rule_observations_disabled",
        }
        assert "prompt" not in cloud


def test_learning_pregates_short_rate_limit_and_duplicate(monkeypatch, tmp_path):
    service, _ = _setup(monkeypatch, tmp_path)
    cases = (
        ("report_too_short", "brief report"),
        ("rate_limited", _report()),
        ("hash_in_flight", _report()),
        ("hash_already_saved", _report()),
    )
    for reason, issue in cases:
        with troubleshooter_module._learning_lock:
            troubleshooter_module._cloud_learning_calls.clear()
            troubleshooter_module._learning_hashes_in_flight.clear()
            troubleshooter_module._learning_hashes_saved.clear()
        if reason == "rate_limited":
            troubleshooter_module._cloud_learning_calls.extend(
                [time.monotonic()] * troubleshooter_module.MAX_CLOUD_LEARNING_CALLS_PER_HOUR
            )
        elif reason in {"hash_in_flight", "hash_already_saved"}:
            digest = report_hash(redact(issue)[0])
            if reason == "hash_in_flight":
                troubleshooter_module._learning_hashes_in_flight.add(digest)
            else:
                troubleshooter_module._learning_hashes_saved.add(digest)
        cloud = _mock_cloud(
            monkeypatch,
            error=AssertionError("pre-gate must prevent cloud"),
        )

        response = _request(service, issue)

        assert response.status_code == 200
        assert _log_entries(
            troubleshooter_module.CLOUD_LEARNING_LOG_PATH
        )[-1]["reason_code"] == reason
        assert "prompt" not in cloud


def test_clarification_exit_never_attempts_learning(monkeypatch, tmp_path):
    service, _ = _setup(monkeypatch, tmp_path)
    cloud = _mock_cloud(
        monkeypatch,
        error=AssertionError("clarification must not learn"),
    )
    issue = (
        "Spring Boot datasource port 5433 and configured port 5434; "
        "PostgreSQL server is listening on port 5432."
    )

    response = _request(service, issue)

    assert response.status_code == 200
    assert response.json()["solution"].startswith(
        "The available information is insufficient"
    )
    assert not troubleshooter_module.CLOUD_LEARNING_LOG_PATH.exists()
    assert "prompt" not in cloud


def test_cloud_decline_error_timeout_and_invalid_output_abstain(
    monkeypatch, tmp_path
):
    cases = (
        ("declined", json.dumps({"declined": "not enough detail"}), None),
        ("cloud_error", None, RuntimeError("mock cloud failure")),
        ("cloud_timeout", None, TimeoutError("mock timeout")),
        ("invalid_json", "not a JSON response", None),
    )
    for reason, output, error in cases:
        case_dir = tmp_path / reason
        service, provisional_dir = _setup(monkeypatch, case_dir)
        _mock_cloud(monkeypatch, output=output, error=error)
        issue = _report()
        expected = service._insufficient_knowledge_response(
            service.analyzer.analyze(issue)
        )

        response = _request(service, issue)

        assert response.status_code == 200
        assert response.json()["solution"] == expected
        assert list(provisional_dir.glob("*.json")) == []
        assert _log_entries(
            troubleshooter_module.CLOUD_LEARNING_LOG_PATH
        )[-1]["reason_code"] == reason


def test_failed_deterministic_check_discards_blueprint(monkeypatch, tmp_path):
    service, provisional_dir = _setup(monkeypatch, tmp_path)
    bad_answer = _answer()
    bad_answer["action_ids"] = ["restart_redis"]
    _mock_cloud(
        monkeypatch,
        output=json.dumps(
            {"blueprint": _blueprint_dict(), "answer": bad_answer}
        ),
    )
    issue = _report()
    expected = service._insufficient_knowledge_response(
        service.analyzer.analyze(issue)
    )

    response = _request(service, issue)

    assert response.status_code == 200
    assert response.json()["solution"] == expected
    assert list(provisional_dir.glob("*.json")) == []
    assert _log_entries(
        troubleshooter_module.CLOUD_LEARNING_LOG_PATH
    )[-1]["reason_code"] != "saved"


def test_success_saves_provisional_then_answers_repeat_locally(
    monkeypatch, tmp_path
):
    service, provisional_dir = _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_MODEL", "mock-model")
    issue = _report()
    cloud, candidate = _set_learned_cloud(monkeypatch, issue)
    local = MagicMock(return_value=candidate)
    service.llm = MagicMock()
    service.llm.generate_response = local

    first = _request(service, issue)

    assert first.status_code == 200
    assert first.json()["solution"].startswith(
        "[Cloud answer, unverified; no approved knowledge existed for this issue]"
    )
    assert first.json()["solution"].endswith(
        "Diagnosis correctness: unknown. The database connection has not been verified."
    )
    saved_files = list(provisional_dir.glob("*.json"))
    assert len(saved_files) == 1
    saved = json.loads(saved_files[0].read_text(encoding="utf-8"))
    assert saved["status"] == BlueprintStatus.PROVISIONAL.value
    assert saved["generated_by"] == "mock-model"
    assert cloud["model_name"] == "mock-model"
    assert cloud["timeout"] == 60
    assert cloud["max_retries"] == 0

    second = _request(service, issue)

    assert second.status_code == 200
    assert second.json()["solution"].startswith(
        "[Provisional blueprint: auto-generated, not yet reviewed]\n"
        "[Local candidate selected;"
    )
    assert local.call_count == 1
    assert len(_log_entries(troubleshooter_module.CLOUD_LEARNING_LOG_PATH)) == 1


def test_learning_path_does_not_persist_report_or_redacted_secrets(
    monkeypatch, tmp_path
):
    service, provisional_dir = _setup(monkeypatch, tmp_path)
    issue = (
        "Redis connection refused while checking the endpoint. "
        "API_KEY=sk-abcdefghijklmnopqrstuvwxyz012345 "
        "Contact alice@example.test for details."
    )
    cloud, _ = _set_learned_cloud(monkeypatch, issue)

    response = _request(service, issue)

    assert response.status_code == 200
    prompt = cloud["prompt"]
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in prompt
    assert "alice@example.test" not in prompt
    persisted = [
        path.read_text(encoding="utf-8")
        for path in (
            provisional_dir.glob("*.json")
        )
    ]
    persisted.extend(
        [troubleshooter_module.CLOUD_LEARNING_LOG_PATH.read_text(encoding="utf-8")]
    )
    redacted_issue = redact(issue)[0]
    assert all(issue not in text for text in persisted)
    assert all(redacted_issue not in text for text in persisted)
    assert all("sk-abcdefghijklmnopqrstuvwxyz012345" not in text for text in persisted)
    assert all("alice@example.test" not in text for text in persisted)
    service.repository.save_evaluation.assert_not_called()


def test_model_blueprint_id_cannot_escape_provisional_directory(
    monkeypatch, tmp_path
):
    service, provisional_dir = _setup(monkeypatch, tmp_path)
    blueprint = _blueprint_dict()
    blueprint["blueprint_id"] = "../../outside"
    learned = {
        "blueprint": blueprint,
        "answer": _answer(),
    }
    _mock_cloud(monkeypatch, output=json.dumps(learned))

    response = _request(service, _report())

    assert response.status_code == 200
    saved_files = list(provisional_dir.glob("*.json"))
    assert len(saved_files) == 1
    assert saved_files[0].resolve().parent == provisional_dir.resolve()
    assert saved_files[0].name == "redis_connection_refused.json"
    saved = json.loads(saved_files[0].read_text(encoding="utf-8"))
    assert saved["blueprint_id"] == "redis_connection_refused"


def test_learning_cloud_wall_clock_deadline_returns_cloud_timeout(
    monkeypatch, tmp_path
):
    service, _ = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        troubleshooter_module,
        "LEARNING_CLOUD_DEADLINE_SECONDS",
        0.02,
    )
    client_started = threading.Event()
    release_client = threading.Event()
    client_finished = threading.Event()
    captured = {}

    class SlowCloud:
        def generate_response(self, _prompt):
            client_started.set()
            release_client.wait(timeout=2)
            client_finished.set()
            return "{}"

    def factory(*, model_name, timeout, max_retries):
        captured.update(
            model_name=model_name,
            timeout=timeout,
            max_retries=max_retries,
        )
        return SlowCloud()

    monkeypatch.setattr(troubleshooter_module, "CloudLLM", factory)
    try:
        response = _request(service, _report())
        assert response.status_code == 200
        assert client_started.is_set()
        assert not client_finished.is_set()
        assert captured["timeout"] == 60
        assert captured["max_retries"] == 0
        assert _log_entries(
            troubleshooter_module.CLOUD_LEARNING_LOG_PATH
        )[-1]["reason_code"] == "cloud_timeout"
    finally:
        release_client.set()


def test_response_body_is_sent_before_blocking_background_task(
    monkeypatch, tmp_path
):
    service, _ = _setup(monkeypatch, tmp_path)
    _set_learned_cloud(monkeypatch)
    task_started = threading.Event()
    release_task = threading.Event()
    response_sent = threading.Event()
    request_completed = threading.Event()

    def blocking_task(*_args):
        task_started.set()
        release_task.wait(timeout=5)

    monkeypatch.setattr(service, "_save_provisional_blueprint", blocking_task)
    app = FastAPI()
    app.add_api_route(
        "/troubleshoot",
        main_module.troubleshoot,
        methods=["POST"],
        response_model=main_module.TroubleshootResponse,
    )

    class ResponseObserver:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            async def observed_send(message):
                await send(message)
                if (
                    message["type"] == "http.response.body"
                    and not message.get("more_body", False)
                ):
                    response_sent.set()

            await self.inner(scope, receive, observed_send)

    app.add_middleware(ResponseObserver)
    result = {}

    with TestClient(app) as client:
        def send_request():
            try:
                result["response"] = client.post(
                    "/troubleshoot",
                    json={"issue": _report()},
                )
            finally:
                request_completed.set()

        worker = threading.Thread(target=send_request)
        worker.start()
        assert response_sent.wait(timeout=3)
        assert task_started.wait(timeout=3)
        assert not request_completed.is_set()
        release_task.set()
        worker.join(timeout=3)

    assert request_completed.is_set()
    assert result["response"].status_code == 200
    with troubleshooter_module._learning_lock:
        troubleshooter_module._learning_hashes_in_flight.clear()


def test_cloud_llm_model_timeout_options_are_optional(monkeypatch):
    import app.services.cloud_llm as cloud_llm_module

    calls = []

    def fake_getenv(name, default=None):
        if name == "OPENROUTER_API_KEY":
            return "mock-only-key"
        if name == "OPENROUTER_MODEL":
            return "mock-model"
        return default

    def fake_openai(**kwargs):
        calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(cloud_llm_module.os, "getenv", fake_getenv)
    monkeypatch.setattr(cloud_llm_module, "OpenAI", fake_openai)

    client = cloud_llm_module.CloudLLM(timeout=60)

    assert client.model_name == "mock-model"
    assert calls[0]["timeout"] == 60
    assert calls[0]["base_url"] == "https://openrouter.ai/api/v1"

    existing = cloud_llm_module.CloudLLM(model_name="explicit-model")
    assert existing.model_name == "explicit-model"
    assert "timeout" not in calls[1]
    assert "max_retries" not in calls[1]

    retry_controlled = cloud_llm_module.CloudLLM(
        model_name="explicit-model",
        timeout=60,
        max_retries=0,
    )
    assert retry_controlled.model_name == "explicit-model"
    assert calls[2]["timeout"] == 60
    assert calls[2]["max_retries"] == 0
