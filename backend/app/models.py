"""Database tables. Events are append-only: they are the audit trail and power Undo."""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

STATUSES = ["Pending", "In Progress", "Blocked", "Complete"]
CHANNELS = ["telegram", "slack", "teams"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class Member(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)          # short id, e.g. "tejas"
    name: str
    email: Optional[str] = Field(default=None, index=True)   # links Slack / Teams accounts
    role: str = "member"                                # member | lead
    preferred_channel: Optional[str] = None             # telegram | slack | teams
    # Telegram
    tg_user_id: Optional[int] = Field(default=None, index=True)
    tg_chat_id: Optional[int] = None
    tg_username: Optional[str] = Field(default=None, index=True)   # learned when they write; resolves "@JustSoham" to Soham
    # Slack
    slack_user_id: Optional[str] = Field(default=None, index=True)
    slack_dm_id: Optional[str] = None
    # Teams
    teams_aad_id: Optional[str] = Field(default=None, index=True)
    teams_conv_id: Optional[str] = None
    teams_service_url: Optional[str] = None
    # conversation state
    awaiting_blocker_task: Optional[str] = None         # set after the "Stuck" button
    pending_text_id: Optional[int] = None               # message waiting for "which task?"

    def linked_channels(self) -> list[str]:
        out = []
        if self.tg_chat_id:
            out.append("telegram")
        if self.slack_dm_id:
            out.append("slack")
        if self.teams_conv_id:
            out.append("teams")
        return out


class Task(SQLModel, table=True):
    id: str = Field(primary_key=True)                   # "T2"
    title: str
    owner_id: int = Field(foreign_key="member.id")
    requested_by_id: Optional[int] = None               # who asked for it (chat or task: command); None = dashboard/seed
    status: str = "Pending"
    progress_pct: Optional[int] = None
    deadline: Optional[str] = None
    eta: Optional[str] = None
    depends_on: str = ""                                # comma-separated task ids
    aliases: str = ""                                   # pipe-separated words people use for it
    blocker_text: Optional[str] = None
    blocker_type: Optional[str] = None
    blocked_by_id: Optional[int] = None
    blocker_resolved: bool = False
    proof_url: Optional[str] = None
    last_update_at: datetime = Field(default_factory=now)
    last_followup_at: Optional[datetime] = None
    reminder_count: int = 0
    est_minutes: Optional[int] = None                   # estimated total effort; None = unknown (never silently 0)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    member_id: Optional[int] = Field(default=None, foreign_key="member.id")
    task_id: Optional[str] = Field(default=None, index=True)
    direction: str                                      # in | out
    kind: str = "reply"                                 # followup | reply | group | unblock_request | confirm | system | meeting
    channel: str = "telegram"                           # telegram | slack | teams | dev | meeting | system | failed
    ext_id: Optional[str] = Field(default=None, index=True)   # platform message id (Telegram id, Slack ts, Teams id)
    reply_to_ext: Optional[str] = None
    text: str
    at: datetime = Field(default_factory=now)


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    old_status: str
    new_status: str
    reason: str
    engine: str                                         # button | rules | jev | llm | lead | undo | meeting
    confidence: Optional[float] = None
    evidence: Optional[str] = None                      # quoted message
    message_id: Optional[int] = None
    actor: str = "bot"
    trace: str = "[]"                                   # JSON list of pipeline steps
    before: str = "{}"                                  # JSON snapshot used by Undo
    reverted: bool = False
    at: datetime = Field(default_factory=now)


class Review(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str
    member_id: Optional[int] = None
    message_id: Optional[int] = None
    source: str = "chat"                                # chat | meeting
    proposal: str                                       # JSON Interpretation + proposed status
    reasons: str = "[]"
    status: str = "pending"                             # pending | approved | overridden
    resolved_status: Optional[str] = None
    at: datetime = Field(default_factory=now)


class Correction(SQLModel, table=True):
    """Lead overrides, reused as few-shot examples for the LLM."""
    id: Optional[int] = Field(default=None, primary_key=True)
    text: str
    wrong_status: str
    right_status: str
    at: datetime = Field(default_factory=now)


class PRDIngestion(SQLModel, table=True):
    """Source identity and bounded outcome summary for lead-authorized PRD ingestion."""
    __tablename__ = "prd_ingestion"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_hash: str = Field(max_length=64, index=True)
    source_ref: str = Field(max_length=500)
    source_name: str = Field(max_length=255)
    uploaded_by_id: int = Field(foreign_key="member.id", index=True)
    created_at: datetime = Field(default_factory=now)
    result: str = "{}"                                      # JSON summary only; never persist raw PRD content


class Meeting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    meeting_url: Optional[str] = None
    provider: str = "upload"                            # recall | upload
    provider_bot_id: Optional[str] = Field(default=None, index=True)
    status: str = "created"                             # created | joining | in_call | ending | processing | done | failed
    error: Optional[str] = None
    summary: Optional[str] = None                       # JSON (headline, decisions, action items, blockers, risks)
    stats: str = "{}"                                   # JSON (utterances, flagged, engine, cost)
    slack_ts: Optional[str] = None
    created_at: datetime = Field(default_factory=now)
    ended_at: Optional[datetime] = None


class Utterance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    meeting_id: int = Field(index=True, foreign_key="meeting.id")
    speaker: str
    member_id: Optional[int] = None
    text: str
    start_s: float = 0
    end_s: Optional[float] = None
    task_id: Optional[str] = None
    flags: str = "{}"                                   # JSON from triage: update / blocker / commitment / decision
    flagged: bool = False
    triaged: bool = False


class AgentAction(SQLModel, table=True):
    """Every autonomous intervention: why it was taken, who got it, and how they answered."""
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=now)
    type: str                                           # REQUEST_ETA | REMIND_OWNER | ... (planner.ACTIONS)
    risk_type: str
    task_id: str = Field(index=True)
    recipient_id: int = Field(foreign_key="member.id")
    dedupe_key: str = Field(index=True)
    context_hash: str = ""
    reason: str = "{}"                                  # JSON: the risk event that justified it
    text: str = ""
    status: str = "pending"                             # pending | sent | failed | unreachable | committed | resolved | expired
    channel: Optional[str] = None
    ext_id: Optional[str] = Field(default=None, index=True)   # platform message id, to correlate replies
    commitment_at: Optional[datetime] = None            # what the person promised ("in 20 minutes")
    followup_at: Optional[datetime] = None
    response_text: Optional[str] = None
    responded_at: Optional[datetime] = None
    graph_version: str = ""


class Forecast(SQLModel, table=True):
    """Latest schedule forecasts (kept for comparison and for the dashboard)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=now)
    version: str = Field(index=True)
    status: str
    payload: str                                        # JSON: ScheduleResult + risks + analysis


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class Appointment(SQLModel, table=True):
    """A team meeting announced in conversation ("group meeting in 20 minutes"): the agent confirms it, reminds
    everyone shortly before it starts, and lists it on the dashboard."""
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    starts_at: datetime                                 # timezone-aware UTC
    created_by_id: Optional[int] = Field(default=None, foreign_key="member.id")
    attendee_ids: str = ""                              # comma-separated member ids; empty = the whole team
    source_text: str = ""                               # the message it came from (evidence)
    status: str = "scheduled"                           # scheduled | cancelled | done
    remind_min: int = 5                                 # reminder this many minutes before the start
    reminded_at: Optional[datetime] = None
    started_at: Optional[datetime] = None               # when the "starting now" note was posted
    created_at: datetime = Field(default_factory=now)
