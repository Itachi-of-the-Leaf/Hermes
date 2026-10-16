"""Project graph: the dependency structure, built in memory from the authoritative SQL tables.

Direction convention (used everywhere, including the Cypher in neo4j_mirror.py):

    (B)-[:DEPENDS_ON]->(A)      "B depends on A": A must finish before B can start.

    deps[B]       = [A]         prerequisites of B (follow the arrow)
    dependents[A] = [B]         tasks that wait for A (follow the arrow backwards)

"Downstream of A" therefore means walking `dependents`, never `deps`.

Vocabulary (kept distinct on purpose):
    blocked   the owner reported a blocker that is not resolved (status Blocked)
    waiting   a prerequisite is simply not finished yet; ordinary sequencing, not a problem
    affected  reachable downstream of a blocked task: *potentially* delayed, not necessarily stuck
    late      the scheduler forecasts it will miss its deadline
"""
import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from ..models import Member, Task


class CycleError(ValueError):
    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("dependency cycle: " + " -> ".join(cycle))


def parse_deadline(raw: str | None) -> datetime | None:
    """'2026-10-17' means the end of that day; naive values are local time. Returns aware or None."""
    if not raw:
        return None
    try:
        if len(raw.strip()) <= 10:
            dt = datetime.fromisoformat(raw.strip()) + timedelta(hours=23, minutes=59)
        else:
            dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo is None else dt


@dataclass
class TNode:
    id: str
    title: str
    owner_id: int
    owner: str
    status: str
    deps: list[str] = field(default_factory=list)
    deadline: datetime | None = None
    est_minutes: int | None = None
    progress_pct: int | None = None
    blocker_text: str | None = None
    blocked_by_id: int | None = None
    blocker_resolved: bool = False

    @property
    def is_open(self) -> bool:
        return self.status != "Complete"

    @property
    def is_blocked(self) -> bool:
        return self.status == "Blocked" and not self.blocker_resolved


class ProjectGraph:
    def __init__(self, tasks: list[TNode], members: dict[int, str] | None = None):
        self.tasks: dict[str, TNode] = {t.id: t for t in tasks}
        self.members = members or {}
        self.issues: list[str] = []
        for t in self.tasks.values():                        # drop references we cannot resolve, but say so
            clean = []
            for d in t.deps:
                if d == t.id:
                    self.issues.append(f"{t.id} depends on itself")
                elif d not in self.tasks:
                    self.issues.append(f"{t.id} depends on unknown task {d}")
                else:
                    clean.append(d)
            t.deps = clean
        self.dependents: dict[str, list[str]] = {i: [] for i in self.tasks}
        for t in self.tasks.values():
            for d in t.deps:
                self.dependents[d].append(t.id)
        self.cycle: list[str] | None = self._find_cycle()
        if self.cycle:
            self.issues.append("dependency cycle: " + " -> ".join(self.cycle))

    # ------------------------------------------------------------ construction
    @classmethod
    def from_db(cls, s: Session) -> "ProjectGraph":
        members = {m.id: m.name for m in s.exec(select(Member)).all()}
        nodes = [TNode(id=t.id, title=t.title, owner_id=t.owner_id, owner=members.get(t.owner_id, "?"),
                       status=t.status, deps=[d.strip() for d in t.depends_on.split(",") if d.strip()],
                       deadline=parse_deadline(t.deadline), est_minutes=t.est_minutes, progress_pct=t.progress_pct,
                       blocker_text=t.blocker_text, blocked_by_id=t.blocked_by_id,
                       blocker_resolved=t.blocker_resolved)
                 for t in s.exec(select(Task)).all()]
        return cls(nodes, members)

    # ------------------------------------------------------------ structure
    def _find_cycle(self) -> list[str] | None:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {i: WHITE for i in self.tasks}
        for root in sorted(self.tasks):
            if color[root] != WHITE:
                continue
            stack = [(root, iter(self.tasks[root].deps))]
            path = [root]
            color[root] = GREY
            while stack:
                node, it = stack[-1]
                for nxt in it:
                    if color[nxt] == GREY:
                        return path[path.index(nxt):] + [nxt]
                    if color[nxt] == WHITE:
                        color[nxt] = GREY
                        path.append(nxt)
                        stack.append((nxt, iter(self.tasks[nxt].deps)))
                        break
                else:
                    color[node] = BLACK
                    path.pop()
                    stack.pop()
        return None

    def topo_order(self) -> list[str]:
        """Prerequisites before dependents (Kahn). Raises CycleError on a cycle."""
        if self.cycle:
            raise CycleError(self.cycle)
        indeg = {i: len(t.deps) for i, t in self.tasks.items()}
        queue = deque(sorted(i for i, n in indeg.items() if n == 0))
        out = []
        while queue:
            n = queue.popleft()
            out.append(n)
            for m in sorted(self.dependents[n]):
                indeg[m] -= 1
                if indeg[m] == 0:
                    queue.append(m)
        return out

    def open_ids(self) -> list[str]:
        return [i for i in self.topo_order() if self.tasks[i].is_open]

    def open_deps(self, task_id: str) -> list[str]:
        return [d for d in self.tasks[task_id].deps if self.tasks[d].is_open]

    # ------------------------------------------------------------ traversals
    def downstream(self, task_id: str, open_only: bool = True) -> dict[str, list[str]]:
        """Everything that transitively waits for `task_id`, each with one shortest dependency path
        [task_id, ..., node]. BFS over the reversed DEPENDS_ON direction."""
        seen: dict[str, list[str]] = {}
        queue = deque([(task_id, [task_id])])
        while queue:
            n, path = queue.popleft()
            for m in sorted(self.dependents[n]):
                if m in seen or m == task_id:
                    continue
                if open_only and not self.tasks[m].is_open:
                    continue
                seen[m] = path + [m]
                queue.append((m, path + [m]))
        return seen

    def upstream_open(self, task_id: str) -> set[str]:
        """Unfinished prerequisites, transitively."""
        seen: set[str] = set()
        stack = list(self.open_deps(task_id))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(self.open_deps(n))
        return seen

    def frontier(self, task_id: str) -> list[str]:
        """The tasks someone can act on *now* to move `task_id` forward: unfinished prerequisites that
        themselves wait for nothing (or the task itself if it has none)."""
        up = self.upstream_open(task_id)
        if not up:
            return [task_id]
        return sorted(n for n in up if not self.open_deps(n))

    def blocked_tasks(self) -> list[TNode]:
        return [t for t in self.tasks.values() if t.is_open and t.is_blocked]

    def affected_by(self, task_id: str) -> dict[str, list[str]]:
        """Potentially affected tasks (not necessarily stuck) with their dependency paths."""
        return self.downstream(task_id)

    def bottlenecks(self) -> list[tuple[str, int]]:
        """Open tasks ranked by how much open work waits behind them."""
        rows = [(i, len(self.downstream(i))) for i in self.tasks if self.tasks[i].is_open]
        return sorted(rows, key=lambda r: (-r[1], r[0]))

    def path_between(self, src: str, dst: str) -> list[str] | None:
        """Dependency path from prerequisite `src` down to dependent `dst` (inclusive), if any."""
        if src == dst:
            return [src]
        return self.downstream(src, open_only=False).get(dst)

    # ------------------------------------------------------------ CPM (no resource limits)
    def cpm(self, durations: dict[str, int]) -> dict[str, dict]:
        """Classic critical path over open tasks: earliest/latest start+finish and slack, assuming
        unlimited people. Resource-constrained scheduling is the solver's job; this is the baseline."""
        order = [i for i in self.topo_order() if i in durations]
        es, ef = {}, {}
        for i in order:
            es[i] = max((ef[d] for d in self.tasks[i].deps if d in ef), default=0)
            ef[i] = es[i] + durations[i]
        end = max(ef.values(), default=0)
        ls, lf = {}, {}
        for i in reversed(order):
            kids = [k for k in self.dependents[i] if k in ls]
            lf[i] = min((ls[k] for k in kids), default=end)
            ls[i] = lf[i] - durations[i]
        return {i: {"es": es[i], "ef": ef[i], "ls": ls[i], "lf": lf[i], "slack": ls[i] - es[i],
                    "critical": ls[i] - es[i] == 0} for i in order}

    def owner_conflicts(self, cpm: dict[str, dict]) -> list[dict]:
        """People whose open tasks overlap in time when everyone works in parallel: they cannot do both."""
        out = []
        ids = sorted(cpm)
        for a_i, a in enumerate(ids):
            for b in ids[a_i + 1:]:
                ta, tb = self.tasks[a], self.tasks[b]
                if ta.owner_id != tb.owner_id:
                    continue
                if cpm[a]["es"] < cpm[b]["ef"] and cpm[b]["es"] < cpm[a]["ef"]:
                    out.append({"owner": ta.owner, "owner_id": ta.owner_id, "tasks": [a, b]})
        return out

    # ------------------------------------------------------------ versioning
    def version(self, extra: object = None) -> str:
        """Hash of every input that changes a schedule. A result computed for another version is stale."""
        rows = [[t.id, t.status, t.owner_id, sorted(t.deps), t.deadline.isoformat() if t.deadline else None,
                 t.est_minutes, t.progress_pct, t.blocker_text, t.blocked_by_id, t.blocker_resolved]
                for t in sorted(self.tasks.values(), key=lambda x: x.id)]
        blob = json.dumps([rows, extra], sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- priority

def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def priority_scores(g: ProjectGraph, slack_min: dict[str, int | None], w: tuple[float, float, float, float],
                    urgency_window_min: int = 480) -> dict[str, dict]:
    """Task priority = how much the *task* matters right now. Computed on demand from the graph and the
    latest forecast (a derived value; storing it would just create something to keep in sync).
    This is deliberately separate from notification priority, which the planner governs with cooldowns:
    an important task must not trigger repeated messages.

        0.35 deadline urgency + 0.30 dependency impact + 0.20 blocker severity + 0.15 resource risk
    (weights provisional, set in config; each component is normalised to 0..1)."""
    open_ids = [i for i, t in g.tasks.items() if t.is_open]
    denom = max(1, len(open_ids) - 1)
    out = {}
    for i in open_ids:
        t = g.tasks[i]
        slack = slack_min.get(i)
        urgency = 0.0 if slack is None else (1.0 if slack <= 0 else clamp(1 - slack / urgency_window_min))
        impact = clamp(len(g.downstream(i)) / denom)
        blocker = 1.0 if t.is_blocked else (0.5 if any(g.tasks[d].is_blocked for d in g.open_deps(i)) else 0.0)
        same_owner_open = sum(1 for j in open_ids if g.tasks[j].owner_id == t.owner_id)
        resource = clamp((same_owner_open - 1) / 3)
        score = w[0] * urgency + w[1] * impact + w[2] * blocker + w[3] * resource
        out[i] = {"score": round(100 * score), "urgency": round(urgency, 2), "impact": round(impact, 2),
                  "blocker": blocker, "resource": round(resource, 2)}
    return out
