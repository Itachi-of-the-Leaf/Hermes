"""Channel layer: formatting per platform, routing to each member's preferred app."""
import asyncio

from sqlmodel import Session, select

from app import channels
from app.channels.slack import blocks_for
from app.channels.teams import card_for
from app.db import engine, init_db, reset_db
from app.models import Member, Message

ITEM = {"text": "⛔ Got it: <b>Connect the payment API</b> → <b>Blocked</b>\nBlocker: <i>test credentials</i> & more",
        "buttons": [[("✅ Done", "st:T2:done"), ("⛔ Stuck", "st:T2:stuck")]], "kind": "confirm", "task_id": "T2"}


def test_formatters():
    assert channels.to_slack("<b>A</b> <i>b</i> & <x>") == "*A* _b_ &amp; "
    assert channels.to_markdown("<b>A</b> <i>b</i> &amp; c") == "**A** _b_ & c"
    assert channels.to_plain("<b>A</b> &amp; B") == "A & B"


def test_slack_blocks_carry_callback_values():
    blocks = blocks_for(ITEM)
    assert blocks[0]["text"]["text"].startswith("⛔ Got it: *Connect the payment API*")
    btns = blocks[1]["elements"]
    assert [b["value"] for b in btns] == ["st:T2:done", "st:T2:stuck"]
    assert len({b["action_id"] for b in btns}) == 2          # Slack requires unique action ids


def test_teams_card_carries_callback_values():
    card = card_for(ITEM).model_dump(by_alias=True, exclude_none=True)
    assert card["type"] == "AdaptiveCard"
    assert "**Connect the payment API**" in card["body"][0]["text"]
    assert [a["data"]["coord"] for a in card["actions"]] == ["st:T2:done", "st:T2:stuck"]


class FakeChannel:
    def __init__(self, name, field):
        self.name, self.field, self.sent = name, field, []

    def ready(self):
        return True

    def linked(self, m):
        return bool(getattr(m, self.field))

    async def send(self, m, item):
        self.sent.append((m.name, item["text"]))
        return f"{self.name}-{len(self.sent)}"


def test_routes_to_preferred_channel(monkeypatch):
    init_db()
    reset_db()
    slack, teams = FakeChannel("slack", "slack_dm_id"), FakeChannel("teams", "teams_conv_id")
    monkeypatch.setattr(channels, "REGISTRY", {"slack": slack, "teams": teams})
    with Session(engine) as s:
        kabir = s.exec(select(Member).where(Member.key == "kabir")).one()
        channels.link(s, kabir, "teams", teams_conv_id="a:1", teams_aad_id="aad-1")
        channels.link(s, kabir, "slack", slack_user_id="U1", slack_dm_id="D1")
        assert kabir.preferred_channel == "teams"             # first connected app wins by default
        asyncio.run(channels.deliver(s, [{**ITEM, "member_id": kabir.id}]))
        assert teams.sent and not slack.sent
        kabir.preferred_channel = "slack"
        s.add(kabir)
        s.commit()
        asyncio.run(channels.deliver(s, [{**ITEM, "member_id": kabir.id}]))
        assert slack.sent
        last = s.exec(select(Message).where(Message.direction == "out").order_by(Message.id.desc())).first()
        assert last.channel == "slack" and last.ext_id == "slack-1"
        assert "<b>" not in last.text                         # stored as plain text for the dashboard
