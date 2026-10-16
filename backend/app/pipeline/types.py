from dataclasses import asdict, dataclass, field
from typing import Optional

INTENTS = ["completed", "progress", "blocked", "question", "off_topic"]


@dataclass
class TaskContext:
    task_id: str
    title: str
    status: str
    owner: str
    depends_on: list[str] = field(default_factory=list)       # titles
    recent: list[str] = field(default_factory=list)           # last messages in this thread
    team: list[str] = field(default_factory=list)             # member names, for blocked_by


@dataclass
class Interpretation:
    intent: str = "question"
    confidence: float = 0.0
    waiting_on_something: Optional[float] = None   # probability, from Jev or rules
    work_remaining: Optional[float] = None
    blocker_text: Optional[str] = None
    blocker_type: Optional[str] = None             # external | teammate | knowledge | resource | personal
    blocked_by: Optional[str] = None               # a member name
    progress_pct: Optional[int] = None
    eta: Optional[str] = None
    proof_url: Optional[str] = None
    hedged: bool = False
    engine: str = "rules"
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    action: str                 # apply | review | ignore | clarify
    new_status: Optional[str]
    interp: Interpretation
    reasons: list[str] = field(default_factory=list)
