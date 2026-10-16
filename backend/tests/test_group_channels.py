"""The agent speaks in the Telegram group AND the Slack project channel; replies from either bind to the same action.
Also the network endpoint behind the Graph page."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import channels
from app.channels import to_slack
from app.config import settings
from app.db import engine as db_engine
from app.intel import engine, responses
from app.intel import routes as intel_routes
from app.main import app
from app.models import AgentAction, Member, Message, Setting, Task

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SLACK_CH = "C0PROJECT01"


class FakeTG:
    name = "telegram"

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def ready(self):
        return True

    def linked(self, m):
        return False

    async def send(self, m, item):
        return None

    async def send_group(self, chat_id, html):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, html))
        return str(5000 + len(self.sent))


class FakeSL:
    name = "slack"

    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def ready(self):
        return True

    def linked(self, m):
        return False

    async def send(self, m, item):
        return None

    async def post(self, channel, text, blocks, thread_ts=None):
        if self.fail:
            raise RuntimeError("slack down")
        self.posts.append((channel, text, blocks))
        return f"1700000000.{len(self.posts):06d}"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def world():
    with TestClient(app) as c:
        c.post("/api/reset")
        with Session(db_engine) as s:
            for m in s.exec(select(Member)).all():
                m.tg_user_id = m.tg_chat_id = m.slack_user_id = m.slack_dm_id = m.preferred_channel = None
                if m.key == "priya":
                    m.tg_user_id, m.slack_user_id = 777, "U-PRIYA"
                s.add(m)
            for t in s.exec(select(Task)).all():
                t.deadline = (NOW + timedelta(hours=12)).isoformat()
                s.add(t)
            for row in s.exec(select(Setting)).all():
                s.delete(row)
            s.commit()
        yield c


@pytest.fixture()
def both(world, monkeypatch):
    tg, sl = FakeTG(), FakeSL()
    monkeypatch.setitem(channels.REGISTRY, "telegram", tg)
    monkeypatch.setitem(channels.REGISTRY, "slack", sl)
    monkeypatch.setattr(settings, "slack_project_channel", SLACK_CH)
    with Session(db_engine) as s:
        s.add(Setting(key="tg_group_chat_id", value="-100123"))
        s.commit()
    return tg, sl


def pid(key):
    with Session(db_engine) as s:
        return s.exec(select(Member).where(Member.key == key)).first().id


def post(text="{M%d} hello <b>team</b> & friends"):
    with Session(db_engine) as s:
        return run(channels.deliver_group(s, text % pid("priya") if "%d" in text else text, task_id="T2", recipient_ids=[pid("priya")]))


# ================================================================ posting

def test_one_message_goes_to_both_chats_with_native_mentions(both):
    tg, sl = both
    ext, channel = post()
    assert channel == "telegram+slack" and ext == "5001;1700000000.000001"
    assert tg.sent[0][0] == -100123 and '<a href="tg://user?id=777">Priya</a>' in tg.sent[0][1] and "<b>team</b>" in tg.sent[0][1]
    chan, text, blocks = sl.posts[0]
    assert chan == SLACK_CH and text.startswith("<@U-PRIYA> hello *team* &amp; friends") and blocks is None


def test_slack_text_is_escaped_before_mentions_are_added():
    assert to_slack("a <b>b</b> & c < d {M1}") == "a *b* &amp; c &lt; d {M1}"
    people = {1: type("M", (), {"name": "Ann", "slack_user_id": "U1", "tg_user_id": None})()}
    assert channels.expand_mentions(to_slack("hi {M1} & co"), people, "slack") == "hi <@U1> &amp; co"       # the mention is not escaped


def test_without_a_slack_account_the_mention_falls_back_to_bold(world):
    people = {1: type("M", (), {"name": "Ann <x>", "slack_user_id": None, "tg_user_id": None})()}
    assert channels.expand_mentions("{M1}", people, "slack") == "*@Ann <x>*"


def test_only_the_slack_project_channel_when_there_is_no_telegram_group(world, monkeypatch):
    sl = FakeSL()
    monkeypatch.setitem(channels.REGISTRY, "slack", sl)
    monkeypatch.setattr(settings, "slack_project_channel", SLACK_CH)
    ext, channel = post()
    assert channel == "slack" and ext == "1700000000.000001" and len(sl.posts) == 1


def test_a_channel_name_that_was_not_resolved_to_an_id_is_not_used(world, monkeypatch):
    sl = FakeSL()
    monkeypatch.setitem(channels.REGISTRY, "slack", sl)
    monkeypatch.setattr(settings, "slack_project_channel", "#proj-demo")
    ext, channel = post()
    assert sl.posts == [] and channel == "unreachable"                      # falls back to the person, who has no app linked


def test_a_dm_id_is_never_used_as_the_group_channel(world, monkeypatch):
    sl = FakeSL()
    monkeypatch.setitem(channels.REGISTRY, "slack", sl)
    monkeypatch.setattr(settings, "slack_project_channel", "D0DIRECT001")
    post()
    assert sl.posts == []


def test_if_one_chat_is_down_the_other_still_gets_it(world, monkeypatch):
    sl = FakeSL()
    monkeypatch.setitem(channels.REGISTRY, "telegram", FakeTG(fail=True))
    monkeypatch.setitem(channels.REGISTRY, "slack", sl)
    monkeypatch.setattr(settings, "slack_project_channel", SLACK_CH)
    with Session(db_engine) as s:
        s.add(Setting(key="tg_group_chat_id", value="-100123"))
        s.commit()
    ext, channel = post()
    assert channel == "slack" and ext == "1700000000.000001"


def test_if_every_chat_is_down_it_is_a_failure_to_retry(world, monkeypatch):
    monkeypatch.setitem(channels.REGISTRY, "telegram", FakeTG(fail=True))
    monkeypatch.setitem(channels.REGISTRY, "slack", FakeSL(fail=True))
    monkeypatch.setattr(settings, "slack_project_channel", SLACK_CH)
    with Session(db_engine) as s:
        s.add(Setting(key="tg_group_chat_id", value="-100123"))
        s.commit()
    assert post() == (None, "failed")


def test_the_group_post_is_recorded_once_as_plain_text(both):
    post()
    with Session(db_engine) as s:
        rows = s.exec(select(Message).where(Message.kind == "agent_action")).all()
    assert len(rows) == 1 and rows[0].channel == "telegram+slack" and "@Priya hello" in rows[0].text and "<" not in rows[0].text


# ================================================================ the whole loop with both chats

def block_kabir(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "stuck, waiting for test credentials"})


def test_the_agent_posts_to_both_chats_and_records_one_action(both, world):
    tg, sl = both
    block_kabir(world)
    report = run(engine.run_once(now=NOW))
    (a,) = report["actions"]
    assert a["channel"] == "telegram+slack" and len(tg.sent) == 1 and len(sl.posts) == 1
    with Session(db_engine) as s:
        rows = s.exec(select(AgentAction)).all()
    assert len(rows) == 1 and rows[0].ext_id == "5001;1700000000.000001" and rows[0].channel == "telegram+slack"
    assert "Priya" in tg.sent[0][1] and "<@U-PRIYA>" in sl.posts[0][1]                    # each chat gets its own kind of mention
    assert world.get("/api/intel/actions").json()[0]["text"].startswith("@Priya")


def reply(key, text, reply_to=None, at=NOW):
    with Session(db_engine) as s:
        me = s.exec(select(Member).where(Member.key == key)).first()
        return run(responses.try_handle(s, me, text, reply_to=reply_to, now=at))


@pytest.mark.parametrize("reply_to", ["5001", "1700000000.000001"])
def test_a_reply_in_either_chat_binds_to_the_same_action(both, world, reply_to):
    block_kabir(world)
    run(engine.run_once(now=NOW))
    assert reply("priya", "in 20 minutes", reply_to=reply_to) == []
    with Session(db_engine) as s:
        (a,) = s.exec(select(AgentAction)).all()
    assert a.status == "committed" and a.commitment_at == NOW + timedelta(minutes=20)


def test_a_reply_to_some_other_message_does_not_bind(both, world):
    block_kabir(world)
    run(engine.run_once(now=NOW))
    for other in ("50", "5002", "1700000000.0000011", "999", "1700000000"):
        assert reply("priya", "in 20 minutes", reply_to=other) is None


def test_the_acknowledgement_also_goes_to_both_chats(both, world):
    tg, sl = both
    block_kabir(world)
    run(engine.run_once(now=NOW))
    reply("priya", "in 20 minutes", reply_to="5001")
    assert len(tg.sent) == 2 and len(sl.posts) == 2 and "noted" in sl.posts[1][1] and "noted" in tg.sent[1][1]


# ================================================================ the network (Graph page) endpoint

def test_the_database_fallback_has_every_node_and_relationship(world):
    n = world.get("/api/intel/network").json()
    assert n["source"] == "database" and n["browser"] is None
    types = {}
    for node in n["nodes"]:
        types.setdefault(node["type"], []).append(node["id"])
    assert types["Project"] == ["Project:main"] and len(types["Person"]) == 4 and len(types["Task"]) == 5
    rel = {}
    for e in n["edges"]:
        rel.setdefault(e["type"], []).append((e["source"], e["target"]))
    assert len(rel["HAS_TASK"]) == 5 and len(rel["OWNS"]) == 5
    assert ("Task:T2", "Task:T3") in rel["DEPENDS_ON"] and ("Task:T4", "Task:T2") in rel["DEPENDS_ON"]      # (dependent)->(prerequisite)
    ids = {x["id"] for x in n["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in n["edges"])                               # no dangling edges


def test_nodes_carry_what_the_page_shows(world):
    world.post("/api/dev/message", json={"member_key": "kabir", "text": "stuck, waiting for test credentials"})
    n = {x["id"]: x for x in world.get("/api/intel/network").json()["nodes"]}
    assert n["Task:T2"]["status"] == "Blocked" and "credentials" in n["Task:T2"]["blocker"]
    assert n["Person:" + str(pid("aanya"))]["role"] == "lead"


def test_the_endpoint_prefers_neo4j_when_it_is_configured(world, monkeypatch):
    from app.intel import neo4j_mirror
    monkeypatch.setattr(settings, "neo4j_uri", "bolt://localhost:7687")
    monkeypatch.setattr(neo4j_mirror, "read_network", lambda: {"nodes": [{"id": "Task:X", "type": "Task", "name": "From Neo4j"}], "edges": []})
    n = world.get("/api/intel/network").json()
    assert n["source"] == "neo4j" and n["nodes"][0]["name"] == "From Neo4j" and n["browser"] == "http://localhost:7474"


def test_the_endpoint_falls_back_to_the_database_if_neo4j_is_down(world, monkeypatch):
    from app.intel import neo4j_mirror
    monkeypatch.setattr(settings, "neo4j_uri", "neo4j+s://abc123.databases.neo4j.io")

    def boom():
        raise ConnectionError("cannot connect")
    monkeypatch.setattr(neo4j_mirror, "read_network", boom)
    n = world.get("/api/intel/network").json()
    assert n["source"] == "database" and "Neo4j unavailable" in n["error"] and len(n["nodes"]) > 5
    assert n["browser"] == "https://console.neo4j.io"


@pytest.mark.parametrize("uri,expected", [("", None), ("bolt://localhost:7687", "http://localhost:7474"),
                                          ("bolt://127.0.0.1:7687", "http://localhost:7474"),
                                          ("neo4j+s://x.databases.neo4j.io", "https://console.neo4j.io")])
def test_browser_links(monkeypatch, uri, expected):
    monkeypatch.setattr(settings, "neo4j_uri", uri)
    assert intel_routes.browser_url() == expected


def test_health_reports_whether_neo4j_and_the_slack_channel_are_set(world, monkeypatch):
    monkeypatch.setattr(settings, "neo4j_uri", "bolt://localhost:7687")
    monkeypatch.setattr(settings, "slack_project_channel", SLACK_CH)
    h = world.get("/api/health").json()
    assert h["neo4j"] is True and h["slack_project_channel"] is True
    monkeypatch.setattr(settings, "neo4j_uri", "")
    assert world.get("/api/health").json()["neo4j"] is False
