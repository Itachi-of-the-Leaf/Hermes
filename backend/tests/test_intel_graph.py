"""Graph intelligence: direction convention, traversals, cycles, CPM, priority."""
import pytest
from helpers import graph, task

from app.intel.graph import CycleError, parse_deadline, priority_scores


def chain():
    # D depends on C depends on B depends on A.  E depends on B too (a second branch).
    return graph(task("A", 1, "Complete"), task("B", 2, "Blocked", ["A"], blocker="creds", blocked_by=1),
                 task("C", 3, "Pending", ["B"]), task("D", 1, "Pending", ["C"]), task("E", 2, "Pending", ["B"]))


def test_depends_on_direction_downstream_walks_backwards():
    g = chain()
    assert g.tasks["C"].deps == ["B"]                       # (C)-[:DEPENDS_ON]->(B)
    assert g.dependents["B"] == ["C", "E"]                  # reverse edges
    assert set(g.downstream("B")) == {"C", "D", "E"}
    assert g.downstream("D") == {}                          # nothing waits for the last task


def test_downstream_returns_a_dependency_path():
    assert chain().downstream("B")["D"] == ["B", "C", "D"]


def test_downstream_skips_completed_tasks_by_default():
    g = graph(task("A"), task("B", 2, "Complete", ["A"]), task("C", 3, "Pending", ["B"]))
    assert g.downstream("A") == {}                          # B is done, and C is only reachable through it
    assert "B" in g.downstream("A", open_only=False)


def test_topological_order_puts_prerequisites_first():
    order = chain().topo_order()
    for t in chain().tasks.values():
        for d in t.deps:
            assert order.index(d) < order.index(t.id)


def test_cycle_is_detected_and_reported():
    g = graph(task("A", 1, deps=["C"]), task("B", 2, deps=["A"]), task("C", 3, deps=["B"]))
    assert g.cycle and g.cycle[0] == g.cycle[-1]
    assert set(g.cycle) == {"A", "B", "C"}
    with pytest.raises(CycleError):
        g.topo_order()
    assert any("cycle" in i for i in g.issues)


def test_self_dependency_and_unknown_reference_are_flagged_not_fatal():
    g = graph(task("A", 1, deps=["A", "ZZZ"]), task("B", 2, deps=["A"]))
    assert g.tasks["A"].deps == []
    assert any("itself" in i for i in g.issues) and any("unknown task ZZZ" in i for i in g.issues)
    assert g.cycle is None


def test_blocked_waiting_and_affected_are_different_things():
    g = chain()
    assert [t.id for t in g.blocked_tasks()] == ["B"]       # reported blocked
    assert g.open_deps("C") == ["B"]                        # C is merely *waiting* on B
    assert not g.tasks["C"].is_blocked
    assert set(g.affected_by("B")) == {"C", "D", "E"}       # potentially affected, not necessarily stuck


def test_a_resolved_blocker_is_no_longer_blocked():
    g = graph(task("A", 1, "Blocked", blocker="x", resolved=True))
    assert g.blocked_tasks() == []


def test_frontier_is_where_someone_can_act_now():
    g = graph(task("A", 1, "In Progress"), task("B", 2, "Pending", ["A"]), task("C", 3, "Pending", ["B"]))
    assert g.frontier("C") == ["A"]
    assert g.frontier("A") == ["A"]


def test_bottlenecks_rank_by_work_waiting_behind():
    g = graph(task("A"), task("B", 2, deps=["A"]), task("C", 3, deps=["A"]), task("D", 4, deps=["B"]))
    assert g.bottlenecks()[0] == ("A", 3)


def test_cpm_finds_the_critical_path_and_slack():
    g = graph(task("A"), task("B", 2, deps=["A"]), task("C", 3, deps=["A"]))
    cpm = g.cpm({"A": 60, "B": 120, "C": 30})
    assert cpm["B"]["critical"] and cpm["A"]["critical"] and not cpm["C"]["critical"]
    assert cpm["C"]["slack"] == 90


def test_owner_conflict_when_one_person_has_overlapping_work():
    g = graph(task("A", 1), task("B", 1), task("C", 2, deps=["A"]))
    conflicts = g.owner_conflicts(g.cpm({"A": 60, "B": 60, "C": 60}))
    assert conflicts == [{"owner": "P1", "owner_id": 1, "tasks": ["A", "B"]}]


def test_no_conflict_when_work_is_sequential():
    g = graph(task("A", 1), task("B", 1, deps=["A"]))
    assert g.owner_conflicts(g.cpm({"A": 60, "B": 60})) == []


def test_version_changes_only_when_scheduling_inputs_change():
    a, b = chain(), chain()
    assert a.version() == b.version()
    b.tasks["C"].est_minutes = 999
    assert a.version() != b.version()
    assert a.version() != a.version(extra=[["B", "2026-10-17T10:00"]])


def test_deadline_parsing():
    assert parse_deadline(None) is None
    assert parse_deadline("garbage") is None
    d = parse_deadline("2026-10-17")
    assert (d.hour, d.minute) == (23, 59)                   # a bare date means end of day
    assert parse_deadline("2026-10-17T16:00:00+05:30").hour == 16


def test_priority_combines_urgency_impact_blocker_and_resource():
    g = chain()
    w = (0.35, 0.30, 0.20, 0.15)
    pri = priority_scores(g, {"B": -30, "C": 600, "D": 600, "E": None}, w)
    assert pri["B"]["score"] > pri["D"]["score"]            # late + blocked + most downstream work
    assert pri["B"]["urgency"] == 1.0 and pri["B"]["blocker"] == 1.0
    assert pri["C"]["blocker"] == 0.5                       # waits on a blocked task
    assert all(0 <= v["score"] <= 100 for v in pri.values())
