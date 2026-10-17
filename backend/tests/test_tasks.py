"""Tasks come from people, not from a seed file: the parser, create/delete, chat commands, the empty project."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import channels, service
from app.config import settings
from app.db import engine as db_engine
from app.main import app
from app.models import AgentAction, Event, Member, Message, Task
from app.taskparse import is_task_command, parse_date, parse_task

TODAY = date(2026, 10, 17)                      # a Saturday
PEOPLE = [("tejas", "Tejas"), ("soham", "Soham"), ("samarth", "Samarth"), ("atharva", "Atharva")]
TASKS = [("T1", "Design the slides", "slides|deck"), ("T2", "Connect the payment API", "payment api|api"),
         ("T3", "Get sandbox test credentials", "creds")]


def parse(text):
    return parse_task(text, PEOPLE, TASKS, TODAY)


# ================================================================ the parser

def test_a_bare_title_is_a_task_for_nobody_in_particular():
    p = parse("task: Build the login page")
    assert p.title == "Build the login page" and p.owner_key is None and p.due is None and p.depends_on == [] and p.errors == []


@pytest.mark.parametrize("text", ["task: Build it now", "/task Build it now", "/task@xdominator_bot Build it now",
                                  "new task Build it now", "add task: Build it now", "TASK: Build it now"])
def test_every_command_spelling_is_recognised(text):
    assert is_task_command(text) and parse(text).title == "Build it now"


@pytest.mark.parametrize("text", ["task is done", "the task: is hard", "tasks: three", "I have a task", "done", "",
                                  "newtask: x", "retask: x"])
def test_ordinary_sentences_are_not_commands(text):
    assert not is_task_command(text)


def test_owner_by_key_or_name_or_unique_prefix():
    assert parse("task: Do it @soham").owner_key == "soham"
    assert parse("task: Do it @Samarth").owner_key == "samarth"
    assert parse("task: Do it @ath").owner_key == "atharva"
    assert parse("task: @tejas Do it").title == "Do it"


def test_an_unknown_or_ambiguous_owner_is_an_error_not_a_guess():
    p = parse("task: Do it @zoe")
    assert p.owner_key is None and any("do not know @zoe" in e and "Tejas" in e for e in p.errors)
    two = parse_task("task: Do it @sa", [("sam", "Sam"), ("sara", "Sara")], [], TODAY)
    assert two.owner_key is None and two.errors


@pytest.mark.parametrize("text,minutes", [("est 90m", 90), ("est 3h", 180), ("est 1.5 hours", 90), ("est 45 mins", 45), ("EST 2 hrs", 120)])
def test_estimates(text, minutes):
    assert parse(f"task: Do it {text}").est_minutes == minutes and parse(f"task: Do it {text}").title == "Do it"


def test_no_estimate_stays_unknown_never_zero():
    assert parse("task: Do it").est_minutes is None


@pytest.mark.parametrize("expr,expected", [
    ("2026-10-25", "2026-10-25"), ("today", "2026-10-17"), ("tomorrow", "2026-10-18"), ("friday", "2026-10-23"),
    ("fri", "2026-10-23"), ("next monday", "2026-10-19"), ("saturday", "2026-10-24"),      # today is Saturday: the next one
    ("25 oct", "2026-10-25"), ("oct 25", "2026-10-25"), ("25th October", "2026-10-25"), ("3 jan", "2027-01-03"),
    ("jan 3, 2028", "2028-01-03"), ("1 nov 2027", "2027-11-01"),
])
def test_dates(expr, expected):
    assert parse_date(expr, TODAY).isoformat() == expected
    assert parse(f"task: Do it due {expr}").due == expected


@pytest.mark.parametrize("expr", ["blah", "2026-13-40", "32 oct", "tomorow-ish", "someday"])
def test_unreadable_dates_are_reported(expr):
    p = parse(f"task: Do it due {expr}")
    assert p.due is None and any("did not understand the date" in e for e in p.errors)


def test_a_date_in_the_past_is_rejected():
    p = parse("task: Do it due 2026-10-01")
    assert p.due is None and any("in the past" in e for e in p.errors)


def test_by_works_for_real_dates_but_not_for_ordinary_words():
    assert parse("task: Do it by friday").due == "2026-10-23"
    q = parse("task: Review the work done by the platform team")
    assert q.due is None and q.title == "Review the work done by the platform team" and q.errors == []


def test_prerequisites_by_id_and_by_title():
    assert parse("task: Test it after T2").depends_on == ["T2"]
    assert parse("task: Test it after T1, T3").depends_on == ["T1", "T3"]
    assert parse("task: Test it depends on Connect the payment API").depends_on == ["T2"]
    assert parse("task: Test it after the payment api and creds").depends_on == ["T2", "T3"]


def test_unknown_prerequisites_are_errors():
    assert any("no task T9" in e for e in parse("task: Test it after T9").errors)
    assert any("could not find a task called 'nothing like this'" in e for e in parse("task: Test it after nothing like this").errors)


def test_everything_together_in_any_order():
    a = parse("task: Build the login page @soham due friday est 3h after T1")
    b = parse("task: Build the login page after T1 est 3h due friday @soham")
    for p in (a, b):
        assert (p.title, p.owner_key, p.due, p.est_minutes, p.depends_on, p.errors) == \
            ("Build the login page", "soham", "2026-10-23", 180, ["T1"], [])


def test_a_missing_title_is_an_error():
    assert any("needs a title" in e for e in parse("task: @soham due friday").errors)
    assert any("needs a title" in e for e in parse("task:").errors)
    assert any("needs a title" in e for e in parse("/task ab").errors)


def test_several_problems_are_all_reported_at_once():
    p = parse("task: Do it @zoe due blah after T9")
    assert len(p.errors) == 3


# ================================================================ creating and deleting

@pytest.fixture()
def world():
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def key_id(key):
    with Session(db_engine) as s:
        return s.exec(select(Member).where(Member.key == key)).first().id


def new_task(s, **kw):
    kw.setdefault("owner_id", key_id("kabir"))
    return service.create_task(s, title=kw.pop("title", "A brand new task"), **kw)


def test_ids_continue_after_the_highest_existing_one(world):
    with Session(db_engine) as s:
        top = max(int(t.id[1:]) for t in s.exec(select(Task)).all())
        assert new_task(s).id == f"T{top + 1}" and new_task(s).id == f"T{top + 2}"


def test_a_new_task_starts_pending_with_findable_aliases(world):
    with Session(db_engine) as s:
        t = new_task(s, title="  Build   the login page ", deadline="2026-10-25", est_minutes=120, depends_on=["T3", "T3"])
        assert (t.title, t.status, t.deadline, t.est_minutes, t.depends_on, t.aliases) == \
            ("Build the login page", "Pending", "2026-10-25", 120, "T3", "build the login page")
        assert service.match_task(s, "the login page is going fine", [t]) is t     # replies can be matched to it


@pytest.mark.parametrize("kw,msg", [
    ({"title": "ab"}, "title"), ({"title": ""}, "title"), ({"owner_id": 9999}, "owner"), ({"depends_on": ["T999"]}, "prerequisite"),
    ({"deadline": "friday"}, "deadline"), ({"est_minutes": 0}, "estimate"), ({"est_minutes": -5}, "estimate"),
])
def test_bad_input_is_refused(world, kw, msg):
    with Session(db_engine) as s, pytest.raises(ValueError, match=msg):
        new_task(s, **kw)


def test_deleting_a_task_unhooks_everything_that_pointed_at_it(world):
    with Session(db_engine) as s:
        a = new_task(s, title="First thing")
        b = new_task(s, title="Second thing", depends_on=[a.id])
        s.add(Message(task_id=a.id, direction="in", text="hi"))
        s.add(Event(task_id=a.id, old_status="Pending", new_status="Blocked", reason="r", engine="rules"))
        s.add(AgentAction(type="REQUEST_ETA", risk_type="CONFIRMED_BLOCKER", task_id=a.id, recipient_id=key_id("kabir"), dedupe_key="k"))
        s.commit()
        assert service.delete_task(s, a.id) is True
        s.refresh(b)
        assert b.depends_on == "" and s.get(Task, a.id) is None
        for model in (Message, Event, AgentAction):
            assert s.exec(select(model).where(model.task_id == a.id)).first() is None


def test_deleting_something_that_is_not_there_is_false(world):
    with Session(db_engine) as s:
        assert service.delete_task(s, "T999") is False


def test_the_board_still_loads_after_deleting_a_task_with_history(world):
    with Session(db_engine) as s:
        a = new_task(s, title="Gone soon")
        s.add(Event(task_id=a.id, old_status="Pending", new_status="Blocked", reason="r", engine="rules"))
        s.commit()
        service.delete_task(s, a.id)
    assert world.get("/api/board").status_code == 200 and world.get("/api/activity").status_code == 200


# ================================================================ the dashboard API

def test_api_create_and_delete(world):
    r = world.post("/api/tasks", json={"title": "Write the report", "owner_key": "meera", "deadline": "2026-10-30",
                                       "est_minutes": 90, "depends_on": ["T3"]})
    assert r.status_code == 200
    tid = r.json()["id"]
    t = next(x for x in world.get("/api/board").json()["tasks"] if x["id"] == tid)
    assert t["owner_key"] == "meera" and t["deadline"] == "2026-10-30" and t["depends_on_titles"] == ["Get sandbox test credentials"]
    assert world.delete(f"/api/tasks/{tid}").status_code == 200
    assert all(x["id"] != tid for x in world.get("/api/board").json()["tasks"])


@pytest.mark.parametrize("body,code", [
    ({"title": "ab", "owner_key": "meera"}, 400), ({"title": "Fine title", "owner_key": "nobody"}, 400),
    ({"title": "Fine title", "owner_key": "meera", "depends_on": ["T999"]}, 400),
    ({"title": "Fine title", "owner_key": "meera", "deadline": "soon"}, 400), ({"owner_key": "meera"}, 422), ({"title": "x"}, 422),
])
def test_api_rejects_bad_tasks(world, body, code):
    assert world.post("/api/tasks", json=body).status_code == code


def test_api_delete_unknown_is_404(world):
    assert world.delete("/api/tasks/T999").status_code == 404


def test_creating_a_task_asks_for_a_replan_and_a_board_refresh(world, monkeypatch):
    from app.intel import engine
    seen = []
    monkeypatch.setattr(engine, "request", lambda kind, tid=None: seen.append((kind, tid)))
    tid = world.post("/api/tasks", json={"title": "Plan the launch", "owner_key": "meera"}).json()["id"]
    world.delete(f"/api/tasks/{tid}")
    assert seen == [("TASK_CREATED", tid), ("TASK_DELETED", tid)]


# ================================================================ creating from chat

def run(coro):
    return asyncio.run(coro)


def dm(key, text):
    with Session(db_engine) as s:
        me = s.exec(select(Member).where(Member.key == key)).first()
        return run(service.handle_dm(s, me, text, channel="telegram"))


def tasks_titled(title):
    with Session(db_engine) as s:
        return [t for t in s.exec(select(Task)).all() if t.title == title]


def test_a_dm_that_starts_with_task_creates_one_owned_by_the_sender(world):
    out = dm("kabir", "task: Prepare the pitch deck due tomorrow est 2h")
    (t,) = tasks_titled("Prepare the pitch deck")
    assert t.owner_id == key_id("kabir") and t.est_minutes == 120 and t.deadline and t.status == "Pending"
    assert len(out) == 1 and "Created" in out[0]["text"] and t.id in out[0]["text"] and out[0]["member_id"] == key_id("kabir")


def test_assigning_to_someone_else_notifies_them_with_buttons(world):
    out = dm("kabir", "task: Prepare the pitch deck @meera")
    (t,) = tasks_titled("Prepare the pitch deck")
    assert t.owner_id == key_id("meera")
    note = next(o for o in out if o["member_id"] == key_id("meera"))
    assert "Kabir gave you a new task" in note["text"] and note["buttons"]                 # she can answer Done / Working / Stuck
    assert out[0]["member_id"] == key_id("kabir") and "owner <b>Meera</b>" in out[0]["text"]


def test_a_task_with_prerequisites_confirms_them_by_title(world):
    out = dm("kabir", "task: Ship it after T3 and T1")
    assert "after Get sandbox test credentials, Design the slides" in out[0]["text"]


def test_a_bad_command_creates_nothing_and_explains(world):
    before = len(tasks_titled("Do it"))
    out = dm("kabir", "task: Do it @zoe due blah after T99")
    assert len(tasks_titled("Do it")) == before == 0
    assert "did not create" in out[0]["text"] and "do not know @zoe" in out[0]["text"] and "task: Build the login page" in out[0]["text"]
    assert len(out) == 1                                                                    # nobody else is bothered


def test_ordinary_replies_still_go_through_the_normal_pipeline(world):
    n = len(world.get("/api/board").json()["tasks"])
    dm("kabir", "task is almost done, just testing")
    assert len(world.get("/api/board").json()["tasks"]) == n


def test_the_stuck_button_answer_is_not_mistaken_for_a_task(world):
    with Session(db_engine) as s:
        me = s.exec(select(Member).where(Member.key == "kabir")).first()
        me.awaiting_blocker_task = "T2"
        s.add(me)
        s.commit()
    dm("kabir", "task: waiting for creds")                                                  # this text IS the blocker
    assert world.get("/api/board").json()["tasks"][1]["status"] == "Blocked"
    assert tasks_titled("waiting for creds") == []


def test_group_messages_can_create_tasks_too(world):
    with Session(db_engine) as s:
        me = s.exec(select(Member).where(Member.key == "kabir")).first()
        out = run(service.handle_group(s, me, "task: Book the venue @priya", channel="telegram"))
    assert tasks_titled("Book the venue")[0].owner_id == key_id("priya") and "Created" in out[0]["text"]


def test_a_new_task_appears_on_the_board_and_is_chased_like_any_other(world):
    dm("kabir", "task: Prepare the pitch deck")
    t = tasks_titled("Prepare the pitch deck")[0]
    assert world.post("/api/checkin", json={"task_id": t.id}).json()["sent"] == 1
    out = dm("kabir", "the pitch deck is done, here is the link https://example.com/deck")
    assert any(x["status"] == "Complete" for x in world.get("/api/board").json()["tasks"] if x["id"] == t.id)
    assert out


# ================================================================ Telegram /task

class Reply:
    def __init__(self):
        self.html = []

    async def reply_html(self, text):
        self.html.append(text)

    async def reply_text(self, text):
        self.html.append(text)


def telegram_update(user_id, text):
    msg = Reply()
    msg.text = text
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=user_id), effective_chat=SimpleNamespace(id=-100, type="supergroup")), msg


def test_telegram_task_command_replies_in_the_chat_it_came_from(world):
    from app.channels import telegram as tg
    with Session(db_engine) as s:
        m = s.exec(select(Member).where(Member.key == "kabir")).first()
        m.tg_user_id, m.tg_chat_id = 4242, 4242
        s.add(m)
        s.commit()
    update, msg = telegram_update(4242, "/task@xdominator_bot Write the demo script @meera due friday")
    run(tg.task_cmd(update, None))
    (t,) = tasks_titled("Write the demo script")
    assert t.owner_id == key_id("meera") and msg.html and "Created" in msg.html[0]


def test_telegram_task_from_someone_who_never_pressed_start_is_refused(world):
    from app.channels import telegram as tg
    update, msg = telegram_update(777777, "/task Do something")
    run(tg.task_cmd(update, None))
    assert tasks_titled("Do something") == [] and "/join" in msg.html[0]


def test_telegram_help_mentions_the_task_command():
    from app.channels import telegram as tg
    update, msg = telegram_update(1, "/help")
    run(tg.help_cmd(update, None))
    assert "/task" in msg.html[0]


# ================================================================ Slack: channel messages and /coordinator task

class FakeSlackApp:
    """Collects the handlers `_register` installs so they can be called directly."""

    def __init__(self):
        self.handlers = {}

    def event(self, name):
        return lambda fn: self.handlers.setdefault(("event", name), fn) or fn

    def action(self, pattern):
        return lambda fn: self.handlers.setdefault(("action", str(pattern)), fn) or fn

    def command(self, name):
        return lambda fn: self.handlers.setdefault(("command", name), fn) or fn


@pytest.fixture()
def slack(world):
    from app.channels import slack as sl
    fake = FakeSlackApp()
    sl._register(fake)
    with Session(db_engine) as s:
        m = s.exec(select(Member).where(Member.key == "kabir")).first()
        m.slack_user_id, m.slack_dm_id = "U-KABIR", "D-KABIR"
        s.add(m)
        s.commit()
    return fake


def test_a_task_message_in_the_slack_channel_is_answered_in_its_thread(slack, monkeypatch):
    monkeypatch.setattr(settings, "slack_project_channel", "")                 # empty: any channel the bot is in
    said = []

    async def say(**kw):
        said.append(kw)

    event = {"user": "U-KABIR", "text": "task: Draft the press release @meera due friday", "channel": "C0PROJECT1",
             "channel_type": "channel", "ts": "1700000000.000100"}
    run(slack.handlers[("event", "message")](event, None, say))
    (t,) = tasks_titled("Draft the press release")
    assert t.owner_id == key_id("meera")
    assert said[0]["thread_ts"] == "1700000000.000100" and "Created" in said[0]["text"] and "*" in said[0]["text"]   # Slack bold, not <b>


def test_task_messages_in_other_channels_are_ignored_when_a_project_channel_is_set(slack, monkeypatch):
    monkeypatch.setattr(settings, "slack_project_channel", "C0PROJECT1")

    async def say(**kw):
        raise AssertionError("must stay silent")

    run(slack.handlers[("event", "message")]({"user": "U-KABIR", "text": "task: Draft the memo", "channel": "C0OTHER0001",
                                              "channel_type": "channel", "ts": "1.1"}, None, say))
    assert tasks_titled("Draft the memo") == []


async def ack():
    return None


def test_the_slash_command_creates_a_task_and_answers_privately(slack):
    replies = []

    async def respond(text):
        replies.append(text)

    run(slack.handlers[("command", "/coordinator")](ack, {"user_id": "U-KABIR", "text": "task Order the stickers @priya est 30m"},
                                                    None, respond))
    (t,) = tasks_titled("Order the stickers")
    assert t.owner_id == key_id("priya") and t.est_minutes == 30 and "Created" in replies[0]


def test_the_slash_command_status_still_works_with_no_arguments(slack):
    replies = []

    async def respond(text):
        replies.append(text)

    run(slack.handlers[("command", "/coordinator")](ack, {"user_id": "U-KABIR", "text": ""}, None, respond))
    assert "Project status" in replies[0]


def test_the_slash_command_from_an_unknown_person_explains_how_to_get_known(slack):
    replies = []

    async def respond(text):
        replies.append(text)

    class Client:
        async def users_info(self, user):
            raise RuntimeError("no")
    run(slack.handlers[("command", "/coordinator")](ack, {"user_id": "U-STRANGER", "text": "task Something useful"}, Client(), respond))
    assert tasks_titled("Something useful") == [] and "send me a direct message" in replies[0]


# ================================================================ an empty project (no pre-set tasks)

@pytest.fixture()
def empty_project(tmp_path, monkeypatch):
    (tmp_path / "team.csv").write_text("key,name,email,role\nada,Ada,ada@x.com,lead\nbo,Bo,bo@x.com,member\n")
    (tmp_path / "tasks.csv").write_text("id,title,owner,status,deadline,depends_on,aliases,est_minutes\n")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c
    monkeypatch.undo()
    with TestClient(app) as c:
        c.post("/api/reset")                                                   # give the other tests the sample team back


def test_an_empty_project_loads_everywhere_without_errors(empty_project):
    c = empty_project
    b = c.get("/api/board").json()
    assert b["tasks"] == [] and [m["name"] for m in b["members"]] == ["Ada", "Bo"] and b["counts"]["Complete"] == 0
    for url in ("/api/activity", "/api/reviews", "/api/team", "/api/intel/forecast", "/api/intel/graph", "/api/intel/actions", "/api/intel/network"):
        assert c.get(url).status_code == 200, url
    assert c.post("/api/checkin", json={}).json() == {"sent": 0}


def test_planning_an_empty_project_is_a_quiet_no_op(empty_project):
    from app.intel import engine
    r = run(engine.run_once(send=True))
    assert r["status"] == "NO_WORK" and r["risks"] == [] and r["actions"] == []


def test_the_first_task_can_be_added_from_chat_to_an_empty_project(empty_project):
    c = empty_project
    with Session(db_engine) as s:
        me = s.exec(select(Member).where(Member.key == "ada")).first()
        out = run(service.handle_dm(s, me, "task: Set up the repository @bo due tomorrow est 1h", channel="telegram"))
    assert "T1" in out[0]["text"]
    (t,) = c.get("/api/board").json()["tasks"]
    assert (t["id"], t["owner_key"], t["status"]) == ("T1", "bo", "Pending")


def test_the_real_project_ships_without_pre_set_tasks():
    from pathlib import Path
    real = Path(__file__).resolve().parent.parent / "data" / "tasks.csv"
    assert [ln for ln in real.read_text().splitlines() if ln.strip()] == ["id,title,owner,status,deadline,depends_on,aliases,est_minutes"]


def test_group_and_dm_destinations_are_exported_for_both_chat_apps():
    assert channels.expand_mentions("{M1}", {1: SimpleNamespace(name="Ada", slack_user_id="U1", tg_user_id=5)}, "slack") == "<@U1>"


def test_reset_asks_for_a_replan_so_the_graph_mirror_forgets_the_old_project(world, monkeypatch):
    from app.intel import engine
    seen = []
    monkeypatch.setattr(engine, "request", lambda kind, tid=None: seen.append(kind))
    world.post("/api/reset")
    assert seen == ["RESET"]


def test_reset_keeps_a_backup_of_the_database_so_a_wipe_is_recoverable(tmp_path, monkeypatch):
    from sqlalchemy import create_engine

    from app import db
    dbfile = tmp_path / "proj.db"
    eng = create_engine(f"sqlite:///{dbfile}")
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "_sqlite", True)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{dbfile}")
    dbfile.write_bytes(b"precious")
    first = db.backup_database()
    assert first is not None and first.read_bytes() == b"precious" and first.name.startswith("proj.before-reset-")
    for _ in range(8):                                                       # only the newest few are kept
        db.backup_database()
    assert len(list(tmp_path.glob("proj.before-reset-*.db"))) == db.KEEP_BACKUPS


def test_no_backup_for_a_non_sqlite_database(monkeypatch):
    from app import db
    monkeypatch.setattr(db, "_sqlite", False)
    assert db.backup_database() is None
