import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REQUESTS_FILE = ROOT / "demo" / "requests.json"
OUTPUT_DIR = ROOT / "demo" / "outputs"
ENDPOINT = "http://127.0.0.1:8000/troubleshoot"
RUNS_PER_SCENARIO = 3


def parse_response(response_body):
    try:
        response_data = json.loads(response_body)
    except json.JSONDecodeError:
        return None, response_body
    solution = response_data.get("solution", "")
    if not isinstance(solution, str):
        return response_data, ""
    start = solution.find("{")
    if start < 0:
        return response_data, solution
    try:
        candidate, _ = json.JSONDecoder().raw_decode(solution[start:])
    except json.JSONDecodeError:
        return response_data, solution
    return response_data, candidate


def row_fields(response_body, http_status):
    response_data, candidate = parse_response(response_body)
    solution = response_data.get("solution", "") if isinstance(response_data, dict) else ""
    stage_match = re.match(r"^\[(Local|Cloud) candidate selected\b", solution)
    stage = stage_match.group(1).lower() if stage_match else "early-exit"
    diagnosis = candidate.get("diagnosis", {}) if isinstance(candidate, dict) else {}
    evidence = candidate.get("evidence", []) if isinstance(candidate, dict) else []
    actions = candidate.get("recommended_actions", []) if isinstance(candidate, dict) else []
    cloud_error = (
        "openrouter_api_key is not configured" in solution.lower()
        or "cloud escalation failed" in solution.lower()
        or "no candidate passed runtime validation" in solution.lower()
    )
    return {
        "diagnosis_id": diagnosis.get("id", "unavailable"),
        "status": diagnosis.get("status", f"http_{http_status}" if http_status else "unavailable"),
        "evidence_ids": [item.get("observation_id", "") for item in evidence if isinstance(item, dict)],
        "actions": [item.get("action_id", "") for item in actions if isinstance(item, dict)],
        "resolution_claim": candidate.get("resolution_claim", "unavailable") if isinstance(candidate, dict) else "unavailable",
        "stage": stage,
        "cloud_error": cloud_error,
    }


def send_request(issue):
    request = Request(
        ENDPOINT,
        data=json.dumps({"issue": issue}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    http_status = None
    try:
        with urlopen(request, timeout=300) as response:
            http_status = response.status
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        http_status = exc.code
        response_body = exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        response_body = json.dumps({"transport_error": str(exc.reason)})
    elapsed_seconds = time.perf_counter() - started
    return http_status, response_body, elapsed_seconds


def main():
    if os.environ.get("OPENROUTER_API_KEY", "") != "":
        raise RuntimeError("Demo requires an empty or absent OPENROUTER_API_KEY.")
    if os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
        raise RuntimeError("Demo requires PYTHON_DOTENV_DISABLED=1.")

    scenarios = json.loads(REQUESTS_FILE.read_text(encoding="utf-8"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    cloud_flags = []

    for scenario_id, issue in scenarios.items():
        for run_number in range(1, RUNS_PER_SCENARIO + 1):
            http_status, response_body, elapsed_seconds = send_request(issue)
            fields = row_fields(response_body, http_status)
            record = {
                "scenario": scenario_id,
                "run": run_number,
                "issue": issue,
                "elapsed_seconds": elapsed_seconds,
                "http_status": http_status,
                "response_body": response_body,
            }
            output_path = OUTPUT_DIR / f"{scenario_id}_run{run_number}.json"
            output_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            rows.append((scenario_id, run_number, fields, elapsed_seconds))
            if fields["stage"] == "cloud" or fields["cloud_error"]:
                cloud_flags.append((scenario_id, run_number, fields["stage"], fields["cloud_error"]))

    print("| scenario | run | diagnosis id | status | evidence ids | actions | resolution_claim | stage | seconds |")
    print("|---|---:|---|---|---|---|---|---|---:|")
    for scenario_id, run_number, fields, elapsed_seconds in rows:
        print(
            f"| {scenario_id} | {run_number} | {fields['diagnosis_id']} | {fields['status']} "
            f"| {', '.join(fields['evidence_ids']) or '-'} | {', '.join(fields['actions']) or '-'} "
            f"| {fields['resolution_claim']} | {fields['stage']} | {elapsed_seconds:.3f} |"
        )
    if cloud_flags:
        print("CLOUD FLAGS:")
        for scenario_id, run_number, stage, cloud_error in cloud_flags:
            print(
                f"{scenario_id} run {run_number}: stage={stage}; "
                f"cloud_error_indicator={cloud_error}"
            )


if __name__ == "__main__":
    main()
