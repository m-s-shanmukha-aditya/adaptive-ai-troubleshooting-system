import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from demo import learning_demo


class MockServer:
    def __init__(
        self,
        scripts,
        provisional_dir,
        default_response=None,
        learning_log_path=None,
    ):
        self.scripts = scripts
        self.provisional_dir = provisional_dir
        self.learning_log_path = learning_log_path
        self.default_response = default_response or _local_response()
        self.requests = []
        self._counts = {}

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                payload = json.loads(self.rfile.read(length))
                issue = payload["issue"]
                outer.requests.append(payload)
                count = outer._counts.get(issue, 0)
                outer._counts[issue] = count + 1
                script = outer.scripts.get(issue, [])
                if count < len(script):
                    response = script[count]
                else:
                    response = {"solution": outer.default_response}
                if response.get("create_draft"):
                    outer.provisional_dir.mkdir(parents=True, exist_ok=True)
                    (outer.provisional_dir / response["create_draft"]).write_text(
                        "{}",
                        encoding="utf-8",
                    )
                if response.get("log_entry") and outer.learning_log_path is not None:
                    outer.learning_log_path.parent.mkdir(parents=True, exist_ok=True)
                    with outer.learning_log_path.open("a", encoding="utf-8") as file:
                        file.write(json.dumps(response["log_entry"]) + "\n")
                body = json.dumps({
                    "issue": issue,
                    "solution": response["solution"],
                }).encode("utf-8")
                self.send_response(response.get("status", 200))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _local_response(diagnosis_id="sample_diagnosis"):
    return (
        "[Local candidate selected; runtime diagnosis is not ground-truth verified]"
        '\n\n{"diagnosis":{"id":"' + diagnosis_id + '"}}'
    )


def _cloud_response():
    return (
        learning_demo.CLOUD_ANSWER_PREFIX
        + '\n\n{"diagnosis":{"id":"cloud_diagnosis"}}'
    )


def _provisional_response():
    return (
        learning_demo.PROVISIONAL_PREFIX
        + "\n[Local candidate selected; runtime diagnosis is not ground-truth verified]"
        + '\n\n{"diagnosis":{"id":"provisional_diagnosis"}}'
    )


def _configure_paths(monkeypatch, tmp_path, scenarios):
    output_dir = tmp_path / "outputs_learning"
    provisional_dir = tmp_path / "blueprints_provisional"
    approved_dir = tmp_path / "blueprints"
    requests_file = tmp_path / "learning_requests.json"
    requests_file.write_text(json.dumps(scenarios), encoding="utf-8")
    monkeypatch.setattr(learning_demo, "REQUESTS_FILE", requests_file)
    monkeypatch.setattr(learning_demo, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(learning_demo, "STATE_FILE", output_dir / "state.json")
    monkeypatch.setattr(learning_demo, "PROVISIONAL_DIR", provisional_dir)
    monkeypatch.setattr(learning_demo, "APPROVED_DIR", approved_dir)
    monkeypatch.setattr(
        learning_demo,
        "CLOUD_LEARNING_LOG",
        tmp_path / "cloud_learning.jsonl",
    )
    monkeypatch.setattr(learning_demo, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(learning_demo, "POLL_TIMEOUT_SECONDS", 0.04)
    return output_dir, provisional_dir


def test_learn_records_decline_draft_hit_and_no_draft(
    monkeypatch, tmp_path, capsys
):
    scenarios = {
        "declined": "A synthetic vague report with no specific technical details.",
        "learned": "A synthetic report that should create a local draft file.",
        "unexpected": "A synthetic report that matches an existing blueprint.",
        "no_draft": "A synthetic report whose learning task creates no draft.",
    }
    output_dir, provisional_dir = _configure_paths(
        monkeypatch,
        tmp_path,
        scenarios,
    )
    timeouts = []
    real_urlopen = learning_demo.urlopen

    def recording_urlopen(request, timeout):
        timeouts.append(timeout)
        return real_urlopen(request, timeout=timeout)

    monkeypatch.setattr(learning_demo, "urlopen", recording_urlopen)
    scripts = {
        scenarios["declined"]: [{
            "solution": (
                '{"diagnosis":{"id":"undetermined",'
                '"status":"insufficient_evidence"}}'
            )
        }],
        scenarios["learned"]: [
            {"solution": _cloud_response(), "create_draft": "new-draft.json"},
            {"solution": _provisional_response()},
            {"solution": _provisional_response()},
            {"solution": _provisional_response()},
        ],
        scenarios["unexpected"]: [{"solution": _local_response()}],
        scenarios["no_draft"]: [{"solution": _cloud_response()}],
    }
    with MockServer(scripts, provisional_dir) as server:
        assert learning_demo.main(["--learn", "--base-url", server.base_url]) == 0

    assert len(server.requests) == 7
    assert timeouts == [200, 200, 120, 120, 120, 200, 200]
    assert all(set(request) == {"issue"} for request in server.requests)
    state = json.loads((output_dir / "state.json").read_text(encoding="utf-8"))
    assert state == {"learned": "new-draft.json"}
    summary = (output_dir / "summary.md").read_text(encoding="utf-8")
    assert "| declined | first | early-exit | undetermined |" in summary
    assert "| learned | repeat3 | provisional_local |" in summary
    assert "| unexpected | first | local |" in summary
    assert "unexpected_hit" in summary
    assert "no_draft" in summary
    assert "no_log_line" in summary
    assert "draft=new-draft.json; wait=" in summary

    records = [
        path for path in output_dir.glob("*.json") if path.name != "state.json"
    ]
    assert len(records) == 7
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert set(record) == {
            "scenario",
            "phase",
            "issue",
            "http_status",
            "seconds",
            "stage",
            "response_body",
        }
    assert "no_draft_first.json" in {path.name for path in records}
    assert "no_draft_repeat1.json" not in {path.name for path in records}
    assert "declined_repeat1.json" not in {path.name for path in records}
    assert capsys.readouterr().out == summary


def test_early_exit_note_uses_only_new_log_fields_and_first_timeout(
    monkeypatch, tmp_path, capsys
):
    scenario = "synthetic early exit"
    output_dir, provisional_dir = _configure_paths(
        monkeypatch,
        tmp_path,
        {"early_exit": scenario},
    )
    log_entry = {
        "reason_code": "invalid_blueprint_id",
        "timings_ms": {"cloud": 100700.0},
        "schema_errors": ["steps.0.step_number:greater_than"],
    }
    script = {
        scenario: [{
            "solution": "PRIVATE RESPONSE TEXT MUST NOT APPEAR",
            "log_entry": log_entry,
        }]
    }
    timeouts = []
    real_urlopen = learning_demo.urlopen

    def recording_urlopen(request, timeout):
        timeouts.append(timeout)
        return real_urlopen(request, timeout=timeout)

    monkeypatch.setattr(learning_demo, "urlopen", recording_urlopen)
    with MockServer(
        script,
        provisional_dir,
        learning_log_path=learning_demo.CLOUD_LEARNING_LOG,
    ) as server:
        assert learning_demo.main([
            "--learn",
            "--base-url",
            server.base_url,
            "--first-timeout",
            "17",
        ]) == 0

    summary = (output_dir / "summary.md").read_text(encoding="utf-8")
    assert "invalid_blueprint_id (cloud 100.7 s)" in summary
    assert "schema_errors: steps.0.step_number:greater_than" in summary
    assert "PRIVATE RESPONSE TEXT" not in summary
    assert timeouts == [17.0]
    assert "PRIVATE RESPONSE TEXT" not in capsys.readouterr().out


def test_refuses_non_localhost_base_url(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path, {"scenario": "synthetic issue text"})

    with pytest.raises(SystemExit) as exc_info:
        learning_demo.main([
            "--learn",
            "--base-url",
            "http://example.com:8000",
        ])

    assert exc_info.value.code == 2


def test_after_approval_reads_only_scenarios_in_state(
    monkeypatch, tmp_path, capsys
):
    scenarios = {
        "approved": "Synthetic issue for an approved draft.",
        "not_recorded": "Synthetic issue without a saved draft name.",
    }
    output_dir, provisional_dir = _configure_paths(
        monkeypatch,
        tmp_path,
        scenarios,
    )
    output_dir.mkdir(parents=True)
    (output_dir / "state.json").write_text(
        json.dumps({"approved": "approved-blueprint.json"}),
        encoding="utf-8",
    )
    with MockServer({}, provisional_dir) as server:
        assert learning_demo.main([
            "--after-approval",
            "--base-url",
            server.base_url,
        ]) == 0

    assert server.requests == [{"issue": scenarios["approved"]}]
    record = json.loads(
        (output_dir / "approved_after_approval.json").read_text(encoding="utf-8")
    )
    assert record["stage"] == "local"
    assert "Provisional" not in record["response_body"]
    assert "| approved | after_approval | local |" in capsys.readouterr().out


def test_never_exceeds_fifteen_requests(monkeypatch, tmp_path, capsys):
    scenarios = {
        f"scenario_{number}": f"Synthetic issue number {number}."
        for number in range(16)
    }
    output_dir, provisional_dir = _configure_paths(
        monkeypatch,
        tmp_path,
        scenarios,
    )
    output_dir.mkdir(parents=True)
    (output_dir / "state.json").write_text(
        json.dumps({
            scenario: f"{scenario}.json"
            for scenario in scenarios
        }),
        encoding="utf-8",
    )
    with MockServer({}, provisional_dir) as server:
        assert learning_demo.main([
            "--after-approval",
            "--base-url",
            server.base_url,
        ]) == 0

    assert len(server.requests) == learning_demo.MAX_REQUESTS == 15
    assert len(list(output_dir.glob("*_after_approval.json"))) == 15
    assert "request cap reached (15)" in capsys.readouterr().out
