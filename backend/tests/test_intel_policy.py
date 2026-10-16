"""Risk engine (evidence, attribution), message writer and reply parsing. No database."""
from datetime import timedelta

import pytest
from helpers import NOW, graph, task

from app.intel import compose, risk, schedule
from app.intel.planner import ActionProposal
from app.intel.responses import is_delivery, parse_eta


def blocker_graph(**over):
    """A is done; B (P2) is blocked on something P9 must provide; C and D wait behind B."""
    return graph(task("A", 1, "Complete"),
                 task("B", 2, "Blocked", ["A"], est=60, blocker="test credentials", blocked_by=9, **over),
                 task("C", 3, "Pending", ["B"], est=60), task("D", 1, "Pending", ["C"], est=60, deadline_in=300))


def kinds(risks):
    return [r.risk_type for r in risks]


# ---------------------------------------------------------------- risk: structural (no solver needed)

def test_confirmed_blocker_is_reported_even_when_there_is_no_schedule():
    g = blocker_graph()
    risks = risk.detect(g, None, None, [], NOW)
    assert "CONFIRMED_BLOCKER" in kinds(risks) and "PLAN_UNAVAILABLE" in kinds(risks)
    b = next(r for r in risks if r.risk_type == "CONFIRMED_BLOCKER")
    assert b.task_id == "B" and b.responsible_member_id == 9 and b.blocker == "test credentials"
    assert {a["task_id"] for a in b.affected} == {"C", "D"}
    assert any("dependency path: B -> C -> D" in e for e in b.evidence)


def test_a_pending_promise_suppresses_the_blocker_risk():
    g = blocker_graph()
    cur = schedule.plan(g, NOW, {"B": NOW + timedelta(minutes=20)})
    commit = [{"action_id": 1, "task_id": "B", "recipient_id": 9, "commitment_at": NOW + timedelta(minutes=20)}]
    assert "CONFIRMED_BLOCKER" not in kinds(risk.detect(g, cur, None, commit, NOW))


def test_an_expired_promise_becomes_an_overdue_risk_pointing_at_the_promiser():
    g = blocker_graph()
    commit = [{"action_id": 4, "task_id": "B", "recipient_id": 9, "commitment_at": NOW - timedelta(minutes=12)}]
    cur = schedule.plan(g, NOW, {"B": commit[0]["commitment_at"]})
    r = next(x for x in risk.detect(g, cur, None, commit, NOW) if x.risk_type == "COMMITMENT_OVERDUE")
    assert r.responsible_member_id == 9 and r.commitment_action_id == 4
    assert "12 min" in r.summary and r.promised_at


def test_a_resolved_blocker_raises_no_blocker_risk():
    g = graph(task("B", 2, "Blocked", est=60, blocker="x", blocked_by=9, resolved=True))
    assert "CONFIRMED_BLOCKER" not in kinds(risk.detect(g, schedule.plan(g, NOW), None, [], NOW))


def test_a_dependency_cycle_is_reported_once_and_nothing_else_is():
    g = graph(task("A", 1, deps=["B"]), task("B", 2, deps=["A"]))
    risks = risk.detect(g, schedule.plan(g, NOW), None, [], NOW)
    assert kinds(risks) == ["GRAPH_INVALID"] and risks[0].severity == "HIGH"


# ---------------------------------------------------------------- risk: scheduling and attribution

def baseline_and_blocked():
    """Same project before and after B becomes blocked on an external party."""
    before = graph(task("A", 1, "Complete"), task("B", 2, "Pending", ["A"], est=60), task("C", 3, "Pending", ["B"], est=60),
                   task("D", 1, "Pending", ["C"], est=60, deadline_in=300))
    return before, blocker_graph()


def test_forecast_slip_is_attributed_to_the_changed_task_along_a_dependency_path():
    before, after = baseline_and_blocked()
    prev, cur = schedule.plan(before, NOW), schedule.plan(after, NOW)
    assert prev.total_late_min == 0 and cur.total_late_min > 0
    r = next(x for x in risk.detect(after, cur, prev, [], NOW) if x.risk_type == "DEADLINE_RISK")
    assert r.contributing_task == "B" and r.attribution == "direct"
    assert r.delay_minutes > 0 and r.previous_forecast == prev.project_end and r.new_forecast == cur.project_end
    assert r.severity == "HIGH" and r.responsible_member_id == 9          # the person who can unblock B
    assert any("input changed for 'B'" in e for e in r.evidence)
    assert any("dependency path: B -> C -> D" in e for e in r.evidence)
    assert any("assumption" in e and "pessimistic" in e for e in r.evidence)
    assert r.confidence == "LOW"


def test_without_a_before_picture_the_cause_is_not_claimed():
    g = blocker_graph()
    r = next(x for x in risk.detect(g, schedule.plan(g, NOW), None, [], NOW) if x.risk_type == "DEADLINE_RISK")
    assert r.attribution == "none"
    assert any("no recent input change" in e for e in r.evidence)


def test_several_changed_tasks_are_reported_as_possible_not_certain():
    mk = lambda st: graph(task("B1", 2, st, est=60, blocker="x" if st == "Blocked" else None, blocked_by=9 if st == "Blocked" else None),  # noqa: E731
                          task("B2", 3, st, est=60, blocker="y" if st == "Blocked" else None, blocked_by=9 if st == "Blocked" else None),
                          task("D", 1, "Pending", ["B1", "B2"], est=60, deadline_in=300))
    prev, g = schedule.plan(mk("Pending"), NOW), mk("Blocked")
    r = next(x for x in risk.detect(g, schedule.plan(g, NOW), prev, [], NOW) if x.risk_type == "DEADLINE_RISK")
    assert r.attribution == "possible" and any("could each explain" in e for e in r.evidence)


def test_no_deadline_risk_when_everything_fits():
    g = graph(task("A", 1, est=60, deadline_in=600), task("B", 2, deps=["A"], est=60, deadline_in=600))
    assert risk.detect(g, schedule.plan(g, NOW), None, [], NOW) == []


def test_overlapping_owner_work_is_reported_only_when_it_causes_lateness():
    late = graph(task("A", 1, est=60, deadline_in=60), task("B", 1, est=60, deadline_in=60))
    assert "OWNER_OVERLOAD" in kinds(risk.detect(late, schedule.plan(late, NOW), None, [], NOW))
    fine = graph(task("A", 1, est=60, deadline_in=600), task("B", 1, est=60, deadline_in=600))
    assert "OWNER_OVERLOAD" not in kinds(risk.detect(fine, schedule.plan(fine, NOW), None, [], NOW))


def test_changed_inputs_lists_what_moved():
    before, after = baseline_and_blocked()
    changes = risk.changed_inputs(schedule.plan(before, NOW), schedule.plan(after, NOW))
    assert any("status" in c for c in changes["B"]) and any("earliest" in c for c in changes["B"])
    assert "A" not in changes


def test_completed_task_shows_up_as_a_change_that_lowers_risk():
    a = graph(task("A", 1, est=60), task("B", 2, deps=["A"], est=60))
    b = graph(task("A", 1, "Complete"), task("B", 2, deps=["A"], est=60))
    assert risk.changed_inputs(schedule.plan(a, NOW), schedule.plan(b, NOW))["A"] == ["completed"]


# ---------------------------------------------------------------- message writer

def proposal(g, cur, kind="REQUEST_ETA", recipient=9, tid="B"):
    risks = risk.detect(g, cur, None, [], NOW)
    return ActionProposal(kind, recipient, tid, "CONFIRMED_BLOCKER", "HIGH", [r for r in risks if r.task_id == tid][:1])


def test_message_states_who_what_why_and_asks_for_an_eta():
    g = blocker_graph()
    text = compose.render(proposal(g, schedule.plan(g, NOW)), g, None, NOW)
    assert text.startswith("{M9}")
    assert "<b>P2</b>" in text and "<b>B</b>" in text and "<i>test credentials</i>" in text
    assert "<b>C</b>" in text and "<b>D</b>" in text                        # what it holds up
    assert "ETA" in text or "when it will be ready" in text


def test_forecast_numbers_are_hedged_when_inputs_are_assumed():
    before, after = baseline_and_blocked()
    cur = schedule.plan(after, NOW)
    risks = risk.detect(after, cur, schedule.plan(before, NOW), [], NOW)
    p = ActionProposal("REQUEST_ETA", 9, "B", "DEADLINE_RISK", "HIGH", risks[:2])
    text = compose.render(p, after, cur, NOW)
    assert "would finish" in text and "an estimate" in text


def test_clarification_asks_the_owner_when_nobody_can_unblock():
    g = graph(task("B", 2, "Blocked", est=60, blocker="x"))
    p = ActionProposal("REQUEST_CLARIFICATION", 2, "B", "CONFIRMED_BLOCKER", "LOW", risk.detect(g, None, None, [], NOW)[:1])
    assert "Who should I ask" in compose.render(p, g, None, NOW)


def test_escalation_names_who_was_asked():
    g = blocker_graph()
    p = ActionProposal("ESCALATE_BLOCKER", 1, "B", "CONFIRMED_BLOCKER", "HIGH", risk.detect(g, None, None, [], NOW)[:1])
    assert "escalating" in compose.render(p, g, None, NOW) and "step in" in compose.render(p, g, None, NOW)


ORIGINAL = "{M9} <b>Kabir</b> is blocked on <b>API</b>. Forecast: late by 2h 00m.\nCan you share it?"


@pytest.mark.parametrize("rewrite,ok", [
    ("{M9} Heads up: <b>Kabir</b> is stuck on <b>API</b>. Forecast: late by 2h 00m. Could you share it?", True),
    ("Heads up: <b>Kabir</b> is stuck on <b>API</b>. Forecast: late by 2h 00m.", False),         # lost the mention
    ("{M9} <b>Kabir</b> is stuck on <b>API</b>. Forecast: late by 5h.", False),                   # invented a number
    ("{M9} Kabir is stuck on API. Forecast: late by 2h 00m.", False),                              # lost the bold names
    ("{M9} <b>Kabir</b> is stuck on <b>API</b> 2h 00m <script>x</script>", False),               # disallowed markup
    ("{M9}{M3} <b>Kabir</b> <b>API</b> 2h 00m", False),                                           # invented a recipient
    ("", False),
    ("{M9} <b>Kabir</b> <b>API</b> 2h 00m " + "blah " * 100, False),                               # runaway length
])
def test_llm_rewrites_are_accepted_only_if_they_preserve_the_facts(rewrite, ok):
    assert compose.valid_rewrite(ORIGINAL, rewrite) is ok


@pytest.mark.parametrize("secs,expected", [(0, "0 min"), (45, "45 min"), (60, "1h"), (125, "2h 05m")])
def test_human_durations(secs, expected):
    assert compose.human(secs) == expected


# ---------------------------------------------------------------- reading replies

@pytest.mark.parametrize("text,minutes", [
    ("I'll send them in 20 minutes", 20), ("give me 45 mins", 45), ("about 2 hours", 120), ("1.5 hrs", 90),
    ("2 hours 30 minutes", 150), ("half an hour", 30), ("in an hour", 60), ("10 min", 10),
])
def test_relative_etas(text, minutes):
    assert parse_eta(text, NOW) == NOW + timedelta(minutes=minutes)


def test_clock_time_eta_rolls_to_tomorrow_if_already_past():
    eta = parse_eta("by 5pm", NOW)
    assert eta is not None and eta > NOW and eta.astimezone().hour == 17 and eta.astimezone().minute == 0


@pytest.mark.parametrize("text", ["soon", "tomorrow", "will do", "no idea", "lol", "by 5", "999 hours", ""])
def test_vague_answers_are_not_turned_into_a_time(text):
    assert parse_eta(text, NOW) is None


@pytest.mark.parametrize("text,delivered", [
    ("sent them", True), ("done", True), ("I shared the keys on email", True), ("✅", True),
    ("I'll send them in 20 minutes", False), ("will share soon", False), ("sent soon, will arrive in 5 mins", False),
    ("not yet", False),
])
def test_delivery_claims_versus_promises(text, delivered):
    assert is_delivery(text) is delivered
