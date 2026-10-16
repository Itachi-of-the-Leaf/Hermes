"""Deterministic layer.

1. A fast heuristic classifier: handles obvious replies with zero API calls, and is the
   offline fallback so the app works before any key is configured.
2. Field extractors (blocker text, proof link, ETA, progress %) used by every engine,
   because Jev returns decisions, never text.
"""
import re

from .types import Interpretation, TaskContext

DONE = re.compile(
    r"\b(done|completed?|finished|merged|shipped|deployed|pushed|all green|tests? pass(ed|ing)?|"
    r"ho gaya|hogaya|ho gya|kar diya|kardiya|ready)\b|✅",
    re.I,
)
BLOCK = re.compile(
    r"\b(waiting (for|on)|wait kar|blocked|stuck|can'?t|cannot|unable|no access|don'?t have|"
    r"haven'?t (got|received)|need .+ (first|before)|depends on|until|nahi mila|ruka|sick|ill|error)\b|⛔|🚫",
    re.I,
)
HEDGE = re.compile(
    r"\b(almost|nearly|mostly|should be|except|but|still|left|remaining|pending|tonight|tomorrow|"
    r"kal|will|soon|in a bit|later|halfway|partly|partially|testing)\b",
    re.I,
)
PROGRESS = re.compile(r"\b(working|started|in progress|on it|doing|testing|halfway|kar raha|\d{1,3}\s?%)", re.I)
QUESTION = re.compile(r"\?\s*$|\b(which task|what task|kya|who)\b", re.I)

BLOCKER_CAPTURE = re.compile(
    r"(?:waiting (?:for|on)|blocked (?:by|on)|stuck (?:on|with)|need(?:s)?|depends on|until|"
    r"don'?t have|haven'?t (?:got|received)|no access to)\s+(?:the\s+)?(.+?)(?:[.!;\n]|$)",
    re.I,
)
URL = re.compile(r"(https?://\S+|\bPR\s?#\d+|\b#\d+\b|\bcommit [0-9a-f]{7,40}\b)", re.I)
PCT = re.compile(r"(\d{1,3})\s?%")
ETA = re.compile(r"\b(tonight|today|tomorrow|kal|by \w+|in \d+ (?:hours?|days?|mins?))\b", re.I)

FAST_DONE = {"done", "done!", "done ✅", "completed", "finished", "merged", "ho gaya", "✅"}
FAST_STUCK = {"stuck", "blocked", "⛔"}


def extract_fields(text: str, interp: Interpretation, ctx: TaskContext) -> Interpretation:
    if not interp.blocker_text:
        m = BLOCKER_CAPTURE.search(text)
        if m:
            interp.blocker_text = m.group(1).strip(" ,")[:140]
    if not interp.proof_url:
        m = URL.search(text)
        if m:
            interp.proof_url = m.group(1)
    if interp.progress_pct is None:
        m = PCT.search(text)
        if m:
            interp.progress_pct = min(100, int(m.group(1)))
    if not interp.eta:
        m = ETA.search(text)
        if m:
            interp.eta = m.group(1)
    if not interp.blocked_by:
        low = text.lower()
        for name in ctx.team:
            if name.lower() != ctx.owner.lower() and re.search(rf"\b{re.escape(name.lower())}\b", low):
                interp.blocked_by = name
                interp.blocker_type = interp.blocker_type or "teammate"
                break
    if interp.blocker_text and not interp.blocker_type:
        bt = interp.blocker_text.lower()
        if any(w in bt for w in ("credential", "key", "access", "api", "permission", "approval", "server")):
            interp.blocker_type = "external"
        elif any(w in bt for w in ("sick", "exam", "busy", "travel")):
            interp.blocker_type = "personal"
        else:
            interp.blocker_type = "resource"
    interp.hedged = bool(HEDGE.search(text))
    return interp


def fast_path(text: str) -> Interpretation | None:
    """Exact one-word replies: no model call needed."""
    t = text.strip().lower()
    if t in FAST_DONE:
        return Interpretation(intent="completed", confidence=0.95, engine="rules",
                              trace=[f"rules: exact '{t}' → completed"])
    if t in FAST_STUCK:
        return Interpretation(intent="blocked", confidence=0.95, engine="rules",
                              trace=[f"rules: exact '{t}' → blocked"])
    return None


def heuristic(text: str, ctx: TaskContext) -> Interpretation:
    """Offline classifier. Blocker beats completion, always."""
    blocked = bool(BLOCK.search(text))
    done = bool(DONE.search(text))
    hedged = bool(HEDGE.search(text))
    progress = bool(PROGRESS.search(text))
    if blocked:
        i = Interpretation(intent="blocked", confidence=0.75, waiting_on_something=0.9)
    elif done and not hedged:
        conf = 0.9 if URL.search(text) else 0.8
        i = Interpretation(intent="completed", confidence=conf, work_remaining=0.1)
    elif done or progress or hedged:
        # an explicit %, ETA or "working on it" is an unambiguous progress update
        explicit = bool(PCT.search(text) or ETA.search(text) or progress)
        i = Interpretation(intent="progress", confidence=0.8 if explicit else 0.7, work_remaining=0.8)
    elif QUESTION.search(text):
        i = Interpretation(intent="question", confidence=0.6)
    else:
        i = Interpretation(intent="off_topic", confidence=0.4)
    i.engine = "rules"
    i.trace.append(f"rules: blocker={blocked} done={done} hedge={hedged} → {i.intent} ({i.confidence:.2f})")
    return i
