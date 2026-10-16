"""Graph -> CP-SAT: constraints, objective, unknown inputs, fallback."""
from datetime import datetime, timedelta

import pytest
from helpers import NOW, graph, task

from app.config import settings
from app.intel import schedule
from app.intel.schedule import ScheduleResult


def minutes_from_now(iso):
    return int((datetime.fromisoformat(iso) - NOW).total_seconds() // 60)


def mins(res, tid, which="start"):
    return minutes_from_now(getattr(res.tasks[tid], which))


def test_precedence_dependents_start_after_prerequisites_end():
    res = schedule.plan(graph(task("A", 1, est=60), task("B", 2, deps=["A"], est=30)), NOW)
    assert res.status == "OPTIMAL" and res.solver == "cp-sat"
    assert mins(res, "B") >= mins(res, "A", "end")


def test_one_person_cannot_do_two_tasks_at_once():
    res = schedule.plan(graph(task("A", 1, est=60), task("B", 1, est=60)), NOW)
    a = (mins(res, "A"), mins(res, "A", "end"))
    b = (mins(res, "B"), mins(res, "B", "end"))
    assert a[1] <= b[0] or b[1] <= a[0]                      # the two windows do not overlap
    assert minutes_from_now(res.project_end) == 120


def test_two_people_work_in_parallel():
    res = schedule.plan(graph(task("A", 1, est=60), task("B", 2, est=60)), NOW)
    assert minutes_from_now(res.project_end) == 60


def test_lateness_is_reported_against_the_deadline():
    res = schedule.plan(graph(task("A", 1, est=120, deadline_in=90)), NOW)
    assert res.tasks["A"].late_min == 30 and res.tasks["A"].slack_min == -30
    assert res.total_late_min == 30


def test_plenty_of_time_means_positive_slack_and_no_lateness():
    res = schedule.plan(graph(task("A", 1, est=60, deadline_in=300)), NOW)
    assert res.tasks["A"].late_min == 0 and res.tasks["A"].slack_min == 240


def test_deadline_is_soft_so_an_impossible_deadline_still_produces_a_plan():
    res = schedule.plan(graph(task("A", 1, est=600, deadline_in=10)), NOW)
    assert res.status == "OPTIMAL" and res.tasks["A"].late_min == 590


def test_lateness_outranks_makespan_in_the_objective():
    # One person, two tasks. Doing the long one first would finish everything at the same time, but only
    # doing the urgent one first meets its deadline. The solver must choose that.
    g = graph(task("LONG", 1, est=120), task("URGENT", 1, est=30, deadline_in=40))
    res = schedule.plan(g, NOW)
    assert res.tasks["URGENT"].late_min == 0
    assert mins(res, "URGENT", "start") == 0


def test_heavier_bottleneck_gets_scheduled_first_when_both_would_be_late():
    # A blocks two tasks, X blocks nothing; one person, equal durations and deadlines.
    g = graph(task("A", 1, est=60, deadline_in=60), task("X", 1, est=60, deadline_in=60),
              task("B", 2, deps=["A"], est=10), task("C", 3, deps=["A"], est=10))
    res = schedule.plan(g, NOW)
    assert mins(res, "A", "start") == 0                      # weight 3 beats weight 1


def test_completed_prerequisites_do_not_constrain_anything():
    res = schedule.plan(graph(task("A", 1, "Complete"), task("B", 2, deps=["A"], est=30)), NOW)
    assert mins(res, "B") == 0 and "A" not in res.tasks


def test_in_progress_work_counts_only_what_remains():
    res = schedule.plan(graph(task("A", 1, "In Progress", est=100, pct=75)), NOW)
    assert res.tasks["A"].duration_min == 25


def test_all_complete_is_no_work_not_an_error():
    res = schedule.plan(graph(task("A", 1, "Complete")), NOW)
    assert res.status == "NO_WORK" and not res.usable


def test_dependency_cycle_is_an_invalid_graph_not_a_crash():
    res = schedule.plan(graph(task("A", 1, deps=["B"]), task("B", 2, deps=["A"])), NOW)
    assert res.status == "INVALID_GRAPH" and "cycle" in res.note and not res.usable


# ---------------------------------------------------------------- uncertainty is explicit, never zero

def test_missing_estimate_uses_a_flagged_default_and_lowers_confidence():
    res = schedule.plan(graph(task("A", 1, est=None)), NOW)
    assert res.tasks["A"].duration_min == settings.intel_default_task_min > 0
    assert any("no estimate" in a for a in res.assumptions)
    assert res.confidence == "LOW"                           # every duration is a guess


def test_some_assumed_durations_give_medium_confidence():
    res = schedule.plan(graph(task("A", 1, est=60), task("B", 2, est=60), task("C", 3, est=None)), NOW)
    assert res.confidence == "MEDIUM"


def test_blocked_with_unknown_unblock_time_is_pessimistic_never_zero():
    g = graph(task("A", 1, "Blocked", est=60, blocker="credentials", blocked_by=9))
    res = schedule.plan(g, NOW)
    assert mins(res, "A") == settings.intel_unknown_unblock_min > 0
    assert res.confidence == "LOW"
    assert any("assumed" in a and "credentials" in a for a in res.assumptions)


def test_a_promised_time_becomes_the_earliest_start_and_is_marked_unverified():
    g = graph(task("A", 1, "Blocked", est=60, blocker="credentials", blocked_by=9))
    res = schedule.plan(g, NOW, {"A": NOW + timedelta(minutes=20)})
    assert mins(res, "A") == 20
    assert res.confidence == "MEDIUM"
    assert any("unverified" in a for a in res.assumptions)


def test_an_expired_promise_falls_back_to_the_pessimistic_placeholder():
    g = graph(task("A", 1, "Blocked", est=60, blocker="credentials", blocked_by=9))
    res = schedule.plan(g, NOW, {"A": NOW - timedelta(minutes=5)})
    assert mins(res, "A") == settings.intel_unknown_unblock_min
    assert any("promised time has passed" in a for a in res.assumptions)


def test_blocked_on_a_prerequisite_needs_no_placeholder_precedence_covers_it():
    g = graph(task("CRED", 2, "In Progress", est=60, pct=50),
              task("API", 1, "Blocked", ["CRED"], est=60, blocker="credentials", blocked_by=2))
    res = schedule.plan(g, NOW)
    assert mins(res, "API") == mins(res, "CRED", "end") == 30
    assert res.confidence == "HIGH" and res.assumptions == []


def test_a_resolved_blocker_adds_no_delay():
    g = graph(task("A", 1, "Blocked", est=60, blocker="x", blocked_by=9, resolved=True))
    assert mins(schedule.plan(g, NOW), "A") == 0


# ---------------------------------------------------------------- failure handling

def test_solver_missing_falls_back_to_a_feasible_dependency_order_schedule(monkeypatch):
    def boom(*a, **k):
        raise ImportError("ortools not installed")
    monkeypatch.setattr(schedule, "solve_cpsat", boom)
    g = graph(task("A", 1, est=60), task("B", 1, est=30, deps=["A"]), task("C", 2, est=30, deps=["B"], deadline_in=500))
    res = schedule.plan(g, NOW)
    assert res.status == "FALLBACK_CPM" and res.solver == "cpm" and res.usable
    assert "ImportError" in res.note
    assert mins(res, "B") >= mins(res, "A", "end") and mins(res, "C") >= mins(res, "B", "end")


def test_fallback_still_serialises_one_person(monkeypatch):
    monkeypatch.setattr(schedule, "solve_cpsat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    res = schedule.plan(graph(task("A", 1, est=60), task("B", 1, est=60)), NOW)
    spans = sorted((mins(res, t), mins(res, t, "end")) for t in ("A", "B"))
    assert spans[0][1] <= spans[1][0]


@pytest.mark.parametrize("bad", ["INFEASIBLE", "UNKNOWN"])
def test_unusable_solver_status_triggers_the_fallback(monkeypatch, bad):
    monkeypatch.setattr(schedule, "solve_cpsat", lambda *a, **k: {"status": bad, "ms": 1, "start": {}})
    res = schedule.plan(graph(task("A", 1, est=60)), NOW)
    assert res.status == "FALLBACK_CPM" and bad in res.note


def test_result_round_trips_through_json():
    res = schedule.plan(graph(task("A", 1, est=60, deadline_in=30), task("B", 2, deps=["A"])), NOW)
    again = ScheduleResult.from_dict(res.to_dict())
    assert again == res


def test_version_is_carried_on_the_result():
    g = graph(task("A", 1))
    assert schedule.plan(g, NOW, version="abc123").version == "abc123"
    assert schedule.plan(g, NOW).version == g.version()
