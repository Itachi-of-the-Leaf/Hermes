"""Meeting reminders: the agent said it would remind everyone, so it does.

`tick()` runs every 30 seconds. For each scheduled meeting it posts, once each:
  * a reminder `remind_min` minutes before the start (skipped when the meeting was announced inside that window),
  * a "starting now" note at the start,
and marks the meeting done shortly afterwards. Posting goes to the team's group chats (with real @mentions of the attendees);
with no group configured each attendee gets it on their own channel.
"""
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from sqlmodel import Session, select

from . import bus, channels
from .db import engine
from .models import Appointment, Member

log = logging.getLogger("appointments")
DONE_AFTER = timedelta(minutes=20)


def attendees(s: Session, a: Appointment) -> list[int]:
    ids = [int(i) for i in a.attendee_ids.split(",") if i]
    return ids or [m.id for m in s.exec(select(Member)).all()]


async def post(s: Session, a: Appointment, text: str) -> None:
    who = attendees(s, a)
    body = " ".join("{M%d}" % i for i in who) + " " + text
    if channels._group_destinations(s):                            # one message in the group, everyone mentioned
        await channels.deliver_group(s, body, kind="reminder", recipient_ids=who)
    else:                                                          # no group: each person on their own channel
        for i in who:
            await channels.deliver_group(s, "{M%d} " % i + text, kind="reminder", recipient_ids=[i])


async def tick(now: datetime | None = None) -> int:
    """Returns how many messages were posted."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    sent = 0
    with Session(engine) as s:
        for a in s.exec(select(Appointment).where(Appointment.status == "scheduled")).all():
            start = a.starts_at.astimezone(timezone.utc)
            created = (a.created_at or now).astimezone(timezone.utc)
            remind_at = start - timedelta(minutes=a.remind_min)
            title = f"<b>{escape(a.title)}</b>"
            changed = False
            if a.reminded_at is None and now >= remind_at:
                if now < start and created < remind_at:            # announced early enough for a reminder to make sense
                    await post(s, a, f"⏰ {title} starts in {max(1, int((start - now).total_seconds() // 60) + 1)} min.")
                    sent += 1
                a.reminded_at, changed = now, True
            if a.started_at is None and now >= start:
                if now < start + timedelta(minutes=5):             # not for a meeting we only noticed long after it began
                    await post(s, a, f"▶️ {title} is starting now.")
                    sent += 1
                a.started_at, changed = now, True
            if now >= start + DONE_AFTER:
                a.status, changed = "done", True
            if changed:
                s.add(a)
                s.commit()
                bus.publish("board")
    return sent
