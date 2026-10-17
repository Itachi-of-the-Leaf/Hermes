"""Dashboard + control API for the scheduling intelligence."""
import json

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..channels import expand_mentions, to_plain
from ..config import settings
from ..db import get_session
from ..models import AgentAction, Member, Task
from . import engine

router = APIRouter(prefix="/api/intel", tags=["intelligence"])


@router.get("/forecast")
def forecast(s: Session = Depends(get_session)):
    payload = engine.latest(s)
    if not payload:
        return {"status": "NONE", "tasks": [], "risks": [], "analysis": {}}
    f = payload["forecast"]
    tasks = {t.id: t for t in s.exec(select(Task)).all()}
    owners = {m.id: m.name for m in s.exec(select(Member)).all()}
    prio = payload.get("analysis", {}).get("priority", {})
    rows = []
    for tid, tf in f["tasks"].items():
        t = tasks.get(tid)
        rows.append({"id": tid, "title": t.title if t else tid, "owner": owners.get(t.owner_id) if t else None,
                     "status": t.status if t else None, **tf, "priority": prio.get(tid, {}).get("score")})
    rows.sort(key=lambda r: r["end"])
    prev = payload.get("prev")
    return {"status": f["status"], "solver": f["solver"], "confidence": f["confidence"], "version": f["version"],
            "computed_at": f["computed_at"], "project_end": f["project_end"], "total_late_min": f["total_late_min"],
            "previous_project_end": prev["project_end"] if prev else None, "assumptions": f["assumptions"],
            "note": f["note"], "solve_ms": f["solve_ms"], "trigger": payload.get("trigger"), "tasks": rows,
            "risks": payload.get("risks", []), "analysis": payload.get("analysis", {})}


@router.get("/graph")
def graph(s: Session = Depends(get_session)):
    """Nodes and edges for a graph view. An edge points from a prerequisite to the task that waits for it."""
    payload = engine.latest(s)
    ftasks = payload["forecast"]["tasks"] if payload else {}
    owners = {m.id: m.name for m in s.exec(select(Member)).all()}
    nodes, edges = [], []
    for t in s.exec(select(Task)).all():
        tf = ftasks.get(t.id, {})
        nodes.append({"id": t.id, "title": t.title, "owner": owners.get(t.owner_id), "status": t.status,
                      "end": tf.get("end"), "late_min": tf.get("late_min", 0), "critical": tf.get("critical", False),
                      "blocked": t.status == "Blocked" and not t.blocker_resolved})
        edges += [{"source": d.strip(), "target": t.id} for d in t.depends_on.split(",") if d.strip()]
    return {"nodes": nodes, "edges": edges}


def _network_from_database(s: Session) -> dict:
    """The same shape as the Neo4j graph, built from the SQL tables (used when Neo4j is not configured or is down)."""
    owners = {m.id: m for m in s.exec(select(Member)).all()}
    payload = engine.latest(s)
    ftasks = payload["forecast"]["tasks"] if payload else {}
    nodes = [{"id": "Project:main", "type": "Project", "name": "Group project"}]
    nodes += [{"id": f"Person:{m.id}", "type": "Person", "name": m.name, "role": m.role} for m in owners.values()]
    edges = []
    for t in s.exec(select(Task)).all():
        tf = ftasks.get(t.id, {})
        nodes.append({"id": f"Task:{t.id}", "type": "Task", "name": t.title, "status": t.status, "deadline": t.deadline,
                      **({"late_minutes": tf["late_min"]} if tf else {}), **({"blocker": t.blocker_text} if t.blocker_text else {})})
        edges += [{"source": "Project:main", "target": f"Task:{t.id}", "type": "HAS_TASK"},
                  {"source": f"Person:{t.owner_id}", "target": f"Task:{t.id}", "type": "OWNS"}]
        if t.requested_by_id and t.requested_by_id != t.owner_id and t.requested_by_id in owners:
            edges.append({"source": f"Person:{t.requested_by_id}", "target": f"Task:{t.id}", "type": "REQUESTED"})
        edges += [{"source": f"Task:{t.id}", "target": f"Task:{d.strip()}", "type": "DEPENDS_ON"}
                  for d in t.depends_on.split(",") if d.strip()]
    return {"nodes": nodes, "edges": edges}


def browser_url() -> str | None:
    u = settings.neo4j_uri
    if not u:
        return None
    return "http://localhost:7474" if "localhost" in u or "127.0.0.1" in u else "https://console.neo4j.io"


@router.get("/network")
def network(s: Session = Depends(get_session)):
    """Every node and relationship of the project graph: live from Neo4j when it is configured and reachable,
    otherwise built from the database so the page always works. `source` says which one you are looking at."""
    if settings.neo4j_uri:
        try:
            from . import neo4j_mirror
            return {"source": "neo4j", "browser": browser_url(), **neo4j_mirror.read_network()}
        except Exception as e:
            return {"source": "database", "browser": browser_url(), "error": f"Neo4j unavailable: {str(e)[:160]}",
                    **_network_from_database(s)}
    return {"source": "database", "browser": None, **_network_from_database(s)}


@router.get("/actions")
def actions(limit: int = 50, s: Session = Depends(get_session)):
    people = {m.id: m for m in s.exec(select(Member)).all()}
    owners = {i: m.name for i, m in people.items()}
    titles = {t.id: t.title for t in s.exec(select(Task)).all()}
    rows = s.exec(select(AgentAction).order_by(AgentAction.id.desc()).limit(min(limit, 200))).all()
    out = []
    for a in rows:
        reason = json.loads(a.reason or "{}")
        evidence = [e for r in reason.get("risks", []) for e in r.get("evidence", [])]
        out.append({**a.model_dump(), "text": to_plain(expand_mentions(a.text, people, "plain")), "recipient": owners.get(a.recipient_id), "task": titles.get(a.task_id),
                    "evidence": evidence[:6], "severity": reason.get("severity")})
    return out


@router.post("/replan")
async def replan(send: bool = True):
    """Run the whole pipeline now. `send=false` shows what the agent would say without contacting anyone."""
    return await engine.run_once(trigger="MANUAL", send=send)
