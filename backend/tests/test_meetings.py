"""Official meetings announced in chat: recognised, scheduled, reminded, cancellable. Social plans are ignored."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import appointments, channels, converse, service
from app.config import settings
from app.db import engine as db_engine
from app.main import app
from app.models import Appointment, Member

NOW = datetime(2026, 10, 17, 4, 30, tzinfo=timezone.utc)
TEAM = [Member(id=i, key=n.lower(), name=n, role="member") for i, n in enumerate(["Tejas", "Soham", "Samarth", "Atharva"], 1)]


def val(items, appts=None):
    return converse.validate(items, TEAM[1], TEAM, [], NOW.date(), now=NOW, appointments=appts)


def meet(**kw):
    return {"kind": "meeting", "title": "Group meeting", "in_minutes": 20, "official": True, "confidence": 0.9, "evidence": "x", **kw}


@pytest.mark.parametrize("text", ["Tejas we have a group meeting in 20 minutes", "stand-up at 10am tomorrow", "demo with the mentor on Monday at 4pm",
                                  "call in 5 min", "sync at 3:30pm"])
def test_gate_lets_meeting_announcements_through(text):
    assert converse.looks_actionable(text)


def test_official_meeting_is_scheduled_for_the_whole_team():
    [p] = val([meet()])
    assert p.kind == "meeting" and p.attendee_ids == [] and p.starts_at == NOW + timedelta(minutes=20)


def test_social_plans_are_not_meetings():
    assert val([meet(title="Pegging sesh", official=False)]) == []
    assert val([meet(official=None)]) == []


@pytest.mark.parametrize("bad", [{"in_minutes": 0}, {"in_minutes": None, "start": "2020-01-01T10:00"}, {"in_minutes": None, "start": "garbage"},
                                 {"in_minutes": None}, {"title": "x"}])
def test_meetings_need_a_real_future_time_and_title(bad):
    assert val([meet(**bad)]) == []


def test_named_attendees_resolve():
    [p] = val([meet(attendees=["Tejas", "Atharva"])])
    assert p.attendee_ids == [1, 4]


def test_the_same_meeting_announced_twice_is_one():
    existing = [Appointment(title="Group meeting", starts_at=NOW + timedelta(minutes=22))]
    assert val([meet()], existing) == []
    assert len(val([meet()], [Appointment(title="Group meeting", starts_at=NOW + timedelta(hours=5))])) == 1


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "auto_tasks", True)
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def add(minutes, **kw):
    with Session(db_engine) as s:
        a = service.create_appointment(s, title="Group meeting", starts_at=datetime.now(timezone.utc) + timedelta(minutes=minutes), **kw)
        return a.id


def test_apply_creates_the_meeting_with_a_cancel_button(world):
    with Session(db_engine) as s:
        who = s.exec(select(Member)).first()
        [p] = converse.validate([meet()], who, list(s.exec(select(Member)).all()), [], NOW.date(), now=datetime.now(timezone.utc))
        lines, buttons = converse.apply(s, who, [p])
        assert any("Group meeting" in x for x in lines) and buttons[0][0][1].startswith("mc:")
        assert s.exec(select(Appointment)).first().status == "scheduled"


def test_tick_reminds_once_then_announces_start_then_finishes(world, monkeypatch):
    sent = []

    async def fake(s, text, **kw):
        sent.append(text)
        return "1", "telegram"
    monkeypatch.setattr(channels, "deliver_group", fake)
    monkeypatch.setattr(channels, "_group_destinations", lambda s: [("telegram", 1, 1)])
    aid = add(20)
    with Session(db_engine) as s:                                   # pretend it was announced 20 minutes ago
        a = s.get(Appointment, aid)
        a.created_at = a.created_at - timedelta(minutes=20)
        s.add(a)
        s.commit()
    start = datetime.now(timezone.utc) + timedelta(minutes=20)
    assert asyncio.run(appointments.tick(start - timedelta(minutes=10))) == 0
    assert asyncio.run(appointments.tick(start - timedelta(minutes=4))) == 1 and "starts in" in sent[-1]
    assert asyncio.run(appointments.tick(start - timedelta(minutes=3))) == 0           # not twice
    assert asyncio.run(appointments.tick(start + timedelta(seconds=10))) == 1 and "starting now" in sent[-1]
    asyncio.run(appointments.tick(start + timedelta(minutes=30)))
    with Session(db_engine) as s:
        assert s.get(Appointment, aid).status == "done"


def test_a_meeting_announced_inside_the_reminder_window_gets_no_early_reminder(world, monkeypatch):
    sent = []

    async def fake(s, text, **kw):
        sent.append(text)
        return "1", "telegram"
    monkeypatch.setattr(channels, "deliver_group", fake)
    monkeypatch.setattr(channels, "_group_destinations", lambda s: [("telegram", 1, 1)])
    add(3)
    assert asyncio.run(appointments.tick()) == 0 and not sent


def test_api_lists_and_cancels(world):
    aid = add(30)
    rows = world.get("/api/appointments").json()
    assert rows[0]["title"] == "Group meeting" and rows[0]["attendees"] == ["everyone"]
    assert world.delete(f"/api/appointments/{aid}").status_code == 200
    assert world.delete(f"/api/appointments/{aid}").status_code == 404
    assert world.get("/api/appointments").json() == []
