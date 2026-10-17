import json
import logging
import secrets

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import bus, jobs
from ..config import settings
from ..db import get_session
from ..models import Meeting, Member, Utterance
from . import analyze  # noqa: F401  (registers job handlers)
from .analyze import add_lines
from .providers import RecallClient, from_recall_segment, parse_transcript

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
log = logging.getLogger("meetings")


def _view(s: Session, m: Meeting, full: bool = False) -> dict:
    out = {**m.model_dump(), "summary": json.loads(m.summary) if m.summary else None, "stats": json.loads(m.stats)}
    if full:
        names = {x.id: x.name for x in s.exec(select(Member)).all()}
        utts = s.exec(select(Utterance).where(Utterance.meeting_id == m.id).order_by(Utterance.start_s)).all()
        out["utterances"] = [{**u.model_dump(), "flags": json.loads(u.flags), "member": names.get(u.member_id)}
                             for u in utts]
    return out


@router.get("")
def list_meetings(s: Session = Depends(get_session)):
    return [_view(s, m) for m in s.exec(select(Meeting).order_by(Meeting.created_at.desc())).all()]


@router.get("/{meeting_id}")
def get_meeting(meeting_id: int, s: Session = Depends(get_session)):
    m = s.get(Meeting, meeting_id)
    if not m:
        raise HTTPException(404)
    return _view(s, m, full=True)


class JoinIn(BaseModel):
    meeting_url: str
    title: str = "Team sync"


@router.post("")
async def join_meeting(body: JoinIn, s: Session = Depends(get_session)):
    """Send the agent into a Teams / Zoom / Meet call by link."""
    if not settings.recall_enabled:
        raise HTTPException(400, "Set RECALL_API_KEY to let the agent join calls, or upload a transcript instead.")
    m = Meeting(title=body.title, meeting_url=body.meeting_url, provider="recall", status="joining")
    s.add(m)
    s.commit()
    s.refresh(m)
    try:
        m.provider_bot_id = await RecallClient().create_bot(body.meeting_url, m.id)
    except Exception as e:
        m.status, m.error = "failed", f"Recall: {e}"[:500]
    s.add(m)
    s.commit()
    bus.publish("meeting", meeting_id=m.id)
    return _view(s, m)


@router.post("/upload")
async def upload_transcript(title: str = Form("Team sync"), file: UploadFile | None = File(None),
                            text: str | None = Form(None), s: Session = Depends(get_session)):
    """A .vtt from Teams ("Download transcript") or plain 'Name: text' lines."""
    raw = (await file.read()).decode("utf-8", "replace") if file else (text or "")
    lines = parse_transcript(raw)
    if not lines:
        raise HTTPException(400, "No transcript lines found. Use WebVTT or 'Name: text' lines.")
    m = Meeting(title=title, provider="upload", status="processing")
    s.add(m)
    s.commit()
    s.refresh(m)
    add_lines(s, m, lines)
    await jobs.enqueue("meeting.analyze", meeting_id=m.id)
    return _view(s, m)


@router.post("/{meeting_id}/finish")
async def finish(meeting_id: int, s: Session = Depends(get_session)):
    """Make the bot leave and summarise (also happens automatically when the call ends).

    A Recall meeting is summarised by the poller once Recall has the transcript; summarising here, while the bot
    was still leaving, ended meetings with no lines. Calling this on a finished meeting fetches its transcript again."""
    m = s.get(Meeting, meeting_id)
    if not m:
        raise HTTPException(404)
    if m.provider == "recall" and m.provider_bot_id and settings.recall_enabled:
        try:
            await RecallClient().leave(m.provider_bot_id)
        except Exception:
            pass                                    # already out of the call
        m.status, m.error = "ending", None
        s.add(m)
        s.commit()
        bus.publish("meeting", meeting_id=m.id)
        await jobs.enqueue("meeting.poll_recall")
        return {"ok": True}
    await jobs.enqueue("meeting.analyze", meeting_id=m.id)
    return {"ok": True}


@router.post("/webhooks/recall")
async def recall_webhook(request: Request, token: str = Query(""), meeting_id: int = Query(...),
                         s: Session = Depends(get_session)):
    """Real-time transcript lines. Authenticated by the secret token we put in the URL."""
    if not secrets.compare_digest(token, settings.webhook_token):
        raise HTTPException(401)
    payload = await request.json()
    m = s.get(Meeting, meeting_id)
    if not m:
        # Usually another server created this bot with our PUBLIC_BASE_URL (e.g. a deployed backend pointing at
        # a laptop tunnel): its live transcript lands here and is lost to it.
        log.warning("Recall webhook for unknown meeting %s: is PUBLIC_BASE_URL on the server that joined the call "
                    "set to this server's own URL?", meeting_id)
        return {"ok": True}
    if payload.get("event") == "transcript.data":
        seg = (payload.get("data") or {}).get("data") or {}
        line = from_recall_segment(seg)
        if line.text:
            if m.status == "joining":
                m.status = "in_call"
                s.add(m)
                s.commit()
            add_lines(s, m, [line])
            await jobs.enqueue("meeting.live_triage", meeting_id=m.id)
    return {"ok": True}                 # always 2xx fast; work happens in the job
