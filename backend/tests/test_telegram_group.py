"""Starting Telegram as a GROUP: add the bot, everyone taps /join, mentions and /task work, no private chats needed."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from telegram.constants import ChatType

from app import channels
from app.channels import telegram as tg
from app.config import settings
from app.db import engine as db_engine
from app.main import app
from app.models import Member, Setting, Task

GROUP, PRIVATE = ChatType.SUPERGROUP, ChatType.PRIVATE


def run(coro):
    return asyncio.run(coro)


class Msg:
    def __init__(self, text=""):
        self.text, self.sent, self.reply_to_message, self.message_id = text, [], None, 1

    async def reply_text(self, text, **kw):
        self.sent.append(("text", text, kw))

    async def reply_html(self, text, **kw):
        self.sent.append(("html", text, kw))

    def said(self):
        return " ".join(t for _, t, _ in self.sent)


def update(user_id, chat_type=GROUP, text="", chat_id=-1001, first_name="Pat", callback=None, my_chat_member=None):
    msg = Msg(text)
    return SimpleNamespace(message=msg, effective_message=msg, effective_user=SimpleNamespace(id=user_id, first_name=first_name),
                           effective_chat=SimpleNamespace(id=chat_id, type=chat_type), callback_query=callback, my_chat_member=my_chat_member), msg


class FakeBot:
    def __init__(self, sees_all=False):
        self.sent, self.sees_all = [], sees_all

    async def get_me(self):
        return SimpleNamespace(can_read_all_group_messages=self.sees_all, username="xdominator_bot")

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw))


def ctx(bot=None, args=()):
    return SimpleNamespace(args=list(args), bot=bot or FakeBot())


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "telegram_group_chat_id", "")
    with TestClient(app) as c:
        c.post("/api/reset")
        with Session(db_engine) as s:
            for m in s.exec(select(Member)).all():
                m.tg_user_id = m.tg_chat_id = m.slack_user_id = m.slack_dm_id = m.preferred_channel = None
                s.add(m)
            for row in s.exec(select(Setting)).all():
                s.delete(row)
            s.commit()
        yield c


def member(key):
    with Session(db_engine) as s:
        m = s.exec(select(Member).where(Member.key == key)).first()
        return SimpleNamespace(**{k: getattr(m, k) for k in ("id", "key", "name", "role", "tg_user_id", "tg_chat_id", "preferred_channel")})


def group_saved():
    with Session(db_engine) as s:
        return channels.group_chat_id(s)


def tap(user_id, key, chat_type=GROUP, first_name="Pat"):
    """A person pressing a name button on the picker."""
    edits = []

    async def answer():
        return None

    async def edit_text(text, **kw):
        edits.append(("text", text))

    async def edit_markup(markup=None, **kw):
        edits.append(("markup", markup))
    q = SimpleNamespace(data=f"iam:{key}", message=Msg(), answer=answer, edit_message_text=edit_text, edit_message_reply_markup=edit_markup)
    u, _ = update(user_id, chat_type, callback=q, first_name=first_name)
    run(tg.on_button(u, ctx()))
    return q, edits


def bot_added(chat_type=GROUP, old="left", new="member", chat_id=-1001, sees_all=False):
    cm = SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type), old_chat_member=SimpleNamespace(status=old), new_chat_member=SimpleNamespace(status=new))
    u, _ = update(1, my_chat_member=cm)
    bot = FakeBot(sees_all)
    run(tg.on_bot_added(u, ctx(bot)))
    return bot


def buttons(markup):
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


# ================================================================ linking

def test_linking_in_a_private_chat_stores_the_chat_id_so_the_agent_can_dm(world):
    tap(111, "kabir", PRIVATE)
    m = member("kabir")
    assert (m.tg_user_id, m.tg_chat_id) == (111, -1001)


def test_linking_in_a_group_never_turns_the_group_into_the_persons_private_chat(world):
    tap(111, "kabir", GROUP)
    m = member("kabir")
    assert m.tg_user_id == 111 and m.tg_chat_id is None


def test_joining_in_the_group_keeps_an_existing_private_link(world):
    tap(111, "kabir", PRIVATE)
    with Session(db_engine) as s:
        me = s.exec(select(Member).where(Member.key == "kabir")).first()
        u, _ = update(111, GROUP)
        tg._link(s, me, u)
    assert member("kabir").tg_chat_id == -1001                    # the private chat id survives


def test_one_telegram_account_is_one_team_member(world):
    tap(111, "kabir")
    with Session(db_engine) as s:
        u, _ = update(111, GROUP)
        tg._link(s, s.exec(select(Member).where(Member.key == "meera")).first(), u)
    assert member("kabir").tg_user_id is None and member("meera").tg_user_id == 111


# ================================================================ /join and the shared picker

def test_join_in_the_group_offers_only_people_not_yet_connected(world):
    tap(111, "kabir")
    u, msg = update(222)
    run(tg.join_cmd(u, ctx()))
    names = buttons(msg.sent[0][2]["reply_markup"])
    assert "Kabir" not in names and {"Aanya", "Meera", "Priya"} <= set(names)


def test_join_when_already_connected_says_so(world):
    tap(111, "kabir")
    u, msg = update(111)
    run(tg.join_cmd(u, ctx()))
    assert "already connected as Kabir" in msg.said() and "reply_markup" not in msg.sent[0][2]


def test_join_when_everybody_is_connected(world):
    for i, key in enumerate(("aanya", "kabir", "meera", "priya")):
        tap(100 + i, key)
    u, msg = update(999)
    run(tg.join_cmd(u, ctx()))
    assert "Everyone on the team is already connected" in msg.said()


def test_tapping_a_name_in_the_group_links_it_and_keeps_the_picker_for_everyone_else(world):
    q, edits = tap(111, "kabir", GROUP, first_name="Kabir S")
    assert member("kabir").tg_user_id == 111
    markup = next(m for kind, m in edits if kind == "markup")
    assert "Kabir" not in buttons(markup) and "Meera" in buttons(markup)       # the others can still tap theirs
    announce = q.message.sent[0][1]
    assert "tg://user?id=111" in announce and "<b>Kabir</b>" in announce


def test_the_picker_disappears_when_the_last_person_has_joined(world):
    for i, key in enumerate(("aanya", "kabir", "meera")):
        tap(100 + i, key)
    q, edits = tap(103, "priya")
    assert [m for kind, m in edits if kind == "markup"] == [None]


def test_a_name_that_belongs_to_someone_else_cannot_be_taken_over(world):
    tap(111, "kabir")
    q, edits = tap(222, "kabir")
    assert member("kabir").tg_user_id == 111 and "already connected to another" in q.message.sent[0][1]


def test_in_a_private_chat_the_button_message_becomes_a_confirmation(world):
    q, edits = tap(111, "meera", PRIVATE)
    assert edits == [("text", "✅ Connected as Meera.")]


def test_start_with_a_key_in_the_group_links_without_a_private_chat(world):
    u, msg = update(111, GROUP)
    run(tg.start(u, ctx(args=["priya"])))
    assert member("priya").tg_user_id == 111 and member("priya").tg_chat_id is None and "in the group" in msg.said()


# ================================================================ adding the bot to a group

def test_adding_the_bot_to_a_group_remembers_it_and_explains_how_to_start(world):
    bot = bot_added()
    assert group_saved() == -1001
    chat_id, text, kw = bot.sent[0]
    assert chat_id == -1001 and "/join" in text and "/task" in text and set(buttons(kw["reply_markup"])) == {"Aanya", "Kabir", "Meera", "Priya"}


def test_a_bot_that_cannot_read_the_group_says_how_to_fix_it(world):
    assert "admin" in bot_added(sees_all=False).sent[0][1] and "BotFather" in bot_added(chat_id=-1002, sees_all=False).sent[0][1]


def test_an_admin_bot_or_one_with_privacy_off_needs_no_warning(world):
    assert "only see /commands" not in bot_added(new="administrator").sent[0][1]
    assert "only see /commands" not in bot_added(chat_id=-1002, sees_all=True).sent[0][1]


def test_a_second_group_does_not_replace_the_first_but_points_to_setgroup(world):
    bot_added(chat_id=-1001)
    second = bot_added(chat_id=-1002)
    assert group_saved() == -1001 and "/setgroup" in second.sent[0][1]


def test_a_configured_group_id_always_wins(world, monkeypatch):
    monkeypatch.setattr(settings, "telegram_group_chat_id", "-42")
    bot_added(chat_id=-1001)
    with Session(db_engine) as s:
        assert channels.group_chat_id(s) == -42 and s.get(Setting, "tg_group_chat_id") is None


@pytest.mark.parametrize("kind,old,new", [(PRIVATE, "left", "member"), (ChatType.CHANNEL, "left", "member"),
                                          (GROUP, "member", "administrator"), (GROUP, "member", "left"), (GROUP, "left", "kicked")])
def test_only_the_moment_the_bot_joins_a_group_counts(world, kind, old, new):
    bot = bot_added(chat_type=kind, old=old, new=new)
    assert bot.sent == [] and group_saved() is None


# ================================================================ /setgroup

def setgroup(user_id, chat_type=GROUP, chat_id=-2002):
    u, msg = update(user_id, chat_type, chat_id=chat_id)
    run(tg.setgroup_cmd(u, ctx()))
    return msg.said()


def test_the_lead_can_switch_the_project_group(world):
    bot_added(chat_id=-1001)
    tap(100, "aanya")                                              # Aanya is the lead
    assert "now the project group" in setgroup(100, chat_id=-2002) and group_saved() == -2002


def test_only_the_lead_can_switch_it(world):
    bot_added(chat_id=-1001)
    tap(200, "kabir")
    assert "Only the team lead" in setgroup(200) and group_saved() == -1001
    assert "Only the team lead" in setgroup(999)                   # someone the bot does not know


def test_setgroup_only_works_inside_a_group(world):
    tap(100, "aanya")
    assert "inside the team group" in setgroup(100, chat_type=PRIVATE)


def test_setgroup_cannot_override_the_env_setting(world, monkeypatch):
    tap(100, "aanya")
    monkeypatch.setattr(settings, "telegram_group_chat_id", "-42")
    assert "takes precedence" in setgroup(100)


# ================================================================ the whole thing, end to end

class FakeChannel:
    name = "telegram"

    def __init__(self):
        self.sent = []

    def ready(self):
        return True

    def linked(self, m):
        return bool(m.tg_chat_id)

    async def send(self, m, item):
        return None

    async def send_group(self, chat_id, html):
        self.sent.append((chat_id, html))
        return "7001"


def test_group_only_start_gives_real_mentions_and_working_commands(world, monkeypatch):
    """Add the bot, two people tap /join, and the agent can @mention them and they can add tasks. Nobody opens a private chat."""
    fake = FakeChannel()
    monkeypatch.setitem(channels.REGISTRY, "telegram", fake)
    bot_added()
    tap(111, "kabir")
    tap(222, "priya")
    priya_id = member("priya").id
    with Session(db_engine) as s:
        ext, channel = run(channels.deliver_group(s, f"{{M{priya_id}}} Kabir is blocked on the API. Can you help?", recipient_ids=[priya_id]))
    assert channel == "telegram" and fake.sent[0][0] == -1001
    assert '<a href="tg://user?id=222">Priya</a>' in fake.sent[0][1]                  # a mention that notifies her
    u, msg = update(111, GROUP, text="/task@xdominator_bot Write the report @priya est 1h")
    run(tg.task_cmd(u, ctx()))
    assert "Created" in msg.said()
    with Session(db_engine) as s:
        t = [x for x in s.exec(select(Task)).all() if x.title == "Write the report"][0]
        assert t.owner_id == priya_id and t.est_minutes == 60


def test_someone_who_has_not_joined_is_told_how(world):
    u, msg = update(555, GROUP, text="/task Do something")
    run(tg.task_cmd(u, ctx()))
    assert "/join" in msg.said()


def test_an_unknown_person_chatting_in_the_group_is_ignored_not_nagged(world):
    u, msg = update(555, GROUP, text="hello everyone")
    run(tg.on_text(u, ctx()))
    assert msg.sent == []


def test_help_lists_the_group_commands(world):
    u, msg = update(1, GROUP)
    run(tg.help_cmd(u, ctx()))
    assert "/join" in msg.said() and "/setgroup" in msg.said() and "/task" in msg.said()
