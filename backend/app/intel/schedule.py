"""Graph -> CP-SAT scheduling model -> versioned forecast.

Neo4j / SQL do not become a CP-SAT model by themselves. This module is the translation layer:
graph entities become integer variables (minutes after `origin`) and relationships become constraints.

Model (for every unfinished task i, all times in whole minutes from `origin` = now):

    start_i, end_i = start_i + d_i                interval variable, fixed duration d_i
    start_i >= earliest_i                         when the task cannot begin before something happens
    start_b >= end_a                              for every "b DEPENDS_ON a" with a unfinished
    NoOverlap(intervals of one owner)             a person cannot do two tasks at once (capacity 1)
    late_i >= end_i - deadline_i,  late_i >= 0    soft deadlines: the model is always feasible and
                                                  reports *how late*, instead of just failing
    makespan >= end_i

Objective, lexicographic by construction:

    minimise  SCALE * sum_i(w_i * late_i) + makespan,      SCALE = (largest possible makespan) + 1

so no amount of makespan saving can ever buy more lateness: first minimise weighted lateness
(w_i = 1 + number of open tasks waiting behind i, so slipping a bottleneck costs more), then finish
the whole project as early as possible. No hand-tuned coefficients.

Unknown inputs are never silently zero:
    * no duration estimate        -> INTEL_DEFAULT_TASK_MIN, listed in `assumptions`, confidence drops
    * blocked, no known unblock   -> INTEL_UNKNOWN_UNBLOCK_MIN (pessimistic), listed, confidence LOW
    * blocked, unblock promised   -> earliest start = the promised time (flagged "unverified")
    * blocked on a prerequisite   -> the precedence constraint already encodes it

The solver may be missing, time out, or fail; `plan()` then returns a deterministic CPM-style list
schedule (status FALLBACK_CPM) so the rest of the system keeps working.
"""
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from ..config import settings
from .graph import ProjectGraph

STATUSES = ("OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "FALLBACK_CPM", "NO_WORK", "INVALID_GRAPH")


@dataclass
class SchedTask:
    id: str
    owner_id: int
    duration: int
    earliest: int = 0
    deadline: int | None = None            # minutes from origin (may be negative: already past)
    weight: int = 1
    notes: list[str] = field(default_factory=list)
    unknown_unblock: bool = False
    duration_assumed: bool = False


@dataclass
class TaskForecast:
    start: str
    end: str
    duration_min: int
    deadline: str | None = None
    late_min: int = 0
    slack_min: int | None = None
    critical: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class ScheduleResult:
    version: str
    status: str                             # one of STATUSES
    solver: str                             # cp-sat | cpm | none
    origin: str
    computed_at: str
    tasks: dict[str, TaskForecast] = field(default_factory=dict)
    project_end: str | None = None
    total_late_min: int = 0
    confidence: str = "HIGH"                # HIGH | MEDIUM | LOW: how much to trust the numbers
    assumptions: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)   # what the solver saw, for attribution
    solve_ms: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduleResult":
        d = dict(d)
        d["tasks"] = {k: TaskForecast(**v) for k, v in d.get("tasks", {}).items()}
        return cls(**d)

    @property
    def usable(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE", "FALLBACK_CPM")

    def end_of(self, task_id: str) -> datetime | None:
        t = self.tasks.get(task_id)
        return datetime.fromisoformat(t.end) if t else None


# ---------------------------------------------------------------- inputs

def _minutes(delta: timedelta) -> int:
    return int(math.floor(delta.total_seconds() / 60))


def build_inputs(g: ProjectGraph, origin: datetime, commitments: dict[str, datetime] | None = None
                 ) -> list[SchedTask]:
    """Normalise unfinished tasks into solver inputs, recording every assumption made."""
    commitments = commitments or {}
    out = []
    for tid in g.open_ids():
        t = g.tasks[tid]
        notes: list[str] = []
        est = t.est_minutes
        assumed = est is None
        if assumed:
            est = settings.intel_default_task_min
            notes.append(f"no estimate for '{t.title}': assumed {est} min")
        dur = est
        if t.status == "In Progress":
            if t.progress_pct is not None:
                dur = max(1, math.ceil(est * (100 - t.progress_pct) / 100))
            else:
                dur = max(1, math.ceil(est / 2))
                notes.append(f"progress of '{t.title}' unknown: assumed half done")
        earliest, unknown = 0, False
        if t.is_blocked:
            via_prereq = t.blocked_by_id is not None and any(
                g.tasks[d].owner_id == t.blocked_by_id for d in g.open_deps(tid))
            promised = commitments.get(tid)
            if via_prereq:
                pass                                            # the precedence constraint covers it
            elif promised and promised > origin:
                earliest = max(0, math.ceil((promised - origin).total_seconds() / 60))
                notes.append(f"'{t.title}' blocked by '{t.blocker_text}': unblock promised for "
                             f"{promised.astimezone():%H:%M} (unverified)")
            else:
                earliest, unknown = settings.intel_unknown_unblock_min, True
                why = "promised time has passed" if promised else "no resolution time known"
                notes.append(f"'{t.title}' blocked by '{t.blocker_text}': {why}; assumed "
                             f"{earliest} min (pessimistic placeholder)")
        out.append(SchedTask(id=tid, owner_id=t.owner_id, duration=dur, earliest=earliest,
                             deadline=_minutes(t.deadline - origin) if t.deadline else None,
                             weight=1 + len(g.downstream(tid)), notes=notes, unknown_unblock=unknown,
                             duration_assumed=assumed))
    return out


# ---------------------------------------------------------------- solving

def solve_cpsat(tasks: list[SchedTask], deps: dict[str, list[str]], time_limit_s: float) -> dict:
    """Returns {"status", "start": {id: int}, "ms"}; raises ImportError if OR-Tools is missing."""
    from ortools.sat.python import cp_model

    t0 = time.perf_counter()
    m = cp_model.CpModel()
    horizon = sum(t.duration for t in tasks) + max((t.earliest for t in tasks), default=0)
    max_dead = max((abs(t.deadline) for t in tasks if t.deadline is not None), default=0)
    start, end, late, iv = {}, {}, {}, {}
    for t in tasks:
        start[t.id] = m.NewIntVar(t.earliest, horizon, f"s_{t.id}")
        end[t.id] = m.NewIntVar(t.earliest + t.duration, horizon + t.duration, f"e_{t.id}")
        iv[t.id] = m.NewIntervalVar(start[t.id], t.duration, end[t.id], f"i_{t.id}")
        if t.deadline is not None:
            late[t.id] = m.NewIntVar(0, horizon + t.duration + max_dead, f"l_{t.id}")
            m.Add(late[t.id] >= end[t.id] - t.deadline)
    for t in tasks:
        for d in deps.get(t.id, []):
            if d in start:
                m.Add(start[t.id] >= end[d])                       # b DEPENDS_ON a  =>  start_b >= end_a
    by_owner: dict[int, list] = {}
    for t in tasks:
        by_owner.setdefault(t.owner_id, []).append(iv[t.id])
    for ivs in by_owner.values():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)                                    # one person, one task at a time
    makespan = m.NewIntVar(0, horizon + max((t.duration for t in tasks), default=0), "makespan")
    m.AddMaxEquality(makespan, list(end.values()))
    scale = horizon + max((t.duration for t in tasks), default=0) + 1
    weights = {t.id: t.weight for t in tasks}
    m.Minimize(scale * sum(weights[i] * v for i, v in late.items()) + makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1                              # deterministic, plenty for ~50 tasks
    solver.parameters.random_seed = 7
    status = solver.Solve(m)
    name = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE"}.get(status, "UNKNOWN")
    res = {"status": name, "ms": int((time.perf_counter() - t0) * 1000), "start": {}}
    if name in ("OPTIMAL", "FEASIBLE"):
        res["start"] = {i: int(solver.Value(v)) for i, v in start.items()}
    return res


def cpm_list_schedule(tasks: list[SchedTask], deps: dict[str, list[str]], order: list[str]) -> dict[str, int]:
    """Fallback: dependency order, honour earliest start and one-task-at-a-time per person, most
    important (heaviest, earliest-deadline) ready task first. Feasible, not optimal."""
    by_id = {t.id: t for t in tasks}
    done_at: dict[str, int] = {}
    free_at: dict[int, int] = {}
    start: dict[str, int] = {}
    remaining = [i for i in order if i in by_id]
    while remaining:
        ready = [i for i in remaining if all(d in done_at for d in deps.get(i, []) if d in by_id)]
        ready.sort(key=lambda i: (-by_id[i].weight, by_id[i].deadline if by_id[i].deadline is not None else 10 ** 9, i))
        i = ready[0]
        t = by_id[i]
        s = max(t.earliest, free_at.get(t.owner_id, 0), max((done_at[d] for d in deps.get(i, []) if d in by_id), default=0))
        start[i] = s
        done_at[i] = s + t.duration
        free_at[t.owner_id] = done_at[i]
        remaining.remove(i)
    return start


# ---------------------------------------------------------------- orchestration

def plan(g: ProjectGraph, origin: datetime, commitments: dict[str, datetime] | None = None,
         version: str | None = None, time_limit_s: float | None = None) -> ScheduleResult:
    """Never raises. Always returns something the rest of the system can reason about."""
    version = version or g.version()
    base = dict(version=version, origin=origin.isoformat(), computed_at=datetime.now().astimezone().isoformat())
    if g.cycle:
        return ScheduleResult(status="INVALID_GRAPH", solver="none", note="; ".join(g.issues), **base)
    try:
        tasks = build_inputs(g, origin, commitments)
    except Exception as e:                                          # pragma: no cover  (defensive)
        return ScheduleResult(status="UNKNOWN", solver="none", note=f"input error: {e}", **base)
    if not tasks:
        return ScheduleResult(status="NO_WORK", solver="none", note="every task is complete", **base)

    deps = {t.id: [d for d in g.tasks[t.id].deps] for t in tasks}
    order = g.topo_order()
    limit = time_limit_s if time_limit_s is not None else settings.intel_solver_time_s
    status, solver, note, ms, start = "UNKNOWN", "none", "", 0, {}
    try:
        res = solve_cpsat(tasks, deps, limit)
        status, ms, solver = res["status"], res["ms"], "cp-sat"
        start = res["start"]
        if not start:
            note = f"CP-SAT returned {status}; using dependency-order fallback"
    except Exception as e:                                          # missing OR-Tools or solver failure
        note = f"CP-SAT unavailable ({type(e).__name__}: {e}); using dependency-order fallback"
    if not start:
        start = cpm_list_schedule(tasks, deps, order)
        status, solver = "FALLBACK_CPM", "cpm"

    by_id = {t.id: t for t in tasks}
    forecasts: dict[str, TaskForecast] = {}
    cpm = g.cpm({t.id: t.duration for t in tasks})
    total_late = 0
    for t in tasks:
        s_min, e_min = start[t.id], start[t.id] + t.duration
        late_min = max(0, e_min - t.deadline) if t.deadline is not None else 0
        total_late += late_min
        forecasts[t.id] = TaskForecast(
            start=(origin + timedelta(minutes=s_min)).isoformat(), end=(origin + timedelta(minutes=e_min)).isoformat(),
            duration_min=t.duration, deadline=g.tasks[t.id].deadline.isoformat() if g.tasks[t.id].deadline else None,
            late_min=late_min, slack_min=(t.deadline - e_min) if t.deadline is not None else None,
            critical=cpm.get(t.id, {}).get("critical", False), notes=t.notes)
    assumptions = [n for t in tasks for n in t.notes]
    assumed_share = sum(t.duration_assumed for t in tasks) / len(tasks)
    if any(t.unknown_unblock for t in tasks):
        confidence = "LOW"
    elif assumed_share > 0.5:
        confidence = "LOW"
    elif assumptions:
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"
    end_min = max(start[t.id] + t.duration for t in tasks)
    return ScheduleResult(
        status=status, solver=solver, tasks=forecasts, project_end=(origin + timedelta(minutes=end_min)).isoformat(),
        total_late_min=total_late, confidence=confidence, assumptions=assumptions, solve_ms=ms, note=note,
        inputs={t.id: {"status": g.tasks[t.id].status, "duration": t.duration, "earliest": t.earliest,
                       "deps": sorted(deps[t.id]), "owner_id": t.owner_id, "unknown_unblock": t.unknown_unblock,
                       "weight": by_id[t.id].weight} for t in tasks}, **base)
