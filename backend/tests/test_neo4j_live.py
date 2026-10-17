"""The Neo4j mirror against a REAL database. Skipped unless NEO4J_LIVE_URI is set, e.g.

    NEO4J_LIVE_URI=bolt://localhost:7687 NEO4J_LIVE_PASSWORD=... pytest tests/test_neo4j_live.py

(Start one with:  docker run -d -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5)
The test wipes everything in the target database, so point it at a throwaway instance.
"""
import os

import pytest
from helpers import NOW, graph, task

from app.intel import neo4j_mirror as nm
from app.intel import schedule

URI = os.environ.get("NEO4J_LIVE_URI")
pytestmark = pytest.mark.skipif(not URI, reason="set NEO4J_LIVE_URI to run against a real Neo4j")


@pytest.fixture()
def db():
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(URI, auth=(os.environ.get("NEO4J_LIVE_USER", "neo4j"), os.environ["NEO4J_LIVE_PASSWORD"]))
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    yield driver
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    driver.close()


def rows(driver, query, **params):
    with driver.session() as s:
        return [r.data() for r in s.run(query, **params)]


def project():
    # A (done) <- B (blocked, P2) <- C (P3) <- D (P1);  E (P2) also waits for B;  F is independent (P1)
    return graph(task("A", 1, "Complete"), task("B", 2, "Blocked", ["A"], blocker="creds", blocked_by=1, est=60),
                 task("C", 3, "Pending", ["B"], est=60), task("D", 1, "Pending", ["C"], est=60, deadline_in=500),
                 task("E", 2, "Pending", ["B"], est=30), task("F", 1, "In Progress", est=60))


def sync(db, g, **kw):
    return nm.sync(g, schedule.plan(g, NOW), driver=db, **kw)


def test_schema_nodes_and_relationships_are_created(db):
    g = project()
    assert sync(db, g) == 6
    assert {r["name"] for r in rows(db, "SHOW CONSTRAINTS YIELD name RETURN name")} >= {"task_id", "person_id", "project_id"}
    assert rows(db, "MATCH (t:Task) RETURN count(t) AS n")[0]["n"] == 6
    assert rows(db, "MATCH (:Project)-[:HAS_TASK]->(t:Task) RETURN count(t) AS n")[0]["n"] == 6
    assert rows(db, "MATCH (p:Person)-[:OWNS]->(t:Task {id:'B'}) RETURN p.id AS p")[0]["p"] == "2"
    # (B)-[:DEPENDS_ON]->(A): B depends on A
    assert rows(db, "MATCH (b:Task {id:'B'})-[:DEPENDS_ON]->(a:Task) RETURN a.id AS a")[0]["a"] == "A"
    assert rows(db, "MATCH (a:Task {id:'A'})-[:DEPENDS_ON]->() RETURN count(*) AS n")[0]["n"] == 0


def test_sync_is_idempotent_and_tracks_changes(db):
    g = project()
    sync(db, g)
    sync(db, g)
    assert rows(db, "MATCH (t:Task) RETURN count(t) AS n")[0]["n"] == 6
    assert rows(db, "MATCH ()-[r:DEPENDS_ON]->() RETURN count(r) AS n")[0]["n"] == 4
    assert rows(db, "MATCH ()-[r:OWNS]->() RETURN count(r) AS n")[0]["n"] == 6
    g.tasks["E"].deps = []                                       # a removed dependency must disappear
    g.tasks["C"].owner_id = 1                                    # a reassigned task must have exactly one owner
    sync(db, g)
    assert rows(db, "MATCH ()-[r:DEPENDS_ON]->() RETURN count(r) AS n")[0]["n"] == 3
    assert [r["p"] for r in rows(db, "MATCH (p:Person)-[:OWNS]->(:Task {id:'C'}) RETURN p.id AS p")] == ["1"]


def test_downstream_traversal_follows_the_arrow_backwards(db):
    sync(db, project())
    got = {r["task"]: r for r in rows(db, nm.DOWNSTREAM, id="B")}
    assert set(got) == {"C", "D", "E"}
    assert got["D"]["hops"] == 2 and got["D"]["path"] == ["B", "C", "D"]
    assert got["C"]["hops"] == 1 and got["E"]["hops"] == 1
    assert rows(db, nm.DOWNSTREAM, id="D") == []                 # nothing waits for the last task
    # A is finished, but what waits for it still does (B is blocked, not complete): the whole chain is downstream
    assert {r["task"] for r in rows(db, nm.DOWNSTREAM, id="A")} == {"B", "C", "D", "E"}


def test_downstream_skips_finished_work(db):
    g = project()
    g.tasks["C"].status = "Complete"
    sync(db, g)
    assert {r["task"] for r in rows(db, nm.DOWNSTREAM, id="B")} == {"E"}      # C is done; D is only reachable through it


def test_direct_blockers_and_frontier_owner(db):
    sync(db, project())
    assert [(r["task"], r["blocker"]) for r in rows(db, nm.DIRECT_BLOCKERS)] == [("B", "creds")]
    # to move D forward, someone must act on B (waits for nothing unfinished: A is done); B belongs to person 2
    assert rows(db, nm.FRONTIER, id="D") == [{"task": "B", "person": "2"}]
    assert rows(db, nm.FRONTIER, id="F") == [{"task": "F", "person": "1"}]


def test_bottlenecks_rank_by_work_waiting_behind(db):
    sync(db, project())
    top = rows(db, nm.BOTTLENECKS)[0]
    assert top["task"] == "B" and top["waiting"] == 3 and top["person"] == "2"


def test_cycle_detection(db):
    sync(db, project())
    assert rows(db, nm.CYCLES) == []
    cyc = graph(task("X", 1, deps=["Y"]), task("Y", 2, deps=["X"]))
    nm.sync(cyc, None, driver=db)
    found = rows(db, nm.CYCLES)
    assert found and set(found[0]["cycle"]) == {"X", "Y"}


def test_one_person_with_overlapping_forecast_windows_is_a_conflict(db):
    g = graph(task("P", 1, est=60), task("Q", 1, est=60), task("R", 2, est=60))
    nm.sync(g, schedule.plan(g, NOW), driver=db)
    # the scheduler serialises one person's tasks, so their forecast windows do not overlap: no conflict
    assert rows(db, nm.OWNER_CONFLICTS) == []
    # if two windows do overlap (e.g. a stale forecast), the query reports the pair and the person
    with db.session() as s:
        s.run("MATCH (t:Task {id:'Q'}) SET t.forecast_start = (SELECT_START), t.forecast_end = (SELECT_END)".replace(
            "(SELECT_START)", "p.forecast_start").replace("(SELECT_END)", "p.forecast_end").replace(
            "MATCH (t:Task {id:'Q'})", "MATCH (t:Task {id:'Q'}), (p:Task {id:'P'})"))
    assert rows(db, nm.OWNER_CONFLICTS) == [{"person": "1", "a": "P", "b": "Q"}]
    # ... and a different person's task with the same window is not a conflict
    assert all(r["person"] == "1" for r in rows(db, nm.OWNER_CONFLICTS))


def test_priority_scores_are_stored_when_given(db):
    g = project()
    sync(db, g, priorities={"B": 77})
    assert rows(db, "MATCH (t:Task {id:'B'}) RETURN t.priority_score AS p")[0]["p"] == 77
    assert rows(db, "MATCH (t:Task {id:'C'}) RETURN t.priority_score AS p")[0]["p"] is None


def test_a_dependency_on_a_task_listed_later_is_not_dropped(db):
    # B is listed before the A it depends on: the edge must still be created
    g = graph(task("B", 2, deps=["A"], est=30), task("A", 1, est=30))
    nm.sync(g, schedule.plan(g, NOW), driver=db)
    assert rows(db, "MATCH (:Task {id:'B'})-[:DEPENDS_ON]->(a:Task) RETURN a.id AS a") == [{"a": "A"}]


def test_the_network_read_returns_every_node_and_relationship(db):
    g = project()
    sync(db, g)
    n = nm.read_network(db)
    ids = {x["id"] for x in n["nodes"]}
    assert {"Project:main", "Task:A", "Task:B", "Person:1", "Person:2", "Person:3"} <= ids and len(ids) == 1 + 6 + 3
    b = next(x for x in n["nodes"] if x["id"] == "Task:B")
    assert b["status"] == "Blocked" and b["blocker"] == "creds" and b["type"] == "Task"
    assert ("Task:B", "Task:A", "DEPENDS_ON") in {(e["source"], e["target"], e["type"]) for e in n["edges"]}
    assert ("Person:2", "Task:B", "OWNS") in {(e["source"], e["target"], e["type"]) for e in n["edges"]}
    assert ("Project:main", "Task:F", "HAS_TASK") in {(e["source"], e["target"], e["type"]) for e in n["edges"]}
    assert all(e["source"] in ids and e["target"] in ids for e in n["edges"])
    import json
    json.dumps(n)                                                    # everything must be JSON-safe (no Neo4j date objects)


def test_deleted_tasks_and_people_disappear_from_the_graph(db):
    g = project()
    sync(db, g)
    del g.tasks["F"]
    del g.members[3]
    for t in g.tasks.values():
        t.deps = [d for d in t.deps if d != "F"]
        if t.owner_id == 3:
            t.owner_id = 1
    sync(db, g)
    ids = {x["id"] for x in nm.read_network(db)["nodes"]}
    assert "Task:F" not in ids and "Person:3" not in ids and "Task:B" in ids


def test_an_empty_project_still_shows_the_project_and_its_people(db):
    g = graph(task("X", 1, "Complete"))
    g.tasks.clear()
    g.dependents.clear()
    g.members = {1: "Ada", 2: "Bo"}
    nm.sync(g, None, driver=db)
    n = nm.read_network(db)
    assert {x["id"] for x in n["nodes"]} == {"Project:main", "Person:1", "Person:2"} and n["edges"] == []
