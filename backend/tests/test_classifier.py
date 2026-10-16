"""Labelled replies -> expected status, run through the offline engine (rules + gate).
Extend this list as you collect real replies; print accuracy with `pytest -q -s`."""
import asyncio

import pytest

from app.pipeline.decide import gate, interpret
from app.pipeline.types import TaskContext

CTX = TaskContext(task_id="T2", title="Connect the payment API", status="Pending", owner="Kabir",
                  depends_on=["Get sandbox test credentials"], team=["Aanya", "Kabir", "Meera", "Priya"])

# (reply, expected status or None for "no status change")
CASES = [
    ("The API is connected, but I am waiting for test credentials.", "Blocked"),
    ("connected, but waiting for test credentials", "Blocked"),
    ("done", "Complete"),
    ("done ✅ pushed to main", "Complete"),
    ("tested, all green, PR #14", "Complete"),
    ("almost done, just testing", "In Progress"),
    ("80% done, will finish tonight", "In Progress"),
    ("kal tak ho jayega", "In Progress"),
    ("working on it", "In Progress"),
    ("checkout page is about 60% done, will finish tonight", "In Progress"),
    ("haven't started, need Priya's keys first", "Blocked"),
    ("stuck", "Blocked"),
    ("can't log into the sandbox, no access", "Blocked"),
    ("sick this week, can someone take it?", "Blocked"),
    ("which task?", None),
    ("lol", None),
]


def run(text):
    i = asyncio.run(interpret(text, CTX))
    return gate(i, CTX.status)


@pytest.mark.parametrize("text,expected", CASES)
def test_reply(text, expected):
    d = run(text)
    assert d.new_status == expected, f"{text!r} → {d.new_status} ({d.interp.trace}, {d.reasons})"


def test_kabir_blocker_recorded():
    d = run("The API is connected, but I am waiting for test credentials.")
    assert d.new_status == "Blocked"
    assert d.interp.blocker_text == "test credentials"
    assert d.interp.blocker_type == "external"


def test_never_complete_on_blocked_text():
    for text, exp in CASES:
        if exp == "Blocked":
            assert run(text).new_status != "Complete"


def test_accuracy_report():
    hits = sum(run(t).new_status == e for t, e in CASES)
    print(f"\nrules engine accuracy: {hits}/{len(CASES)} = {hits / len(CASES):.0%}")
    assert hits / len(CASES) >= 0.9
