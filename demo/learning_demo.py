"""Exercise the local cloud-learning workflow against a running app."""

import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REQUESTS_FILE = ROOT / "demo" / "learning_requests.json"
OUTPUT_DIR = ROOT / "demo" / "outputs_learning"
STATE_FILE = OUTPUT_DIR / "state.json"
PROVISIONAL_DIR = ROOT / "data" / "blueprints_provisional"
APPROVED_DIR = ROOT / "data" / "blueprints"
MAX_REQUESTS = 15
POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 30

CLOUD_ANSWER_PREFIX = (
    "[Cloud answer, unverified; no approved knowledge existed for this issue]"
)
PROVISIONAL_PREFIX = "[Provisional blueprint: auto-generated, not yet reviewed]"


def validate_base_url(base_url):
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ValueError("base URL must be a valid localhost HTTP URL") from exc
    if (
        parsed.scheme != "http"
        or hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base URL must use HTTP on localhost, 127.0.0.1, or ::1")
    return base_url.rstrip("/") + "/troubleshoot"


def _response_document(response_body):
    try:
        document = json.loads(response_body)
    except (json.JSONDecodeError, TypeError):
        return {}
    return document if isinstance(document, dict) else {}


def classify_stage(response_body):
    solution = _response_document(response_body).get("solution", "")
    if not isinstance(solution, str):
        return "early-exit"
    if solution.startswith(CLOUD_ANSWER_PREFIX):
        return "cloud_learning"
    if solution.startswith(PROVISIONAL_PREFIX):
        if "\n[Local candidate selected" in solution:
            return "provisional_local"
        return "early-exit"
    if solution.startswith("[Local candidate selected"):
        return "local"
    if solution.startswith("[Cloud candidate selected"):
        return "cloud_fallback"
    return "early-exit"


def _candidate_from_solution(solution):
    decoder = json.JSONDecoder()
    start = solution.find("{")
    if start < 0:
        return {}
    try:
        candidate, _ = decoder.raw_decode(solution[start:])
    except json.JSONDecodeError:
        return {}
    return candidate if isinstance(candidate, dict) else {}


def _diagnosis_id(response_body):
    document = _response_document(response_body)
    solution = document.get("solution", "")
    if not isinstance(solution, str):
        return "unavailable"
    candidate = _candidate_from_solution(solution)
    diagnosis = candidate.get("diagnosis", {})
    if isinstance(diagnosis, dict) and isinstance(diagnosis.get("id"), str):
        return diagnosis["id"]
    diagnosis_id = candidate.get("diagnosis_id")
    return diagnosis_id if isinstance(diagnosis_id, str) else "unavailable"


def _early_exit_note(response_body):
    document = _response_document(response_body)
    solution = document.get("solution", "")
    if not isinstance(solution, str):
        return "failed_check"
    candidate = _candidate_from_solution(solution)
    diagnosis = candidate.get("diagnosis", {})
    if (
        "available information is insufficient" in solution.lower()
        or isinstance(diagnosis, dict)
        and diagnosis.get("status") == "insufficient_evidence"
    ):
        return "decline"
    return "failed_check"


def _snapshot(directory):
    if not directory.exists():
        return set()
    return {path.name for path in directory.iterdir() if path.is_file()}


def _new_draft(before):
    if not PROVISIONAL_DIR.exists():
        return None
    new_files = sorted(
        path.name
        for path in PROVISIONAL_DIR.glob("*.json")
        if path.name not in before
    )
    return new_files[0] if new_files else None


def _post_issue(endpoint, issue):
    request = Request(
        endpoint,
        data=json.dumps({"issue": issue}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    http_status = None
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            http_status = response.status
            response_body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        http_status = exc.code
        response_body = exc.read().decode("utf-8", errors="replace")
    except (URLError, OSError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else str(exc)
        response_body = json.dumps({"transport_error": str(reason)})
    return http_status, response_body, time.perf_counter() - started


class RequestBudget:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.count = 0

    def post(self, issue):
        if self.count >= MAX_REQUESTS:
            return None
        self.count += 1
        return _post_issue(self.endpoint, issue)


def _save_response(scenario, phase, issue, result):
    http_status, response_body, seconds = result
    stage = classify_stage(response_body)
    record = {
        "scenario": scenario,
        "phase": phase,
        "issue": issue,
        "http_status": http_status,
        "seconds": round(seconds, 3),
        "stage": stage,
        "response_body": response_body,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"{scenario}_{phase}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return record


def _load_scenarios():
    scenarios = json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(scenarios, dict) or not all(
        isinstance(name, str) and isinstance(issue, str)
        for name, issue in scenarios.items()
    ):
        raise ValueError("learning request file must map scenario names to issue text")
    return scenarios


def _load_state():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not all(
        isinstance(scenario, str)
        and isinstance(filename, str)
        and Path(filename).name == filename
        and filename.endswith(".json")
        for scenario, filename in state.items()
    ):
        raise ValueError("learning state must map scenario names to draft JSON filenames")
    return state


def _summary(rows):
    lines = [
        "| scenario | phase | stage | diagnosis id | seconds | note |",
        "|---|---|---|---|---:|---|",
    ]
    for row in rows:
        note = row["note"].replace("|", "\\|")
        lines.append(
            f"| {row['scenario']} | {row['phase']} | {row['stage']} "
            f"| {row['diagnosis_id']} | {row['seconds']:.3f} | {note} |"
        )
    summary = "\n".join(lines) + "\n"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "summary.md").write_text(summary, encoding="utf-8")
    print(summary, end="")


def _row(scenario, phase, record, note):
    return {
        "scenario": scenario,
        "phase": phase,
        "stage": record["stage"],
        "diagnosis_id": _diagnosis_id(record["response_body"]),
        "seconds": record["seconds"],
        "note": note,
    }


def _learn(scenarios, budget):
    rows = []
    drafts = {}
    cap_reached = False

    for scenario, issue in scenarios.items():
        before = _snapshot(PROVISIONAL_DIR) | _snapshot(APPROVED_DIR)
        result = budget.post(issue)
        if result is None:
            cap_reached = True
            break
        first = _save_response(scenario, "first", issue, result)
        if first["stage"] != "cloud_learning":
            note = (
                _early_exit_note(first["response_body"])
                if first["stage"] == "early-exit"
                else "unexpected_hit"
            )
            rows.append(_row(scenario, "first", first, note))
            continue

        poll_started = time.perf_counter()
        deadline = poll_started + POLL_TIMEOUT_SECONDS
        draft = _new_draft(before)
        while draft is None and time.perf_counter() < deadline:
            time.sleep(min(POLL_INTERVAL_SECONDS, deadline - time.perf_counter()))
            draft = _new_draft(before)
        wait_seconds = time.perf_counter() - poll_started
        if draft is None:
            rows.append(
                _row(
                    scenario,
                    "first",
                    first,
                    f"no_draft after {wait_seconds:.3f}s; repeats skipped",
                )
            )
            continue

        drafts[scenario] = draft
        rows.append(
            _row(
                scenario,
                "first",
                first,
                f"draft={draft}; wait={wait_seconds:.3f}s",
            )
        )
        for repeat_number in range(1, 4):
            phase = f"repeat{repeat_number}"
            repeat_result = budget.post(issue)
            if repeat_result is None:
                cap_reached = True
                break
            repeat = _save_response(scenario, phase, issue, repeat_result)
            note = (
                "expected"
                if repeat["stage"] == "provisional_local"
                else "unexpected_stage"
            )
            rows.append(_row(scenario, phase, repeat, note))
        if cap_reached:
            break

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(drafts, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if cap_reached:
        rows.append({
            "scenario": "-",
            "phase": "-",
            "stage": "-",
            "diagnosis_id": "-",
            "seconds": 0.0,
            "note": f"request cap reached ({MAX_REQUESTS}); remaining requests skipped",
        })
    _summary(rows)


def _after_approval(scenarios, budget):
    rows = []
    state = _load_state()
    cap_reached = False
    for scenario in state:
        if scenario not in scenarios:
            continue
        result = budget.post(scenarios[scenario])
        if result is None:
            cap_reached = True
            break
        record = _save_response(
            scenario,
            "after_approval",
            scenarios[scenario],
            result,
        )
        note = (
            "expected"
            if record["stage"] == "local"
            and PROVISIONAL_PREFIX not in record["response_body"]
            else "unexpected_stage"
        )
        rows.append(_row(scenario, "after_approval", record, note))
    if cap_reached:
        rows.append({
            "scenario": "-",
            "phase": "-",
            "stage": "-",
            "diagnosis_id": "-",
            "seconds": 0.0,
            "note": f"request cap reached ({MAX_REQUESTS}); remaining requests skipped",
        })
    _summary(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Demonstrate the local cloud-learning workflow."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--learn", action="store_true")
    mode.add_argument("--after-approval", action="store_true")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="localhost app base URL (default: http://127.0.0.1:8000)",
    )
    args = parser.parse_args(argv)
    try:
        endpoint = validate_base_url(args.base_url)
        scenarios = _load_scenarios()
        budget = RequestBudget(endpoint)
        if args.learn:
            _learn(scenarios, budget)
        else:
            _after_approval(scenarios, budget)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
