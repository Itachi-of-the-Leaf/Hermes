"""`python -m app.cli doctor`: is everything connected and working? Checks every integration live and says how to fix it.

Read-only: it never sends a chat message and never changes data. It reads the database, calls each service's
identity endpoint (Slack auth.test, Telegram getMe, Neo4j connectivity) and asks the running backend for its health.
"""
import asyncio
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from .config import settings
from .db import engine
from .models import Member, Setting, Task

BACKEND = "http://localhost:8000/api"
SLACK_REQUIRED = {"chat:write", "im:history", "im:read", "im:write", "channels:history", "groups:history",
                  "users:read", "users:read.email", "commands"}
SLACK_OPTIONAL = {"channels:read", "groups:read"}          # only to write #channel-names instead of IDs in .env
_SLACK_ID = re.compile(r"^[CG][A-Z0-9]{8,}$")


@dataclass
class Report:
    lines: list[tuple[str, str]] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    def section(self, title: str) -> None:
        self.lines.append(("", f"\n{title}"))

    def ok(self, text: str, fix: str | None = None) -> None:          # `fix` is accepted so ok/warn can be chosen with one expression
        self.lines.append(("ok  ", text))

    def warn(self, text: str, fix: str | None = None) -> None:
        self.lines.append(("warn", text))
        if fix:
            self.fixes.append(fix)

    def fail(self, text: str, fix: str) -> None:
        self.lines.append(("FAIL", text))
        self.fixes.append(fix)

    @property
    def failed(self) -> bool:
        return any(level == "FAIL" for level, _ in self.lines)

    def render(self) -> str:
        out = [f"{lvl}  {txt}" if lvl else txt for lvl, txt in self.lines]
        n_fail = sum(1 for lvl, _ in self.lines if lvl == "FAIL")
        n_warn = sum(1 for lvl, _ in self.lines if lvl == "warn")
        out.append(f"\n{'=' * 60}\n{n_fail} problem(s), {n_warn} warning(s)")
        if self.fixes:
            out.append("\nTo fix:")
            out += [f"  {i}. {f}" for i, f in enumerate(dict.fromkeys(self.fixes), 1)]
        else:
            out.append("Everything that can be checked from here is connected and working.")
        return "\n".join(out)


def missing_scopes(granted: set[str]) -> tuple[set[str], set[str]]:
    """-> (required scopes that are missing, optional ones that are missing)."""
    return SLACK_REQUIRED - granted, SLACK_OPTIONAL - granted


def ago(iso: str | None) -> str:
    if not iso:
        return "never"
    secs = int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds())
    return f"{secs}s ago" if secs < 90 else f"{secs // 60} min ago" if secs < 5400 else f"{secs // 3600} h ago"


def check_backend(r: Report) -> dict:
    r.section("Backend")
    try:
        h = httpx.get(BACKEND + "/health", timeout=10).json()
        ready = httpx.get(BACKEND + "/ready", timeout=10)
    except Exception as e:
        r.fail(f"the backend is not answering on {BACKEND} ({type(e).__name__})", "Start it: cd backend && uvicorn app.main:app --port 8000")
        return {}
    r.ok(f"backend is up (role {h['role']}, env {h['env']}) and its database/queue are {'ready' if ready.status_code == 200 else 'NOT ready'}")
    flags = {k: h.get(k) for k in ("telegram", "slack", "jev", "llm", "neo4j", "recall")}
    for name, on in flags.items():
        (r.ok if on else r.warn)(f"{name} is {'connected' if on else 'not connected'}" + ("" if on else " (optional unless you use it)"))
    return h


def check_slack(r: Report, health: dict, members: list[Member]) -> None:
    r.section("Slack")
    if not settings.slack_bot_token:
        r.fail("SLACK_BOT_TOKEN is not set", "Slack app > OAuth & Permissions > Install to Workspace, copy the xoxb- token into backend/.env")
        return
    from slack_sdk import WebClient
    try:
        c = WebClient(token=settings.slack_bot_token)
        auth = c.auth_test()
        granted = set((auth.headers.get("x-oauth-scopes") or "").replace(" ", "").split(","))
    except Exception as e:
        r.fail(f"Slack rejected the bot token ({type(e).__name__})", "Reinstall the Slack app and copy the new Bot User OAuth Token into backend/.env")
        return
    r.ok(f"bot @{auth['user']} in workspace '{auth['team']}'")
    need, opt = missing_scopes(granted)
    if need:
        r.fail(f"missing required scopes: {', '.join(sorted(need))}", "Slack app > OAuth & Permissions > add the missing Bot Token Scopes, then reinstall")
    else:
        r.ok("all required scopes granted")
    if opt:
        r.warn(f"optional scopes not granted: {', '.join(sorted(opt))} (needed only to use #channel-names in .env)",
               "Add channels:read and groups:read and reinstall, or put channel IDs in .env")
    if not settings.slack_signing_secret:
        r.fail("SLACK_SIGNING_SECRET is not set", "Slack app > Basic Information > Signing Secret into backend/.env")
    else:
        body = '{"type":"url_verification","challenge":"doctor"}'
        stamp = str(int(time.time()))
        sig = "v0=" + hmac.new(settings.slack_signing_secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
        try:
            url = settings.public_base_url.rstrip("/") + "/api/slack/events"
            resp = httpx.post(url, content=body, timeout=15, headers={"Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp,
                                                                       "X-Slack-Signature": sig, "ngrok-skip-browser-warning": "1"})
            if resp.status_code == 200 and "doctor" in resp.text:
                r.ok(f"the public events URL answers Slack's handshake ({settings.public_base_url})")
            else:
                r.fail(f"the public events URL answered HTTP {resp.status_code}", "Check SLACK_SIGNING_SECRET and that PUBLIC_BASE_URL points at this backend")
        except Exception as e:
            r.fail(f"the public events URL is not reachable ({type(e).__name__})", f"Start your tunnel (ngrok http 8000) and set PUBLIC_BASE_URL; Slack cannot reach {settings.public_base_url}")
    real = health.get("slack_last_real_event")
    if real:
        r.ok(f"Slack itself last sent an event {ago(real)}: Event Subscriptions are configured")
    else:
        r.warn("Slack has not sent this backend any event since it started",
               "Slack app > Event Subscriptions: Request URL = <PUBLIC_BASE_URL>/api/slack/events (wait for Verified), bot events message.im, "
               "message.channels, message.groups; also Interactivity and the /coordinator slash command; then DM the bot once")
    for label, val in (("project channel", settings.slack_project_channel), ("summaries channel", settings.slack_summary_channel), ("alerts channel", settings.slack_alert_channel)):
        if not val:
            r.warn(f"{label}: not set", f"Set SLACK_{label.split()[0].upper().replace('SUMMARIES', 'SUMMARY').replace('ALERTS', 'ALERT')}_CHANNEL in backend/.env and /invite the bot to it")
        elif _SLACK_ID.match(val) or val.startswith("#"):
            r.ok(f"{label}: {val}")
        else:
            r.warn(f"{label}: '{val}' is neither a channel ID nor #name", "Use the channel ID (C...) or #name")


def check_telegram(r: Report, health: dict, session: Session) -> None:
    r.section("Telegram")
    if not settings.telegram_token:
        r.fail("TELEGRAM_BOT_TOKEN is not set", "Create a bot with @BotFather and put its token in backend/.env")
        return
    from telegram import Bot

    async def go():
        async with Bot(settings.telegram_token) as bot:
            return await bot.get_me(), await bot.get_webhook_info()
    try:
        me, hook = asyncio.run(go())
    except Exception as e:
        r.fail(f"Telegram rejected the token ({type(e).__name__})", "Get a fresh token from @BotFather (/token) and put it in backend/.env")
        return
    r.ok(f"bot @{me.username}")
    if hook.url:
        r.fail(f"a webhook is set ({hook.url}); polling cannot run", "Remove it: https://api.telegram.org/bot<token>/deleteWebhook")
    else:
        r.ok("no webhook, long polling can run")
    if me.can_read_all_group_messages:
        r.ok("privacy mode is OFF: the bot sees ordinary group messages")
    else:
        r.warn("privacy mode is ON: in groups the bot sees only /commands, @mentions and replies to it", "BotFather > /setprivacy > choose the bot > Disable")
    row = session.get(Setting, "tg_group_chat_id")
    chat = settings.telegram_group_chat_id or (row.value if row else "")
    if chat:
        r.ok(f"team group chat: {chat}")
    else:
        r.warn("no team group yet", "Create a Telegram group and add the bot (ideally as an admin): it remembers the group and posts instructions. "
               "Then everyone taps their name there (or sends /join). No private chats needed.")
    last = health.get("telegram_last_update")
    (r.ok if last else r.warn)(f"Telegram last reached the backend {ago(last)}" if last else "Telegram has not reached the backend since it started",
                              None if last else "Message the bot (/start <your key>) or write in the group once")


def reachable(m: Member, group: bool) -> bool:
    """A private chat on either app, or - when a Telegram group is set - having identified themselves in it (they can be @mentioned there)."""
    return bool(m.slack_dm_id or m.tg_chat_id or (group and m.tg_user_id))


def check_people(r: Report, members: list[Member], group: bool = False) -> None:
    r.section("People (who the agent can reach)")
    if not members:
        r.fail("there are no team members", "Fill backend/data/team.csv (python -m app.cli slack-roster --write) and press Reset")
        return
    r.lines.append(("", f"  {'name':10} {'role':7} {'Slack':7} {'Telegram':9}"))
    for m in members:
        r.lines.append(("", f"  {m.name:10} {m.role:7} {'linked' if m.slack_dm_id else '-':7} {'linked' if m.tg_chat_id else 'in group' if m.tg_user_id else '-':9}"))
    leads = [m for m in members if m.role == "lead"]
    if len(leads) != 1:
        r.fail(f"there must be exactly one lead, found {len(leads)}", "Set exactly one role=lead in backend/data/team.csv and press Reset")
    elif not reachable(leads[0], group):
        r.fail(f"the lead ({leads[0].name}) is not connected to any app, so 'Needs your call' reviews and escalations cannot reach anyone",
               f"{leads[0].name}: DM the Slack bot once, or press Start on the Telegram link, so the lead is reachable")
    else:
        r.ok(f"the lead ({leads[0].name}) is reachable, so reviews and escalations are delivered")
    unlinked = [m.name for m in members if not reachable(m, group)]
    if unlinked:
        r.warn(f"not connected to any app: {', '.join(unlinked)} (requests addressed to them are recorded as 'unreachable')",
               "Each person taps their name in the Telegram group (/join) or DMs the Slack bot once (matched by email)")


def check_graph(r: Report, session: Session, members: list[Member]) -> None:
    r.section("Graph database (Neo4j)")
    if not settings.neo4j_uri:
        r.warn("NEO4J_URI is not set: the Graph page falls back to the project database", "Set NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD (local Docker or Aura) in backend/.env")
        return
    try:
        from neo4j import GraphDatabase
        with GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)) as d:
            d.verify_connectivity()
            with (d.session(database=settings.neo4j_database) if settings.neo4j_database else d.session()) as s:
                counts = {x["l"]: x["n"] for x in s.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS n")}
    except Exception as e:
        r.fail(f"cannot connect to {settings.neo4j_uri} ({type(e).__name__}: {str(e).splitlines()[0][:90]})",
               "Check NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD and NEO4J_DATABASE in backend/.env (Aura: use the values in its downloaded credentials file)")
        return
    r.ok(f"connected to {settings.neo4j_uri}")
    tasks = len(session.exec(select(Task)).all())
    same = counts.get("Task", 0) == tasks and counts.get("Person", 0) == len(members)
    (r.ok if same else r.warn)(f"graph holds {counts.get('Task', 0)} tasks and {counts.get('Person', 0)} people; the project has {tasks} tasks and {len(members)} people"
                              + ("" if same else " (the mirror is behind)"),
                              None if same else "Change a task or press Reset; the mirror re-syncs after every change")


def check_models(r: Report, health: dict) -> None:
    r.section("Understanding replies and meetings")
    (r.ok if settings.jev_enabled else r.warn)(f"Jev: {'on' if settings.jev_enabled else 'off (rules decide)'}", None if settings.jev_enabled else "Set OPENROUTER_API_KEY")
    (r.ok if settings.llm_enabled else r.warn)(f"LLM: {settings.llm_model if settings.llm_enabled else 'off'}", None if settings.llm_enabled else "Set LLM_API_KEY and LLM_MODEL")
    if settings.recall_enabled:
        try:
            ok = httpx.get(f"https://{settings.recall_region}.recall.ai/api/v1/bot/", params={"page_size": 1}, timeout=15,
                           headers={"Authorization": f"Token {settings.recall_api_key}"}).status_code == 200
        except Exception:
            ok = False
        if ok:
            r.ok(f"Recall meeting agent: key works in region {settings.recall_region}")
        else:
            r.fail(f"Recall rejected the key in region {settings.recall_region}", "A Recall key only works in its own region: RECALL_REGION in backend/.env")
    else:
        r.warn("Recall meeting agent: off (uploading a transcript still works)", "Set RECALL_API_KEY, RECALL_REGION, PUBLIC_BASE_URL")


def run() -> int:
    r = Report()
    health = check_backend(r)
    with Session(engine) as s:
        members = list(s.exec(select(Member)).all())
        check_slack(r, health, members)
        check_telegram(r, health, s)
        check_people(r, members, group=bool(settings.telegram_group_chat_id or s.get(Setting, "tg_group_chat_id")))
        check_graph(r, s, members)
    check_models(r, health)
    print(r.render())
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(run())

