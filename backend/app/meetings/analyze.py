"""Meeting intelligence.

Two-stage design (cheap model on everything, expensive model on very little):
  1. Triage every line with Jev: which task is it about, and is it a status update, a
     blocker, a commitment or a decision? One request per line, many in parallel.
  2. Only flagged lines go on: status lines run through the normal chat pipeline
     (interpret + gate) and become lead reviews; the LLM writes the summary from the
     flagged lines and the board. Without Jev or an LLM, rules do both stages.
Then the summary is posted to Slack and action items are DM'd to their owners.
"""
import asyncio
import json
import logging
import re
from datetime import timedelta
from html import escape

from sqlmodel import Session, select

from .. import bus, channels, jobs, service
from ..config import settings
from ..db import engine
from ..models import Meeting, Member, Message, Task, Utterance, now
from ..pipeline import jev, llm
from ..pipeline.decide import gate, interpret
from ..pipeline.rules import BLOCK, DONE, PROGRESS
from ..telemetry import meeting_utterances, span
from ..textutil import looks_like_question  # noqa: F401  (also imported from here by tests)
from .providers import Line

log = logging.getLogger("meetings")

FLAGS = ("update", "blocker", "commitment", "decision")
COMMIT = re.compile(r"\b(i'?ll|i will|let me|i can take|i'?m going to|will do|i'?ll handle|by (tonight|tomorrow|friday|monday))\b", re.I)
DECIDE = re.compile(r"\b(let'?s|we('ll| will| should)|decided|agreed?|go with|final(ly)?|we're going)\b", re.I)


def _questions(tasks: list[Task]) -> dict:
    task_criteria = {t.id: f"{t.title}" + (f" (also called: {t.aliases.replace('|', ', ')})" if t.aliases else "")
                     for t in tasks}
    task_criteria["none"] = "The line is not about any of these tasks."
    yn = lambda instr, yes, no: {"type": "noul", "instructions": instr, "criteria": {"true": yes, "false": no}}
    return {
        "task": {"type": "choice", "instructions": "Which project task is this meeting line about?",
                 "criteria": task_criteria},
        "update": yn("Does the speaker report the status or progress of a task?",
                     "They say something is done, under way, partly done or not started.",
                     "No status is reported."),
        "blocker": yn("Does the line say some work is blocked or waiting on someone or something?",
                      "Work cannot continue until a person, access, credentials or another task is ready.",
                      "Nothing is reported as blocking work."),
        "commitment": yn("Does someone commit to doing something?",
                         "A person says they will do a specific thing, often with a time.", "No one commits to an action."),
        "decision": yn("Does the line state a decision the group made?",
                       "The group settles on an option, plan, scope or date.", "No decision is stated."),
    }


async def triage_line(u: Utterance, prev: list[str], tasks: list[Task], speaker_tasks: list[str],
                      title: str) -> dict:
    """Return {"task": id|None, "update": p, "blocker": p, "commitment": p, "decision": p, "engine": ...}."""
    if settings.jev_enabled:
        state = {"meeting": title, "speaker": u.speaker, "speaker_owns": speaker_tasks,
                 "previous_lines": prev[-2:], "line": u.text}
        try:
            a = await jev.decide(state, _questions(tasks))
            task = a["task"]["choice"]
            flags = {k: round(float(a[k]["noul"]), 2) for k in FLAGS}
            task = None if task == "none" else task
            by_id = {t.id: t for t in tasks}
            if task and by_id.get(task) and by_id[task].title not in speaker_tasks:
                # People report on their own work. If the line clearly matches a task the speaker owns, that is the one
                # ("the payment API is connected but I'm waiting for the credentials" is about the speaker's payment task).
                own = service.match_task(None, u.text, [t for t in tasks if t.title in speaker_tasks])
                if own:
                    task = own.id
            return _guard(u.text, {"task": task, "task_conf": round(float(a["task"].get("confidence", 0)), 2), **flags, "engine": "jev"})
        except Exception:
            pass                                        # fall through to rules
    t = service.match_task(None, u.text, tasks)
    return {"task": t.id if t else None, "task_conf": 0.7 if t else 0.0,
            "update": 0.8 if (DONE.search(u.text) or PROGRESS.search(u.text)) else 0.1,
            "blocker": 0.85 if BLOCK.search(u.text) else 0.05,
            "commitment": 0.8 if COMMIT.search(u.text) else 0.05,
            "decision": 0.75 if DECIDE.search(u.text) else 0.05, "engine": "rules"}




def _guard(text: str, f: dict) -> dict:
    """Deterministic sanity checks on top of the model's probabilities. A question is not a commitment or a
    decision ("Did anyone catch the cricket score?" is not someone promising something)."""
    if looks_like_question(text):
        f = {**f, "commitment": min(f.get("commitment", 0), 0.2), "decision": min(f.get("decision", 0), 0.2)}
    return f


def _mostly_a_promise(f: dict) -> bool:
    """"I'll record it on Saturday" announces future work; it is not a status report on the task."""
    c, u, b = f.get("commitment", 0), f.get("update", 0), f.get("blocker", 0)
    # a clear promise (>= 0.9) tolerates a borderline blocker score: Jev hovers around 0.5 on "I'll send the credentials"
    return c >= 0.5 and c > u and (b < 0.5 or (c >= 0.9 and b < 0.7))


def _is_flagged(f: dict) -> bool:
    return any(f.get(k, 0) >= 0.5 for k in FLAGS) or (bool(f.get("task")) and f.get("update", 0) >= 0.4)


def _speaker_member(members: list[Member], speaker: str, email: str | None = None) -> Member | None:
    if email:
        for m in members:
            if m.email and m.email == email.lower():
                return m
    first = speaker.strip().split()[0].lower() if speaker.strip() else ""
    return next((m for m in members if m.name.lower() == first or m.name.lower() == speaker.lower()), None)


# ---------------------------------------------------------------- ingest

def add_lines(s: Session, meeting: Meeting, lines: list[Line]) -> list[Utterance]:
    members = s.exec(select(Member)).all()
    out = []
    for ln in lines:
        if not ln.text.strip():
            continue
        m = _speaker_member(members, ln.speaker, ln.email)
        u = Utterance(meeting_id=meeting.id, speaker=ln.speaker, member_id=m.id if m else None, text=ln.text,
                      start_s=ln.start_s, end_s=ln.end_s)
        s.add(u)
        out.append(u)
    s.commit()
    for u in out:
        s.refresh(u)
    bus.publish("meeting", meeting_id=meeting.id)
    return out


async def _triage_pending(s: Session, meeting: Meeting) -> None:
    tasks = s.exec(select(Task)).all()
    members = {m.id: m for m in s.exec(select(Member)).all()}
    utts = s.exec(select(Utterance).where(Utterance.meeting_id == meeting.id).order_by(Utterance.start_s)).all()
    pending = [(i, u) for i, u in enumerate(utts) if not u.triaged]
    sem = asyncio.Semaphore(settings.jev_concurrency)

    async def one(i: int, u: Utterance):
        async with sem:
            owns = [t.title for t in tasks if u.member_id and t.owner_id == u.member_id]
            return u, await triage_line(u, [x.text for x in utts[max(0, i - 2):i]], tasks, owns, meeting.title)

    for u, f in await asyncio.gather(*(one(i, u) for i, u in pending)):
        u.flags, u.flagged, u.triaged, u.task_id = json.dumps(f), _is_flagged(f), True, f.get("task")
        s.add(u)
        meeting_utterances.add(1, {"flagged": str(u.flagged).lower(), "engine": f["engine"]})
    s.commit()
    _ = members


@jobs.handler("meeting.live_triage")
async def live_triage(meeting_id: int) -> None:
    """Called as transcript.data webhooks arrive, so insights show up during the call."""
    with Session(engine) as s:
        m = s.get(Meeting, meeting_id)
        if m:
            await _triage_pending(s, m)
            bus.publish("meeting", meeting_id=meeting_id)


# ---------------------------------------------------------------- analysis

SUMMARY_SYSTEM = """You write the summary of a student project meeting from flagged transcript lines.
Return ONLY JSON with keys:
  headline: one sentence, the most important outcome
  summary: list of 2-4 short bullet strings
  decisions: list of strings
  action_items: list of {"owner": name, "what": short imperative, "due": string or null, "task_id": id or null}
  blockers: list of {"task_id": id or null, "text": what is blocked and on what, "owner": name or null}
  risks: list of strings (deadline or dependency risks visible from the board)
Use only facts in the lines and the board. Names must be team members' first names."""


def _fallback_summary(meeting: Meeting, flagged: list[Utterance], tasks: dict[str, Task]) -> dict:
    get = lambda u, k: json.loads(u.flags).get(k, 0)
    first = lambda u: u.speaker.split()[0]
    decisions = [u.text for u in flagged if get(u, "decision") >= 0.5]
    actions = [{"owner": first(u), "what": u.text, "due": None, "task_id": u.task_id}
               for u in flagged if get(u, "commitment") >= 0.5]
    blockers = [{"task_id": u.task_id, "text": u.text, "owner": first(u)} for u in flagged if get(u, "blocker") >= 0.5]
    updates = [u for u in flagged if get(u, "update") >= 0.5 and u.task_id]
    n = lambda k, word: f"{k} {word}{'' if k == 1 else 's'}"
    return {
        "headline": f"{n(len(updates), 'status update')}, {n(len(blockers), 'blocker')} and "
                    f"{n(len(actions), 'action item')}.",
        "summary": [f"{first(u)} ({tasks[u.task_id].title}): {u.text}" for u in updates[:4] if u.task_id in tasks],
        "decisions": decisions, "action_items": actions, "blockers": blockers,
        "risks": [f"{t.title} is blocked: {t.blocker_text}" for t in tasks.values() if t.status == "Blocked"],
    }


async def _summarise(s: Session, meeting: Meeting, flagged: list[Utterance]) -> tuple[dict, str]:
    tasks = {t.id: t for t in s.exec(select(Task)).all()}
    members = {m.id: m for m in s.exec(select(Member)).all()}
    if settings.llm_enabled and flagged:
        fmt = lambda u: (f"[{int(u.start_s // 60):02d}:{int(u.start_s % 60):02d}] {u.speaker}: {u.text}"
                         + (f"  (about {u.task_id})" if u.task_id else ""))
        board = "\n".join(f"{t.id} {t.title} | owner {members[t.owner_id].name} | {t.status}"
                          + (f" | blocked: {t.blocker_text}" if t.blocker_text else "")
                          + (f" | due {t.deadline}" if t.deadline else "") for t in tasks.values())
        data = await llm.complete_json(SUMMARY_SYSTEM, f"Meeting: {meeting.title}\n\nBoard:\n{board}\n\n"
                                                       f"Flagged lines:\n" + "\n".join(fmt(u) for u in flagged))
        if data and data.get("headline"):
            return data, "llm"
    return _fallback_summary(meeting, flagged, tasks), "rules"


async def _status_signals(s: Session, meeting: Meeting, flagged: list[Utterance]) -> list[dict]:
    """Status claims heard in the meeting become reviews (or conflicts), never silent changes."""
    outbox: list[dict] = []
    latest: dict[tuple[str, int], Utterance] = {}
    for u in flagged:                                   # last thing each person said about each task wins
        f = json.loads(u.flags)
        if not u.member_id:
            continue
        member = s.get(Member, u.member_id)
        claim = service.done_claim_about_others(s, member, u.text)
        if claim:                                       # "the integration is done, so I'm starting checkout"
            u.task_id = claim[0].id
            latest[(u.task_id, u.member_id)] = u
        elif u.task_id and (f.get("update", 0) >= 0.5 or f.get("blocker", 0) >= 0.5) and not _mostly_a_promise(f):
            latest[(u.task_id, u.member_id)] = u
    for (task_id, member_id), u in latest.items():
        task, member = s.get(Task, task_id), s.get(Member, member_id)
        if not task or not member:
            continue
        msg = Message(member_id=member.id, task_id=task.id, direction="in", kind="meeting", channel="meeting",
                      text=f"[{meeting.title}] {u.text}")
        s.add(msg)
        s.commit()
        s.refresh(msg)
        if task.owner_id != member.id:
            outbox += await service.check_conflict(s, member, task, u.text, msg)
            continue
        interp = await interpret(u.text, service.context(s, task), service.corrections(s))
        interp.trace.append(f"meeting: '{meeting.title}' at {int(u.start_s // 60)}:{int(u.start_s % 60):02d}")
        decision = gate(interp, task.status)
        if decision.new_status is None or decision.new_status == task.status and not interp.blocker_text:
            continue
        if decision.action == "apply" and settings.meeting_auto_apply:
            interp.engine = f"meeting/{interp.engine}"
            outbox += service.apply(s, task, decision, msg, member.name)
        else:
            decision.reasons.append("heard in a meeting transcript")
            outbox += service.send_to_review(s, task, decision, member, msg, source="meeting")
    return outbox


def slack_blocks(meeting: Meeting, summary: dict, stats: dict, members_by_name: dict[str, Member]) -> list[dict]:
    def who(name: str | None) -> str:
        m = members_by_name.get((name or "").lower())
        return f"<@{m.slack_user_id}>" if m and m.slack_user_id else (name or "someone")

    sec = lambda text: {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📝 {meeting.title}"[:150]}},
        sec(f"*{summary.get('headline', '')}*"),
    ]
    if summary.get("summary"):
        blocks.append(sec("\n".join(f"• {b}" for b in summary["summary"])))
    if summary.get("decisions"):
        blocks.append(sec("*Decisions*\n" + "\n".join(f"• {d}" for d in summary["decisions"])))
    if summary.get("action_items"):
        blocks.append(sec("*Action items*\n" + "\n".join(
            f"• {who(a.get('owner'))}: {a.get('what')}" + (f" _(due {a['due']})_" if a.get("due") else "")
            for a in summary["action_items"])))
    if summary.get("blockers"):
        blocks.append(sec("*Blockers*\n" + "\n".join(f"• ⛔ {b.get('text')}" for b in summary["blockers"])))
    if summary.get("risks"):
        blocks.append(sec("*Risks*\n" + "\n".join(f"• ⚠️ {r}" for r in summary["risks"])))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
        f"{stats.get('utterances', 0)} lines triaged by {stats.get('triage_engine')}, "
        f"{stats.get('flagged', 0)} sent to the {stats.get('summary_engine')} summary · "
        f"{stats.get('reviews', 0)} board updates waiting for the lead"}]})
    return blocks


@jobs.handler("meeting.analyze")
async def analyze(meeting_id: int) -> None:
    with Session(engine) as s, span("meeting.analyze", meeting_id=meeting_id):
        meeting = s.get(Meeting, meeting_id)
        if not meeting:
            return
        meeting.status = "processing"
        s.add(meeting)
        s.commit()
        bus.publish("meeting", meeting_id=meeting_id)
        try:
            await _triage_pending(s, meeting)
            utts = s.exec(select(Utterance).where(Utterance.meeting_id == meeting.id).order_by(Utterance.start_s)).all()
            flagged = [u for u in utts if u.flagged]
            outbox = await _status_signals(s, meeting, flagged)
            summary, engine_used = await _summarise(s, meeting, flagged)
            engines = {json.loads(u.flags).get("engine") for u in utts}
            stats = {"utterances": len(utts), "flagged": len(flagged), "triage_engine": "jev" if "jev" in engines else "rules",
                     "summary_engine": engine_used, "reviews": sum(1 for o in outbox if o["kind"] == "review"),
                     "reduction": round(1 - len(flagged) / len(utts), 2) if utts else 0}
            meeting.summary, meeting.stats = json.dumps(summary), json.dumps(stats)
            meeting.status, meeting.ended_at = "done", meeting.ended_at or now()
            s.add(meeting)
            s.commit()

            # action items: DM each owner through their own channel
            members = s.exec(select(Member)).all()
            by_name = {m.name.lower(): m for m in members}
            for a in summary.get("action_items") or []:
                m = by_name.get(str(a.get("owner") or "").split(" ")[0].lower())
                if m:
                    due = f" (due {escape(str(a['due']))})" if a.get("due") else ""
                    outbox.append(service.out(m.id, f"📝 From <b>{escape(meeting.title)}</b>: {escape(str(a.get('what')))}{due}",
                                              "system", a.get("task_id")))
            await channels.deliver(s, outbox)

            # summary to the Slack channel
            slack = channels.REGISTRY.get("slack")
            if slack and slack.ready() and settings.slack_summary_channel:
                meeting.slack_ts = await slack.post(settings.slack_summary_channel, summary.get("headline", meeting.title),
                                                    slack_blocks(meeting, summary, stats, by_name))
                s.add(meeting)
                s.commit()
            log.info("meeting %s analysed: %s", meeting_id, stats)
        except Exception as e:
            log.exception("meeting %s failed", meeting_id)
            meeting.status, meeting.error = "failed", str(e)[:500]
            s.add(meeting)
            s.commit()
        bus.publish("meeting", meeting_id=meeting_id)
        bus.publish("board")


@jobs.handler("meeting.poll_recall")
async def poll_recall() -> None:
    """Worker safety net: finish meetings whose bot left the call, even if no webhook arrived."""
    from .providers import RecallClient

    if not settings.recall_enabled:
        return
    rc = RecallClient()
    with Session(engine) as s:
        live = s.exec(select(Meeting).where(Meeting.provider == "recall",
                                            Meeting.status.in_(["joining", "in_call"]))).all()
        for m in live:
            try:
                bot = await rc.get_bot(m.provider_bot_id)
            except Exception as e:
                log.warning("recall poll %s: %s", m.id, e)
                continue
            code = rc.status_of(bot)
            if code in ("in_call_recording", "in_call_not_recording") and m.status != "in_call":
                m.status = "in_call"
            elif code in ("call_ended", "done", "fatal"):
                have = s.exec(select(Utterance).where(Utterance.meeting_id == m.id)).first()
                if not have:                                  # webhooks missed: pull the final transcript
                    add_lines(s, m, await rc.transcript(bot))
                m.ended_at = now()
                s.add(m)
                s.commit()
                await jobs.enqueue("meeting.analyze", meeting_id=m.id)
                continue
            elif m.created_at < now() - timedelta(hours=4):
                m.status, m.error = "failed", "bot never finished"
            s.add(m)
            s.commit()
            bus.publish("meeting", meeting_id=m.id)
