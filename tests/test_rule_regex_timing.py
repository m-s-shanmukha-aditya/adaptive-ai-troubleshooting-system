from time import perf_counter

import pytest

from app.services.rule_interpreter import MAX_REPORT_CHARS, RuleInterpreter
from tests.fixtures.postgres_rules import build_postgres_rules_blueprint


def _repeat_to_limit(text: str) -> str:
    repeats = (MAX_REPORT_CHARS + len(text) - 1) // len(text)
    return (text * repeats)[:MAX_REPORT_CHARS]


ADVERSARIAL_REPORTS = [
    (
        "postgres plus repeated port",
        "postgres " + ("port " * 798) + "x",
    ),
    (
        "repeated word without punctuation",
        ("postgres " * 444) + "xxxx",
    ),
    (
        "credentials context near-match",
        _repeat_to_limit("MongoDB username or password is wrongx "),
    ),
    (
        "configured-port context near-match",
        _repeat_to_limit("Elasticsearch client is configured to use port 5432 "),
    ),
    (
        "missing-database context near-match",
        _repeat_to_limit("Redis key does not exist "),
    ),
]


@pytest.mark.parametrize(("label", "report"), ADVERSARIAL_REPORTS)
def test_interpreter_finishes_adversarial_reports_under_half_second(label, report):
    assert len(report) == MAX_REPORT_CHARS
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()

    started = perf_counter()
    interpreter.interpret(report, blueprint)
    elapsed = perf_counter() - started

    print(f"{label}: {elapsed:.6f}s")
    assert elapsed < 0.5, f"{label} took {elapsed:.6f}s"
