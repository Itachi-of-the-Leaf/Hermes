"""The real roster (backend/data), the Slack roster command, and channel-name lookup."""
import asyncio
import csv
from pathlib import Path

import pytest

from app.channels.slack import SlackChannel
from app.cli import roster_rows
from app.config import settings
from app.intel.graph import ProjectGraph, TNode

REAL = Path(__file__).resolve().parent.parent / "data"      # not DATA_DIR: tests run on the sample team


def read(name):
    with open(REAL / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- the real data files must be self-consistent

def test_real_team_has_one_lead_unique_keys_and_emails():
    team = read("team.csv")
    assert len(team) >= 2
    assert [m["role"] for m in team].count("lead") == 1
    assert len({m["key"] for m in team}) == len(team)
    assert all(m["email"] and "@" in m["email"] for m in team)       # Slack links people by email
    assert all(m["key"] == m["key"].lower() and m["key"].isalnum() for m in team)


def test_real_tasks_belong_to_real_people_and_form_a_valid_graph():
    keys = {m["key"] for m in read("team.csv")}
    tasks = read("tasks.csv")
    assert {t["owner"] for t in tasks} <= keys, "a task is owned by someone who is not in team.csv"
    assert len({t["id"] for t in tasks}) == len(tasks)
    g = ProjectGraph([TNode(id=t["id"], title=t["title"], owner_id=1, owner=t["owner"], status=t["status"],
                            deps=[d for d in t["depends_on"].split(",") if d]) for t in tasks])
    assert g.issues == [] and g.cycle is None
    assert all(t["status"] in ("Pending", "In Progress", "Blocked", "Complete") for t in tasks)


def test_the_sample_team_no_longer_ships_as_the_real_one():
    emails = [m["email"] for m in read("team.csv")]
    assert not any(e.endswith("@example.edu") for e in emails)


# ---------------------------------------------------------------- slack-roster

SLACK_USERS = [
    {"id": "U1", "name": "owner", "real_name": "Ada Lovelace", "is_owner": True, "profile": {"email": "Ada@X.com"}},
    {"id": "U2", "name": "handle", "real_name": "sam", "profile": {"email": "sam@x.com"}},
    {"id": "U3", "name": "bot", "is_bot": True, "profile": {}},
    {"id": "USLACKBOT", "name": "slackbot", "profile": {}},
    {"id": "U4", "name": "gone", "deleted": True, "real_name": "Gone", "profile": {}},
    {"id": "U5", "name": "sam2", "real_name": "Sam Two", "profile": {"email": ""}},
]


def test_roster_comes_from_real_people_only_with_owner_as_lead():
    rows = roster_rows(SLACK_USERS)
    assert [r["key"] for r in rows] == ["ada", "sam", "sam2"]        # bots, Slackbot and deactivated users are skipped
    assert rows[0] == {"key": "ada", "name": "Ada", "email": "ada@x.com", "role": "lead"}
    assert [r["role"] for r in rows] == ["lead", "member", "member"]


def test_duplicate_first_names_get_distinct_keys():
    users = [{"id": f"U{i}", "name": "x", "real_name": "Alex Smith", "profile": {"email": f"a{i}@x.com"}} for i in range(3)]
    assert [r["key"] for r in roster_rows(users)] == ["alex", "alex2", "alex22"]


def test_somebody_always_leads_even_without_a_workspace_owner():
    rows = roster_rows([{"id": "U1", "name": "a", "real_name": "A One", "profile": {}},
                        {"id": "U2", "name": "b", "real_name": "B Two", "profile": {}}])
    assert [r["role"] for r in rows] == ["lead", "member"]


def test_two_owners_still_yield_one_lead():
    users = [{"id": f"U{i}", "name": "x", "real_name": f"P{i}", "is_owner": True, "profile": {}} for i in range(2)]
    assert [r["role"] for r in roster_rows(users)] == ["lead", "member"]


# ---------------------------------------------------------------- channel names in .env

class FakeClient:
    def __init__(self, pages=None, error=None):
        self.pages, self.error, self.calls = pages or [], error, []

    async def conversations_list(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        i = len(self.calls) - 1
        return {"channels": self.pages[i], "response_metadata": {"next_cursor": "more" if i + 1 < len(self.pages) else ""}}


def channel_with(client):
    ch = SlackChannel()
    ch.app = type("A", (), {"client": client})()
    return ch


@pytest.fixture()
def names(monkeypatch):
    monkeypatch.setattr(settings, "slack_project_channel", "#proj-demo")
    monkeypatch.setattr(settings, "slack_summary_channel", "proj-demo-meetings")        # the # is optional
    monkeypatch.setattr(settings, "slack_alert_channel", "CALERT12345")                  # already an ID: left alone


def test_channel_names_are_resolved_to_ids_across_pages(names):
    client = FakeClient([[{"name": "general", "id": "C0GENERAL1"}, {"name": "proj-demo", "id": "C0PROJ0001"}],
                         [{"name": "proj-demo-meetings", "id": "C0MEET0001"}]])
    asyncio.run(channel_with(client).resolve_channels())
    assert settings.slack_project_channel == "C0PROJ0001"
    assert settings.slack_summary_channel == "C0MEET0001"
    assert settings.slack_alert_channel == "CALERT12345"
    assert len(client.calls) == 2 and client.calls[0]["types"] == "public_channel,private_channel"


def test_ids_only_means_no_lookup_and_no_scope_needed(monkeypatch):
    monkeypatch.setattr(settings, "slack_project_channel", "C0PROJ0001")
    monkeypatch.setattr(settings, "slack_summary_channel", "")
    monkeypatch.setattr(settings, "slack_alert_channel", "")
    client = FakeClient()
    asyncio.run(channel_with(client).resolve_channels())
    assert client.calls == []


def test_a_missing_scope_is_reported_and_leaves_settings_untouched(names, caplog):
    with caplog.at_level("WARNING", logger="slack"):
        asyncio.run(channel_with(FakeClient(error=RuntimeError("missing_scope"))).resolve_channels())
    assert settings.slack_project_channel == "#proj-demo"
    assert any("channels:read" in r.message for r in caplog.records)


def test_an_unknown_channel_is_reported_not_silently_ignored(names, caplog):
    with caplog.at_level("WARNING", logger="slack"):
        asyncio.run(channel_with(FakeClient([[{"name": "proj-demo", "id": "C0PROJ0001"}]])).resolve_channels())
    assert settings.slack_project_channel == "C0PROJ0001"
    assert any("proj-demo-meetings" in r.message and "not found" in r.message for r in caplog.records)
