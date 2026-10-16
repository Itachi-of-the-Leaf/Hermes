"""Closing the loop: a reply to one of the agent's messages becomes a commitment or a resolution.

Binding is deliberately conservative ("don't assume every group message belongs to the latest topic"):
  1. a Telegram *reply* to the agent's message binds to that exact action, if the replier is its recipient;
  2. otherwise a plain message binds only when the sender has exactly one open action AND the text clearly
     contains a delivery claim or a time. Anything else flows through the normal pipeline untouched.

A promise ("in 20 minutes") is recorded as a commitment: the schedule uses it, a follow-up is due after it,
and the blocker is NOT marked resolved. Only a delivery claim ("sent them") resolves the blocker, and even
then the existing flow asks the owner to confirm they can resume.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy import or_
from sqlmodel import Session, select

from .. import bus, channels, service
from ..config import settings
from ..models import AgentAction, Member, Message, Task

log = logging.getLogger("intel.responses")

DELIVERED = re.compile(r"\b(sent|shared|delivered|done|sorted|fixed|resolved|gave|given|emailed|uploaded)\b|✅", re.I)
FUTURE = re.compile(r"\b(will|shall|going to|gonna|i'll|ill|soon)\b|'ll", re.I)
_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", re.I)
_MINS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b", re.I)
_CLOCK = re.compile(r"\bby\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)


def parse_eta(text: str, now: datetime) -> datetime | None:
    """'in 20 minutes', '1.5 hours', 'half an hour', 'by 5pm'. Vague answers ('soon', 'tomorrow') -> None,
    so the agent asks for a concrete time instead of inventing one."""
    low = text.lower()
    mins = sum(float(m) * 60 for m in _HOURS.findall(low)) + sum(float(m) for m in _MINS.findall(low))
    if mins == 0:
        if re.search(r"half an? hour|half hour", low):
            mins = 30
        elif re.search(r"\b(an|one|1) hour\b", low):
            mins = 60
    if mins > 0:
        return now + timedelta(minutes=mins) if mins <= 72 * 60 else None
    m = _CLOCK.search(low)
    if m and (m.group(2) or m.group(3)):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            return None
        local = now.astimezone()
        due = local.replace(hour=h, minute=mi, second=0, microsecond=0)
        if due <= local:
            due += timedelta(days=1)
        return due.astimezone(timezone.utc)
    return None


def _open_actions(s: Session, member: Member) -> list[AgentAction]:
    return list(s.exec(select(AgentAction).where(AgentAction.recipient_id == member.id,
                                                 AgentAction.status.in_(["sent", "committed"]))
                       .order_by(AgentAction.created_at.desc())).all())


def bind(s: Session, member: Member, text: str, reply_to: str | None) -> tuple[AgentAction | None, bool]:
    """-> (action, explicit). explicit=True when it was a direct reply to the agent's message."""
    if reply_to:
        r = str(reply_to)                                     # an action posted to several chats stores "tgid;slackts"
        a = s.exec(select(AgentAction).where(or_(AgentAction.ext_id == r, AgentAction.ext_id.like(f"{r};%"),
                                                 AgentAction.ext_id.like(f"%;{r};%"), AgentAction.ext_id.like(f"%;{r}")))).first()
        if a and a.recipient_id == member.id:
            return a, True
        return None, False
    open_ = _open_actions(s, member)
    if len(open_) == 1 and (DELIVERED.search(text) or parse_eta(text, datetime.now(timezone.utc))):
        return open_[0], False
    return None, False


def is_delivery(text: str) -> bool:
    return bool(DELIVERED.search(text)) and not FUTURE.search(text)


async def try_handle(s: Session, member: Member, text: str, reply_to: str | None = None, ext_id: str | None = None,
                     channel: str = "telegram", now: datetime | None = None) -> list[dict] | None:
    """None = not ours, carry on with the normal pipeline."""
    now = now or datetime.now(timezone.utc)
    action, explicit = bind(s, member, text, reply_to)
    if action is None or action.status not in ("sent", "committed"):
        return None
    task = s.get(Task, action.task_id)
    if task is None:
        return None
    tid, title = task.id, task.title        # plain values: the row may be deleted while we await the chat apps
    from . import engine as intel

    def record() -> None:
        s.add(Message(member_id=member.id, task_id=tid, direction="in", kind="action_reply", channel=channel,
                      ext_id=ext_id, reply_to_ext=reply_to, text=text))
        action.response_text, action.responded_at = text, now
        s.add(action)
        s.commit()
        bus.publish("message", task_id=tid)

    if action.status == "committed" and action.response_text == text:
        return []                                                        # the same reply twice: already handled
    owner = s.get(Member, task.owner_id)
    # The agent asked someone else to act on a blocker; their answer is about that blocker.
    about_blocker = action.type in ("REQUEST_ETA", "REMIND_OWNER") and task.status == "Blocked" and not task.blocker_resolved \
        and action.recipient_id != task.owner_id

    if about_blocker and is_delivery(text):
        record()
        for a in _open_actions(s, member):
            if a.task_id == tid:
                a.status = "resolved"
                s.add(a)
        s.commit()
        outbox = service.resolve_blocker(s, task, member, text)         # asks the owner to confirm they can resume
        await channels.deliver_group(s, f"Thanks {{M{member.id}}}. {{M{owner.id}}}, can you confirm you can resume "
                                        f"<b>{escape(title)}</b>?", task_id=tid, kind="agent_action",
                                     recipient_ids=[member.id, owner.id])
        intel.request("BLOCKER_RESOLVED", tid)
        return outbox

    if about_blocker:
        eta = parse_eta(text, now)
        if eta is not None:
            record()
            for a in _open_actions(s, member):                           # a new promise supersedes the old one
                if a.task_id == tid and a.id != action.id:
                    a.status = "expired"
                    s.add(a)
            action.status, action.commitment_at = "committed", eta
            action.followup_at = eta + timedelta(minutes=settings.intel_commit_grace_min)
            s.add(action)
            s.commit()
            await channels.deliver_group(
                s, f"Thanks {{M{member.id}}}, noted: around {eta.astimezone():%H:%M}. {{M{owner.id}}}, I will check back "
                   f"then; <b>{escape(title)}</b> stays blocked until it is confirmed.",
                task_id=tid, kind="agent_action", recipient_ids=[member.id, owner.id])
            intel.request("COMMITMENT_MADE", tid)
            return []
        if explicit:
            record()
            return [service.out(member.id, f"Thanks! When do you expect <i>{escape(task.blocker_text or 'it')}</i> to be "
                                           f"ready for <b>{escape(owner.name)}</b>? A time like “in 30 minutes” or "
                                           f"“by 5pm” is enough.", "clarify", tid)]
        return None

    # Any other action (a status request to the owner themself): their answer is an ordinary task update.
    if explicit:
        action.response_text, action.responded_at, action.status = text, now, "resolved"
        s.add(action)
        s.commit()
    return None
