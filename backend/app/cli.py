"""Operational commands, run as one-off ECS tasks or locally.

    python -m app.cli seed        load data/team.csv + data/tasks.csv if the DB is empty
    python -m app.cli checkin     send check-ins now (e.g. from an EventBridge schedule)
    python -m app.cli doctor           check every integration live (Slack, Telegram, Neo4j, models) and say how to fix what is wrong
    python -m app.cli telegram-check   verify the bot token, privacy mode, group chat and who has linked
    python -m app.cli neo4j-check      verify the Neo4j connection (local Docker or Aura) and count the nodes
    python -m app.cli slack-roster [--write]   list the Slack workspace's people as team.csv rows (--write saves it)
"""
import asyncio
import re
import sys

from sqlmodel import Session

from . import channels, runtime, service
from .db import engine, seed_if_empty
from .telemetry import setup_logging


async def _checkin():
    await runtime.start()
    with Session(engine) as s:
        outbox = service.followups(s, force=True)
        await channels.deliver(s, outbox)
        print(f"sent {len(outbox)} check-ins")
    await runtime.stop()


async def _telegram_check() -> int:
    """Talk to the real Telegram API and report whether the bot can run the whole loop. Never prints the token."""
    from sqlmodel import select
    from telegram import Bot

    from .config import settings
    from .models import Member
    if not settings.telegram_enabled:
        print("FAIL  TELEGRAM_BOT_TOKEN is not set in backend/.env")
        return 1
    ok = True
    async with Bot(settings.telegram_token) as bot:
        try:
            me = await bot.get_me()
        except Exception as e:
            print(f"FAIL  Telegram rejected the token: {type(e).__name__}: {e}")
            return 1
        print(f"ok    bot is @{me.username} ({me.first_name})")
        hook = await bot.get_webhook_info()
        if hook.url:
            ok = False
            print(f"FAIL  a webhook is set ({hook.url}); long polling cannot run until it is removed")
        else:
            print("ok    no webhook, long polling is free to run")
        if me.can_read_all_group_messages:
            print("ok    privacy mode is OFF: the bot sees ordinary group messages")
        else:
            print("warn  privacy mode is ON: in groups the bot only sees /commands, @mentions and replies to it. "
                  "Replies to the agent's messages still work; for plain messages use BotFather > /setprivacy > Disable")
    with Session(engine) as s:
        chat = channels.group_chat_id(s)
        print(f"ok    project group chat {chat}" if chat else
              "warn  no project group yet: add the bot to a group and have a linked member write there "
              "(or set TELEGRAM_GROUP_CHAT_ID)")
        members = s.exec(select(Member)).all()
        for m in members:
            print(f"{'ok  ' if m.tg_chat_id else 'warn'}  {m.name:<8} {'linked to Telegram' if m.tg_chat_id else 'not linked: open t.me/' + me.username + '?start=' + m.key}")
    return 0 if ok else 1


def roster_rows(members: list[dict]) -> list[dict]:
    """Slack users -> team.csv rows. Bots, deactivated accounts and Slackbot are skipped. The workspace owner leads
    (the first person if there is no owner); everything is meant to be reviewed and edited by hand."""
    people = [m for m in members if not m.get("is_bot") and not m.get("deleted") and m.get("id") != "USLACKBOT"]
    rows, used = [], set()
    for m in people:
        profile = m.get("profile") or {}
        full = (m.get("real_name") or profile.get("display_name") or m.get("name") or "").strip()
        first = (full.split() or ["member"])[0]
        key = re.sub(r"[^a-z0-9]", "", first.lower()) or "member"
        while key in used:
            key += "2"
        used.add(key)
        rows.append({"key": key, "name": first[:1].upper() + first[1:], "email": (profile.get("email") or "").lower(),
                     "role": "lead" if m.get("is_owner") else "member"})
    leads = [r for r in rows if r["role"] == "lead"]
    if rows and not leads:
        rows[0]["role"] = "lead"
    for extra in leads[1:]:
        extra["role"] = "member"
    return rows


async def _slack_roster(write: bool) -> int:
    from slack_sdk.web.async_client import AsyncWebClient

    from .config import settings
    if not settings.slack_enabled:
        print("FAIL  SLACK_BOT_TOKEN is not set in backend/.env")
        return 1
    rows = roster_rows((await AsyncWebClient(token=settings.slack_bot_token).users_list(limit=500))["members"])
    lines = ["key,name,email,role"] + [f"{r['key']},{r['name']},{r['email']},{r['role']}" for r in rows]
    print("\n".join(lines))
    missing = [r["name"] for r in rows if not r["email"]]
    if missing:
        print(f"warn  no email for {', '.join(missing)}: they will be asked to pick their name in a DM instead of linking automatically")
    if write:
        (settings.data_dir / "team.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {settings.data_dir / 'team.csv'} (tasks.csv still needs its owner column to use these keys)")
    return 0


def _neo4j_check() -> int:
    from .config import settings
    if not settings.neo4j_uri:
        print("FAIL  NEO4J_URI is not set in backend/.env (Aura: neo4j+s://<id>.databases.neo4j.io)")
        return 1
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
    try:
        driver.verify_connectivity()
        with (driver.session(database=settings.neo4j_database) if settings.neo4j_database else driver.session()) as s:
            ver = s.run("CALL dbms.components() YIELD name, versions RETURN name, versions[0] AS v").single()
            print(f"ok    connected to {settings.neo4j_uri} ({ver['name']} {ver['v']})")
            counts = {r["l"]: r["n"] for r in s.run("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS n")}
            print("ok    nodes:", counts or "none yet (they appear after the next plan or task change)")
        return 0
    except Exception as e:
        print(f"FAIL  {type(e).__name__}: {str(e)[:200]}")
        return 1
    finally:
        driver.close()


def main(argv: list[str]) -> int:
    setup_logging()
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "doctor":
        from .doctor import run
        return run()
    if cmd == "neo4j-check":
        return _neo4j_check()
    if cmd == "slack-roster":
        return asyncio.run(_slack_roster("--write" in argv))
    if cmd == "telegram-check":
        return asyncio.run(_telegram_check())
    if cmd == "seed":
        print("seeded" if seed_if_empty() else "already seeded, nothing to do")
        return 0
    if cmd == "checkin":
        asyncio.run(_checkin())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
