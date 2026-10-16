"""Message writer. Facts come from the structured action; the LLM may only reword.

`render()` builds the message deterministically from verified state (recipient, task, blocker, forecast).
`polish()` optionally asks the LLM to make it sound natural, then *validates* the rewrite: it must keep every
mention token, every bold task name and may not introduce a single number that was not in the original.
Anything else is discarded and the template is sent, so the model cannot invent recipients, times or reasons.

Mentions are written as {M<member id>} and expanded to a real Telegram mention at send time.
"""
import logging
import re
from datetime import datetime
from html import escape

from ..pipeline import llm
from .graph import ProjectGraph
from .planner import ActionProposal
from .schedule import ScheduleResult

log = logging.getLogger("intel.compose")


def clock(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return "?"
    dt = datetime.fromisoformat(iso).astimezone()
    ref = (now or datetime.now().astimezone()).astimezone()
    return f"{dt:%H:%M}" if dt.date() == ref.date() else f"{dt:%a %H:%M}"


def human(minutes: int) -> str:
    h, m = divmod(max(0, int(minutes)), 60)
    return f"{h}h {m:02d}m" if h and m else f"{h}h" if h else f"{m} min"


def _impact(p: ActionProposal, g: ProjectGraph, cur: ScheduleResult | None, now: datetime | None) -> str:
    r = p.risks[0]
    lines = []
    others = [a for a in r.affected if a["task_id"] != p.task_id][:3]
    if others:
        lines.append("This holds up " + ", ".join(f"<b>{escape(a['title'])}</b> ({escape(a['owner'])})" for a in others) + ".")
    dr = next((x for x in p.risks if x.risk_type == "DEADLINE_RISK"), None)
    if dr and cur and cur.usable:
        late = [a for a in dr.affected if a["late_min"] > 0]
        if late:
            w = max(late, key=lambda a: a["late_min"])
            hedge = "" if cur.confidence == "HIGH" else " (an estimate; some durations or unblock times are assumed)"
            lines.append(f"Forecast: <b>{escape(w['title'])}</b> would finish {human(w['late_min'])} after its "
                         f"{clock(dr.deadline, now)} deadline{hedge}.")
    return " ".join(lines)


def render(p: ActionProposal, g: ProjectGraph, cur: ScheduleResult | None, now: datetime | None = None) -> str:
    r = p.risks[0]
    task = g.tasks[p.task_id]
    to = "{M%d}" % p.recipient_id
    t = f"<b>{escape(task.title)}</b>"
    owner = f"<b>{escape(task.owner)}</b>"
    blocker = f"<i>{escape(task.blocker_text)}</i>" if task.blocker_text else "something outstanding"
    impact = _impact(p, g, cur, now)
    if p.type == "REQUEST_ETA":
        return (f"{to} {owner} is blocked on {t}: {blocker}. {impact}\n"
                f"Can you sort it out, or tell me roughly when it will be ready?").replace("  ", " ")
    if p.type == "REMIND_OWNER":
        if r.risk_type == "COMMITMENT_OVERDUE":
            return (f"{to} you said {blocker} for {t} would be ready around {clock(r.promised_at, now)}. "
                    f"It is not confirmed yet and {owner} is still waiting. {impact}\n"
                    f"Is it done, or what is a new ETA?").replace("  ", " ")
        return (f"{to} {t} is holding up the schedule and has not started. {impact}\n"
                f"Can you pick it up, or tell me what is in the way?").replace("  ", " ")
    if p.type == "REQUEST_STATUS":
        return f"{to} quick status on {t}? {impact}\nA one-line update is enough.".replace("  ", " ")
    if p.type == "REQUEST_CLARIFICATION":
        if r.risk_type == "GRAPH_INVALID":
            return (f"{to} the task dependencies form a loop ({escape(' -> '.join(g.cycle or []))}), so I cannot plan "
                    f"the schedule. Please fix the dependencies so one of these tasks no longer waits on the other.")
        return (f"{to} {t} is blocked on {blocker}, but I do not know who can unblock it. "
                f"Who should I ask?")
    if p.type == "ESCALATE_BLOCKER":
        who = ", ".join(sorted({escape(g.members.get(x.responsible_member_id, "the owner")) for x in p.risks
                                if x.responsible_member_id})) or "the owner"
        return (f"{to} escalating: {t} is still stuck ({blocker}) and I have not had a confirmed answer from {who}. "
                f"{impact}\nCan you step in?").replace("  ", " ")
    return f"{to} update on {t}."


_TOKEN = re.compile(r"\{M\d+\}")
_BOLD = re.compile(r"<b>.*?</b>", re.S)
_NUM = re.compile(r"\d+")


def valid_rewrite(original: str, rewrite: str) -> bool:
    if not rewrite or len(rewrite) > 1.5 * len(original) + 40:
        return False
    if sorted(_TOKEN.findall(rewrite)) != sorted(_TOKEN.findall(original)):
        return False
    if not all(b in rewrite for b in _BOLD.findall(original)):
        return False
    if not set(_NUM.findall(rewrite)) <= set(_NUM.findall(original)):
        return False                                       # no invented times, counts or ids
    return not re.search(r"<(?!/?[bi]>)", rewrite)           # only <b> and <i> are allowed


async def polish(text: str) -> str:
    if not llm.settings.llm_enabled:
        return text
    sys = ("Rewrite a short project-coordination message so it sounds friendly and natural. Keep every fact. "
           "Keep every {M<number>} token and every <b>...</b> segment exactly as written. Do not add numbers, "
           "times, names or promises. Only <b> and <i> tags. Reply as JSON: {\"message\": \"...\"}")
    try:
        out = await llm.complete_json(sys, text, max_tokens=400)
        cand = (out or {}).get("message", "")
        if valid_rewrite(text, cand):
            return cand
        log.info("LLM rewrite rejected; sending the template")
    except Exception as e:                                   # never block a message on the model
        log.warning("polish failed: %s", e)
    return text
