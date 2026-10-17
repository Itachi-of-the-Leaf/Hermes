"""Optional Neo4j projection of the project graph (read model; SQL stays the source of truth).

Why a mirror and not the primary store: status changes, Undo and the audit trail already live in SQL with
transactions. Writing the same facts to a second authoritative database would create a consistency problem
(two stores that can disagree after a crash or an Undo) for no algorithmic gain at this project size, where
traversals over a few dozen tasks take microseconds in memory. Neo4j earns its place here for what it is
good at: ad-hoc Cypher exploration of the dependency network and a live graph visualisation.

`sync()` is idempotent (MERGE) and safe to call after every plan. Enable it with NEO4J_URI / NEO4J_USER /
NEO4J_PASSWORD. The driver is imported lazily, so nothing else needs it installed.

Convention (same as intel/graph.py):   (B)-[:DEPENDS_ON]->(A)   means B depends on A.
Downstream of A = follow DEPENDS_ON *backwards*:  (A)<-[:DEPENDS_ON*]-(x)
"""
import logging

from ..config import settings
from .graph import ProjectGraph
from .schedule import ScheduleResult

log = logging.getLogger("intel.neo4j")

# ---------------------------------------------------------------- schema
SCHEMA = [
    "CREATE CONSTRAINT project_id IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT task_id IF NOT EXISTS FOR (t:Task) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE INDEX task_status IF NOT EXISTS FOR (t:Task) ON (t.status)",
    "CREATE INDEX task_deadline IF NOT EXISTS FOR (t:Task) ON (t.deadline)",
]

# ---------------------------------------------------------------- writes
UPSERT_PROJECT = "MERGE (p:Project {id: $id}) SET p.name = $name, p.graph_version = $version"

UPSERT_PERSON = "MERGE (p:Person {id: $id}) SET p.name = $name"

UPSERT_TASK = """
MATCH (pr:Project {id: $project})
MERGE (t:Task {id: $id})
SET t.name = $name, t.status = $status, t.owner_id = $owner_id, t.deadline = $deadline,
    t.estimated_remaining_minutes = $duration, t.forecast_start = $forecast_start, t.forecast_end = $forecast_end,
    t.late_minutes = $late,
    t.blocker = $blocker, t.priority_score = $priority, t.last_updated = datetime()
MERGE (pr)-[:HAS_TASK]->(t)
WITH t
MATCH (p:Person {id: $owner_id})
OPTIONAL MATCH (:Person)-[old:OWNS]->(t) DELETE old
MERGE (p)-[:OWNS]->(t)
"""

# who asked the owner to do it; only drawn when that is someone else ("I'll do X" needs no second line)
SET_REQUESTER = """
MATCH (t:Task {id: $id})
OPTIONAL MATCH (:Person)-[old:REQUESTED]->(t) DELETE old
WITH t
MATCH (p:Person {id: $requester}) WHERE $requester <> $owner_id
MERGE (p)-[:REQUESTED]->(t)
"""

# dependencies are replaced wholesale per task so a removed dependency disappears from the graph
CLEAR_DEPS = "MATCH (t:Task {id: $id})-[r:DEPENDS_ON]->() DELETE r"
ADD_DEP = "MATCH (b:Task {id: $task}), (a:Task {id: $dep}) MERGE (b)-[:DEPENDS_ON]->(a)"

# ---------------------------------------------------------------- reads (the questions the agent asks)
DIRECT_BLOCKERS = """
MATCH (t:Task {status: 'Blocked'}) RETURN t.id AS task, t.blocker AS blocker, t.owner_id AS owner
"""

# everything that waits (transitively) for a task: reverse DEPENDS_ON
DOWNSTREAM = """
MATCH p = (a:Task {id: $id})<-[:DEPENDS_ON*1..]-(x:Task)
WHERE all(n IN nodes(p)[1..] WHERE n.status <> 'Complete')   // a finished task in between means x no longer waits for a
RETURN x.id AS task, x.name AS name, length(p) AS hops, [n IN nodes(p) | n.id] AS path
ORDER BY hops
"""

# who must act so that a task can move: unfinished prerequisites that wait for nothing
FRONTIER = """
MATCH (t:Task {id: $id})-[:DEPENDS_ON*0..]->(a:Task)
WHERE a.status <> 'Complete' AND NOT EXISTS { (a)-[:DEPENDS_ON]->(b:Task) WHERE b.status <> 'Complete' }
MATCH (p:Person)-[:OWNS]->(a)
RETURN DISTINCT a.id AS task, p.id AS person
"""

# a dependency cycle exists iff a task can reach itself
CYCLES = "MATCH p = (t:Task)-[:DEPENDS_ON*1..]->(t) RETURN [n IN nodes(p) | n.id] AS cycle LIMIT 1"

# one person scheduled on tasks whose forecast windows overlap
OWNER_CONFLICTS = """
MATCH (p:Person)-[:OWNS]->(a:Task), (p)-[:OWNS]->(b:Task)
WHERE a.id < b.id AND a.status <> 'Complete' AND b.status <> 'Complete'
  AND datetime(a.forecast_start) < datetime(b.forecast_end) AND datetime(b.forecast_start) < datetime(a.forecast_end)
RETURN p.id AS person, a.id AS a, b.id AS b
"""

# which unfinished task holds up the most work, and who owns it
BOTTLENECKS = """
MATCH (t:Task) WHERE t.status <> 'Complete'
OPTIONAL MATCH p = (t)<-[:DEPENDS_ON*1..]-(x:Task) WHERE all(n IN nodes(p)[1..] WHERE n.status <> 'Complete')
WITH t, count(DISTINCT x) AS waiting
MATCH (p:Person)-[:OWNS]->(t)
RETURN t.id AS task, p.id AS person, waiting ORDER BY waiting DESC
"""


PRUNE_TASKS = "MATCH (t:Task) WHERE NOT t.id IN $ids DETACH DELETE t"
PRUNE_PEOPLE = "MATCH (p:Person) WHERE NOT p.id IN $ids DETACH DELETE p"

# the whole graph, for the dashboard: every node and every relationship
NETWORK_NODES = "MATCH (n) RETURN labels(n)[0] AS label, properties(n) AS props"
NETWORK_EDGES = "MATCH (a)-[r]->(b) RETURN labels(a)[0] AS al, a.id AS aid, type(r) AS rel, labels(b)[0] AS bl, b.id AS bid"
_SHOW = ("name", "status", "blocker", "late_minutes", "priority_score", "forecast_end", "deadline", "role", "owner_id")


def _driver():
    from neo4j import GraphDatabase  # lazy: only needed when the mirror is enabled
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def _session(driver):
    return driver.session(database=settings.neo4j_database) if settings.neo4j_database else driver.session()


def read_network(driver=None) -> dict:
    """Every node and relationship currently in Neo4j, as {"nodes": [...], "edges": [...]} (ids are "Label:id")."""
    own = driver is None
    driver = driver or _driver()
    try:
        with _session(driver) as sess:
            nodes = []
            for r in sess.run(NETWORK_NODES):
                p = dict(r["props"])
                nodes.append({"id": f"{r['label']}:{p.get('id')}", "type": r["label"],
                              **{k: p[k] for k in _SHOW if p.get(k) is not None and not hasattr(p[k], "iso_format")}})
            edges = [{"source": f"{r['al']}:{r['aid']}", "target": f"{r['bl']}:{r['bid']}", "type": r["rel"]}
                     for r in sess.run(NETWORK_EDGES)]
        return {"nodes": nodes, "edges": edges}
    finally:
        if own:
            driver.close()


def sync(g: ProjectGraph, cur: ScheduleResult | None, project_id: str = "main", driver=None,
         priorities: dict[str, int] | None = None) -> int:
    """Project the current graph (+ forecast) into Neo4j. Returns the number of tasks written.
    `priorities`: optional {task_id: 0-100} from the priority score."""
    priorities = priorities or {}
    if driver is None:
        from neo4j import GraphDatabase  # lazy: only needed when the mirror is enabled
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    with driver.session() as sess:
        for stmt in SCHEMA:
            sess.run(stmt)
        sess.run(UPSERT_PROJECT, id=project_id, name="Group project", version=cur.version if cur else "")
        for pid, name in g.members.items():
            sess.run(UPSERT_PERSON, id=str(pid), name=name)
        for t in g.tasks.values():                       # pass 1: every task exists before any edge points at it
            tf = cur.tasks.get(t.id) if cur and cur.usable else None
            sess.run(UPSERT_TASK, project=project_id, id=t.id, name=t.title, status=t.status, owner_id=str(t.owner_id),
                     deadline=t.deadline.isoformat() if t.deadline else None, duration=tf.duration_min if tf else None,
                     forecast_start=tf.start if tf else None, forecast_end=tf.end if tf else None,
                     late=tf.late_min if tf else None, blocker=t.blocker_text, priority=priorities.get(t.id))
            sess.run(SET_REQUESTER, id=t.id, requester=str(t.requested_by_id), owner_id=str(t.owner_id))
            sess.run(CLEAR_DEPS, id=t.id)
        for t in g.tasks.values():                       # pass 2: dependencies may point at tasks listed later
            for d in t.deps:
                sess.run(ADD_DEP, task=t.id, dep=d)
        sess.run(PRUNE_TASKS, ids=list(g.tasks))          # deleted tasks and people disappear from the graph too
        sess.run(PRUNE_PEOPLE, ids=[str(i) for i in g.members])
    return len(g.tasks)
