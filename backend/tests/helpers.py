"""Builders for graph/scheduler unit tests (no database)."""
from datetime import datetime, timedelta, timezone

from app.intel.graph import ProjectGraph, TNode

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def task(tid, owner=1, status="Pending", deps=(), est=60, deadline_in=None, pct=None, blocker=None,
         blocked_by=None, resolved=False, title=None):
    return TNode(id=tid, title=title or tid, owner_id=owner, owner=f"P{owner}", status=status, deps=list(deps),
                 deadline=NOW + timedelta(minutes=deadline_in) if deadline_in is not None else None,
                 est_minutes=est, progress_pct=pct, blocker_text=blocker, blocked_by_id=blocked_by,
                 blocker_resolved=resolved)


def graph(*tasks):
    members = {t.owner_id: t.owner for t in tasks}
    return ProjectGraph(list(tasks), members)
