"""Turn a chat line into a task. Deterministic on purpose: a task is created only from what the person actually wrote.

    /task Build the login page @soham due friday est 3h after T1
    task: Record the demo video due 2026-10-25 after Build the login page

Recognised, in any order after the title:
    @name | @key              owner (default: the person who wrote it)
    due <date> | by <date>    today, tomorrow, a weekday, 2026-10-25, "25 oct", "oct 25"
    est <n>h | est <n>m       effort estimate (feeds the CP-SAT schedule; unknown stays unknown)
    after <T3 | a task title>[, ...]   prerequisites ("depends on" also works)
Anything it cannot understand becomes an error that is told to the person, never a guess.
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
WEEKDAYS = {d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}

PREFIX = re.compile(r"^\s*(?:/task(?:@\w+)?\b|(?:new|add)\s+task\b|task\s*:)\s*[:\-]?\s*", re.I)
HELP = ("Create a task like this:\n  task: Build the login page @soham due friday est 3h after T1\n"
        "@name = owner (default: you) · due = today, tomorrow, a weekday or 2026-10-25 · est = 90m or 3h · after = prerequisite task ids or titles")

_EST = re.compile(r"\best\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.I)
_OWNER = re.compile(r"@(\w+)")
_DEPS = re.compile(r"\b(?:after|depends\s+on|needs)\b", re.I)
_MON = r"(?:" + "|".join(MONTHS) + r")[a-z]*"
_DATE_FORMS = (
    r"\d{4}-\d{2}-\d{2}",
    r"today|tonight|tomorrow|tmrw",
    r"(?:next\s+)?(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*",
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MON}(?:\s+\d{{4}})?",
    rf"{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?",
)
_DUE = re.compile(r"\b(?:due|by|on)\s+(" + "|".join(f"(?:{f})" for f in _DATE_FORMS) + r")\b", re.I)
_WORD = r"(?!after\b|depends\b|needs\b|est\b|@)\S+"             # a word that is not one of our own keywords
_DUE_ANY = re.compile(rf"\bdue\s+({_WORD}(?:\s+{_WORD})?)", re.I)


@dataclass
class ParsedTask:
    title: str = ""
    owner_key: str | None = None
    due: str | None = None
    est_minutes: int | None = None
    depends_on: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def is_task_command(text: str) -> bool:
    return bool(PREFIX.match(text or ""))


def parse_date(expr: str, today: date) -> date | None:
    e = expr.strip().lower().replace(",", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e):
        try:
            return date.fromisoformat(e)
        except ValueError:
            return None
    if e in ("today", "tonight"):
        return today
    if e in ("tomorrow", "tmrw"):
        return today + timedelta(days=1)
    m = re.fullmatch(r"(?:next\s+)?(mon|tue|wed|thu|fri|sat|sun)[a-z]*", e)
    if m:
        ahead = (WEEKDAYS[m.group(1)] - today.weekday()) % 7 or 7          # the next one, never today
        return today + timedelta(days=ahead)
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+?)(?:\s+(\d{4}))?", e) or None
    if m:
        day, mon, year = int(m.group(1)), m.group(2)[:3], m.group(3)
    else:
        m2 = re.fullmatch(r"([a-z]+?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", e)
        if not m2:
            return None
        mon, day, year = m2.group(1)[:3], int(m2.group(2)), m2.group(3)
    if mon not in MONTHS:
        return None
    try:
        d = date(int(year) if year else today.year, MONTHS[mon], day)
        if not year and d < today:
            d = date(today.year + 1, MONTHS[mon], day)
        return d
    except ValueError:
        return None


def _resolve_person(token: str, people: list[tuple[str, str]]) -> str | None:
    t = token.lower()
    exact = [k for k, n in people if t in (k.lower(), n.lower())]
    if exact:
        return exact[0]
    prefix = [k for k, n in people if n.lower().startswith(t) or k.lower().startswith(t)]
    return prefix[0] if len(prefix) == 1 else None


def _resolve_task(piece: str, tasks: list[tuple[str, str, str]]) -> tuple[str | None, str | None]:
    """-> (task id, error)."""
    p = piece.strip().strip(".,;").lower()
    if not p:
        return None, None
    if re.fullmatch(r"t\d+", p):
        hit = next((i for i, _, _ in tasks if i.lower() == p), None)
        return (hit, None) if hit else (None, f"there is no task {p.upper()}")
    exact = [i for i, title, _ in tasks if title.lower() == p]
    if len(exact) == 1:
        return exact[0], None
    words = set(re.findall(r"[a-z0-9]+", p))
    loose = [i for i, title, aliases in tasks
             if words and (words <= set(re.findall(r"[a-z0-9]+", title.lower()))
                           or p in [a.strip().lower() for a in aliases.split("|")])]
    if len(loose) == 1:
        return loose[0], None
    return None, (f"'{piece.strip()}' matches several tasks, use its id (like T2)" if loose
                  else f"I could not find a task called '{piece.strip()}'")


def parse_task(text: str, people: list[tuple[str, str]], tasks: list[tuple[str, str, str]], today: date) -> ParsedTask:
    """people: [(key, name)]; tasks: [(id, title, aliases)]."""
    out = ParsedTask()
    t = PREFIX.sub("", text or "", count=1)

    m = _EST.search(t)
    if m:
        qty, unit = float(m.group(1)), m.group(2).lower()
        out.est_minutes = max(1, round(qty * 60)) if unit.startswith("h") else max(1, round(qty))
        t = t[:m.start()] + " " + t[m.end():]

    m = _DUE.search(t)
    if m:
        d = parse_date(m.group(1), today)
        if d is None:
            out.errors.append(f"I did not understand the date '{m.group(1)}'")
        elif d < today:
            out.errors.append(f"{d.isoformat()} is in the past")
        else:
            out.due = d.isoformat()
        t = t[:m.start()] + " " + t[m.end():]
    else:
        bad = _DUE_ANY.search(t)
        if bad:                                              # an explicit "due ..." we could not read: say so
            out.errors.append(f"I did not understand the date '{bad.group(1).strip()}'")
            t = t[:bad.start()] + " " + t[bad.end():]

    m = _OWNER.search(t)
    if m:
        key = _resolve_person(m.group(1), people)
        if key is None:
            out.errors.append(f"I do not know @{m.group(1)}. People: {', '.join(n for _, n in people)}")
        out.owner_key = key
        t = t[:m.start()] + " " + t[m.end():]

    m = _DEPS.search(t)
    if m:
        for piece in re.split(r",|&|\band\b", t[m.end():]):
            tid, err = _resolve_task(piece, tasks)
            if err:
                out.errors.append(err)
            elif tid and tid not in out.depends_on:
                out.depends_on.append(tid)
        t = t[:m.start()]

    out.title = re.sub(r"\s+", " ", t).strip(" \t\r\n.,;:-")
    if len(out.title) < 3:
        out.errors.append("the task needs a title")
    return out
