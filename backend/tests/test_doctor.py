"""The `doctor` command's logic, and the 'did the chat app really reach us' tracking it relies on."""
import asyncio
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import channels, doctor
from app import main as app_main
from app.channels import slack as slack_mod
from app.channels import telegram as tg_mod
from app.config import settings
from app.main import app
from app.models import Member

# ---------------------------------------------------------------- pure logic

def test_missing_scopes_separates_required_from_optional():
    need, opt = doctor.missing_scopes(set(doctor.SLACK_REQUIRED))
    assert need == set() and opt == {"channels:read", "groups:read"}
    need, opt = doctor.missing_scopes(set(doctor.SLACK_REQUIRED) - {"chat:write"} | {"channels:read", "groups:read"})
    assert need == {"chat:write"} and opt == set()


def test_manifest_scopes_cover_what_the_doctor_requires():
    from pathlib import Path
    manifest = (Path(__file__).resolve().parents[2] / "deploy" / "slack-manifest.yaml").read_text()
    for scope in doctor.SLACK_REQUIRED | doctor.SLACK_OPTIONAL:
        assert scope in manifest, f"{scope} is required by the doctor but missing from the Slack manifest"


@pytest.mark.parametrize("delta,expected", [(timedelta(seconds=5), "5s ago"), (timedelta(minutes=10), "10 min ago"), (timedelta(hours=3), "3 h ago")])
def test_ago(delta, expected):
    assert doctor.ago((datetime.now(timezone.utc) - delta).isoformat()) == expected
    assert doctor.ago(None) == "never"


def test_report_counts_orders_and_deduplicates_fixes():
    r = doctor.Report()
    r.section("Thing")
    r.ok("fine")
    r.warn("meh", "do A")
    r.warn("meh again", "do A")
    r.warn("no fix needed")
    out = r.render()
    assert "0 problem(s), 3 warning(s)" in out and not r.failed
    assert out.count("do A") == 1                                 # the same fix is listed once
    r.fail("broken", "do B")
    assert r.failed and "1 problem(s)" in r.render() and r.render().index("do A") < r.render().index("do B")


def test_a_clean_report_says_so():
    r = doctor.Report()
    r.ok("all good")
    assert "Everything that can be checked from here is connected and working." in r.render()


def member(name, role="member", slack=None, tg=None):
    return Member(key=name.lower(), name=name, role=role, slack_dm_id=slack, tg_chat_id=tg)


def people(*ms):
    r = doctor.Report()
    doctor.check_people(r, list(ms))
    return r


def test_the_lead_must_be_reachable_or_reviews_have_nowhere_to_go():
    r = people(member("Ada", "lead"), member("Bo"))
    assert r.failed and any("cannot reach anyone" in f or "reachable" in f for f in r.fixes)
    assert not people(member("Ada", "lead", slack="D1"), member("Bo")).failed
    assert not people(member("Ada", "lead", tg=5)).failed                    # Telegram alone is enough


def test_exactly_one_lead_is_required():
    assert people(member("Ada"), member("Bo")).failed
    assert people(member("Ada", "lead", slack="D1"), member("Bo", "lead", slack="D2")).failed


def test_unconnected_teammates_are_a_warning_not_a_failure():
    r = people(member("Ada", "lead", slack="D1"), member("Bo"), member("Cy"))
    assert not r.failed and any("Bo, Cy" in txt for lvl, txt in r.lines if lvl == "warn")


def test_the_people_table_shows_who_is_linked_where():
    out = people(member("Ada", "lead", slack="D1", tg=5), member("Bo")).render()
    assert "Ada" in out and out.count("linked") >= 2


def test_no_members_at_all_is_a_failure():
    assert people().failed


# ---------------------------------------------------------------- 'did the chat apps really reach us?'

@pytest.fixture()
def clean_stats():
    before = dict(channels.STATS)
    channels.STATS.update({"slack_real": None, "telegram": None})
    yield
    channels.STATS.update(before)


def test_health_starts_without_proof_that_slack_or_telegram_reached_us(clean_stats):
    with TestClient(app) as c:
        h = c.get("/api/health").json()
    assert h["slack_last_real_event"] is None and h["telegram_last_update"] is None


def test_a_telegram_update_is_recorded(clean_stats):
    asyncio.run(tg_mod.stamp_update(None, None))
    with TestClient(app) as c:
        assert c.get("/api/health").json()["telegram_last_update"] is not None


def _signed(body, secret):
    stamp = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": sig}


def test_only_slacks_own_servers_count_as_proof_slack_is_configured(clean_stats, monkeypatch):
    body = '{"type":"url_verification","challenge":"x"}'
    with TestClient(app) as c:                       # started FIRST: with a Slack token set, startup would build the real global channel
        monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
        monkeypatch.setattr(settings, "slack_signing_secret", "s3cret")
        ch = slack_mod.SlackChannel()
        ch.build()
        monkeypatch.setattr(app_main, "SLACK", ch)
        c.post("/api/slack/events", content=body, headers=_signed(body, "s3cret"))                       # a test request (curl, simulator)
        assert c.get("/api/health").json()["slack_last_real_event"] is None
        c.post("/api/slack/events", content=body, headers={**_signed(body, "s3cret"), "User-Agent": "Slackbot 1.0 (+https://api.slack.com/robots)"})
        assert c.get("/api/health").json()["slack_last_real_event"] is not None


def test_someone_who_joined_in_the_telegram_group_is_reachable_once_a_group_exists():
    joined = Member(id=2, key="bo", name="Bo", role="member", tg_user_id=222)
    r = doctor.Report()
    doctor.check_people(r, [member("Ada", "lead", slack="D1"), joined], group=True)
    assert not any("not connected" in t for lvl, t in r.lines if lvl == "warn") and "in group" in r.render()
    r2 = doctor.Report()
    doctor.check_people(r2, [member("Ada", "lead", slack="D1"), joined], group=False)          # without a group there is nowhere to reach Bo
    assert any("not connected to any app: Bo" in t for lvl, t in r2.lines if lvl == "warn")


def test_a_lead_who_joined_only_in_the_group_is_reachable():
    lead = Member(id=1, key="ada", name="Ada", role="lead", tg_user_id=111)
    r = doctor.Report()
    doctor.check_people(r, [lead], group=True)
    assert not r.failed
    r2 = doctor.Report()
    doctor.check_people(r2, [lead], group=False)
    assert r2.failed
