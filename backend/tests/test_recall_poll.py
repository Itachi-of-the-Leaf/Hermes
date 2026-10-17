"""Recall meetings are summarised from the full transcript, never before Recall has it."""
import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import jobs
from app.config import settings
from app.db import engine
from app.main import app
from app.meetings import analyze
from app.meetings.providers import Line, RecallClient
from app.models import Meeting, Utterance, now


@pytest.fixture
def c():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def recall(monkeypatch):
    """A fake Recall: set `bot` (codes and when the call ended) and `lines` (the final transcript)."""
    state = {"codes": ["in_call_recording"], "ended_ago": None, "lines": [], "left": [], "queued": []}

    async def get_bot(self, bot_id):
        changes = [{"code": code} for code in state["codes"]]
        if state["ended_ago"] is not None:
            for ch in changes:
                if ch["code"] == "call_ended":
                    ch["created_at"] = (now() - state["ended_ago"]).isoformat().replace("+00:00", "Z")
        return {"id": bot_id, "status_changes": changes}

    async def transcript(self, bot):
        return list(state["lines"])

    async def leave(self, bot_id):
        state["left"].append(bot_id)

    async def enqueue(name, **kwargs):
        state["queued"].append((name, kwargs))

    monkeypatch.setattr(settings, "recall_api_key", "test-key")
    monkeypatch.setattr(RecallClient, "get_bot", get_bot)
    monkeypatch.setattr(RecallClient, "transcript", transcript)
    monkeypatch.setattr(RecallClient, "leave", leave)
    monkeypatch.setattr(jobs, "enqueue", enqueue)
    return state


def meeting(status="in_call") -> int:
    with Session(engine) as s:
        m = Meeting(title="Team sync", provider="recall", provider_bot_id="bot-1", status=status)
        s.add(m)
        s.commit()
        return m.id


def load(mid: int) -> tuple[Meeting, int]:
    with Session(engine) as s:
        return s.get(Meeting, mid), len(s.exec(select(Utterance).where(Utterance.meeting_id == mid)).all())


def analysed(state, mid) -> bool:
    return ("meeting.analyze", {"meeting_id": mid}) in state["queued"]


def test_waits_for_the_transcript_after_the_call_ends(c, recall):
    mid = meeting()
    recall.update(codes=["in_call_recording", "call_ended"], ended_ago=timedelta(seconds=5))
    asyncio.run(analyze.poll_recall())
    m, n = load(mid)
    assert m.status == "ending" and n == 0 and not analysed(recall, mid)     # not summarised from nothing


def test_summarises_from_the_final_transcript_once_recall_is_done(c, recall):
    mid = meeting("ending")
    recall.update(codes=["in_call_recording", "call_ended", "recording_done", "done"], ended_ago=timedelta(seconds=30),
                  lines=[Line("Soham", "The login API is blocked on the schema"), Line("Samarth", "I'll send it tonight")])
    asyncio.run(analyze.poll_recall())
    m, n = load(mid)
    assert n == 2 and m.ended_at and analysed(recall, mid)


def test_keeps_waiting_while_the_transcript_is_still_processing(c, recall):
    mid = meeting("ending")
    recall.update(codes=["call_ended", "done"], ended_ago=timedelta(minutes=2), lines=[])
    asyncio.run(analyze.poll_recall())
    assert load(mid)[0].status == "ending" and not analysed(recall, mid)


def test_gives_up_waiting_and_summarises_a_silent_call(c, recall):
    mid = meeting("ending")
    recall.update(codes=["call_ended", "done"], ended_ago=analyze.TRANSCRIPT_WAIT + timedelta(minutes=1), lines=[])
    asyncio.run(analyze.poll_recall())
    assert analysed(recall, mid)


def test_bot_still_in_the_call_does_not_undo_ending(c, recall):
    mid = meeting("ending")
    asyncio.run(analyze.poll_recall())
    assert load(mid)[0].status == "ending"


def test_end_and_summarise_waits_for_the_transcript(c, recall):
    mid = meeting()
    assert c.post(f"/api/meetings/{mid}/finish").json() == {"ok": True}
    assert recall["left"] == ["bot-1"]
    assert load(mid)[0].status == "ending"
    assert ("meeting.poll_recall", {}) in recall["queued"] and not analysed(recall, mid)


def test_fetch_again_recovers_a_meeting_summarised_with_no_lines(c, recall):
    mid = meeting("done")
    assert c.post(f"/api/meetings/{mid}/finish").status_code == 200
    assert load(mid)[0].status == "ending"            # the poller pulls the transcript and summarises again
