"""HTTP contract: health, validation, error codes, prod gating, auth on webhooks, live stream."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


@pytest.fixture()
def c():
    with TestClient(app) as client:
        client.post("/api/reset")
        yield client


def tasks(c):
    return {t["id"]: t for t in c.get("/api/board").json()["tasks"]}


# ---------------------------------------------------------------- health

def test_health_reports_nothing_configured(c):
    h = c.get("/api/health").json()
    assert h["ok"] is True and h["env"] == "dev"
    assert not any([h["telegram"], h["slack"], h["teams"], h["recall"], h["jev"], h["llm"]])


def test_ready_checks_database(c):
    assert c.get("/api/ready").json() == {"ok": True}


# ---------------------------------------------------------------- board + team

def test_board_shape_and_seed(c):
    b = c.get("/api/board").json()
    assert set(b) >= {"tasks", "members", "counts", "pending_reviews"}
    t = tasks(c)
    assert t["T1"]["status"] == "Complete" and t["T2"]["status"] == "Pending"
    assert sum(b["counts"].values()) == len(b["tasks"])
    assert {m["key"] for m in b["members"]} >= {"aanya", "kabir", "meera", "priya"}


def test_team_lists_members_without_links_when_no_telegram(c):
    team = c.get("/api/team").json()
    assert {m["key"] for m in team} >= {"aanya", "kabir"}
    assert all(m["telegram_link"] is None and m["channels"] == [] for m in team)


def test_preferred_channel_rejected_when_not_linked(c):
    r = c.post("/api/team/kabir/preferred", json={"channel": "slack"})
    assert r.status_code == 400


def test_preferred_channel_unknown_member(c):
    assert c.post("/api/team/ghost/preferred", json={"channel": "slack"}).status_code == 400


# ---------------------------------------------------------------- 404s and validation

def test_timeline_unknown_task_is_404(c):
    assert c.get("/api/tasks/NOPE/timeline").status_code == 404


def test_resolve_requires_verdict(c):
    assert c.post("/api/reviews/1/resolve", json={}).status_code == 422


def test_dev_message_unknown_member(c):
    r = c.post("/api/dev/message", json={"member_key": "ghost", "text": "hi"})
    assert r.status_code == 404


def test_dev_message_requires_text(c):
    assert c.post("/api/dev/message", json={"member_key": "kabir"}).status_code == 422


def test_unknown_button_payload_is_ignored(c):
    r = c.post("/api/dev/button", json={"member_key": "kabir", "data": "garbage"})
    assert r.status_code == 200 and r.json() == {"outbox": []}


def test_missing_review_and_event_are_404(c):
    assert c.post("/api/reviews/999/resolve", json={"verdict": "approve"}).status_code == 404
    assert c.post("/api/events/9999/undo").status_code == 404


def test_resolve_rejects_unknown_verdict(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "done I think but not totally sure"})
    rid = c.get("/api/reviews").json()[0]["id"]
    assert c.post(f"/api/reviews/{rid}/resolve", json={"verdict": "banana"}).status_code == 400
    assert len(c.get("/api/reviews").json()) == 1          # still waiting for the lead


def test_resolving_or_undoing_twice_is_harmless(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "done I think but not totally sure"})
    rid = c.get("/api/reviews").json()[0]["id"]
    assert c.post(f"/api/reviews/{rid}/resolve", json={"verdict": "In Progress"}).status_code == 200
    assert c.post(f"/api/reviews/{rid}/resolve", json={"verdict": "Blocked"}).status_code == 200
    assert tasks(c)["T2"]["status"] == "In Progress"       # second click changed nothing
    ev = [i for i in c.get("/api/tasks/T2/timeline").json()["items"] if i["type"] == "event"][-1]
    assert c.post(f"/api/events/{ev['id']}/undo").status_code == 200
    assert c.post(f"/api/events/{ev['id']}/undo").status_code == 200


# ---------------------------------------------------------------- check-ins

def test_checkin_all_tasks_skips_nothing_pending(c):
    sent = c.post("/api/checkin", json={}).json()["sent"]
    assert sent >= 1


def test_checkin_single_task(c):
    assert c.post("/api/checkin", json={"task_id": "T2"}).json() == {"sent": 1}


# ---------------------------------------------------------------- reply handling

def test_done_with_hedge_never_completes(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "done I think but not totally sure"})
    assert tasks(c)["T2"]["status"] != "Complete"
    assert len(c.get("/api/reviews").json()) == 1


def test_chitchat_changes_nothing(c):
    before = tasks(c)
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "lol"})
    assert {k: v["status"] for k, v in tasks(c).items()} == {k: v["status"] for k, v in before.items()}


def test_activity_feed_records_changes(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "stuck"})
    assert tasks(c)["T2"]["status"] == "Blocked"
    assert len(c.get("/api/activity").json()) >= 1


def test_activity_shows_names_instead_of_mention_tokens(c):
    from sqlmodel import Session, select

    from app.db import engine
    from app.models import Member, Message
    with Session(engine) as s:
        kabir = s.exec(select(Member).where(Member.key == "kabir")).first()
        s.add(Message(member_id=kabir.id, direction="out", text=f"New task T9 for {{M{kabir.id}}} and {{M999}}"))
        s.commit()
    texts = [i["text"] for i in c.get("/api/activity").json() if i["type"] == "message"]
    assert "New task T9 for @Kabir and @someone" in texts


def test_undo_restores_previous_status(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "working on it"})
    ev = [i for i in c.get("/api/tasks/T2/timeline").json()["items"] if i["type"] == "event"][-1]
    assert tasks(c)["T2"]["status"] == "In Progress"
    c.post(f"/api/events/{ev['id']}/undo")
    assert tasks(c)["T2"]["status"] == "Pending"


def test_reset_clears_state(c):
    c.post("/api/dev/message", json={"member_key": "kabir", "text": "stuck"})
    c.post("/api/reset")
    assert tasks(c)["T2"]["status"] == "Pending"


# ---------------------------------------------------------------- production gating

def test_dev_endpoints_forbidden_in_prod(c, monkeypatch):
    monkeypatch.setattr(main, "DEV", False)
    assert c.post("/api/reset").status_code == 403
    assert c.post("/api/dev/message", json={"member_key": "kabir", "text": "x"}).status_code == 403
    assert c.post("/api/dev/button", json={"member_key": "kabir", "data": "x"}).status_code == 403


# ---------------------------------------------------------------- integrations are off by default

def test_slack_http_mode_not_configured(c):
    assert c.post("/api/slack/events").status_code == 404


def test_recall_webhook_wrong_token_rejected(c):
    assert c.post("/api/meetings/webhooks/recall?token=bad&meeting_id=1", json={}).status_code == 401


def test_recall_webhook_unknown_meeting_still_2xx(c):
    r = c.post("/api/meetings/webhooks/recall?token=change-me&meeting_id=99999", json={})
    assert r.status_code == 200


def test_join_meeting_needs_recall_key(c):
    r = c.post("/api/meetings", json={"meeting_url": "https://teams.microsoft.com/l/meetup-join/x"})
    assert r.status_code == 400 and "RECALL_API_KEY" in r.json()["detail"]


def test_upload_rejects_empty_transcript(c):
    assert c.post("/api/meetings/upload", data={"text": ""}).status_code == 400


def test_unknown_meeting_is_404(c):
    assert c.get("/api/meetings/99999").status_code == 404
    assert c.post("/api/meetings/99999/finish").status_code == 404


# ---------------------------------------------------------------- Slack HTTP events (what Slack does when you save the Request URL)

def _slack_signed(body: str, secret: str, ts: int | None = None, tamper: bool = False) -> dict:
    import hashlib
    import hmac
    import time
    ts = str(ts if ts is not None else int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig[:-1] + ("0" if sig[-1] != "0" else "1") if tamper else sig}


@pytest.fixture()
def slack_http(monkeypatch):
    from app.channels.slack import SlackChannel
    monkeypatch.setattr(main.settings, "slack_bot_token", "xoxb-test-token")
    monkeypatch.setattr(main.settings, "slack_signing_secret", "test-signing-secret")
    ch = SlackChannel()
    ch.build()
    monkeypatch.setattr(main, "SLACK", ch)
    return "test-signing-secret"


def test_slack_challenge_is_answered_for_a_correctly_signed_request(c, slack_http):
    body = '{"type":"url_verification","challenge":"3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P"}'
    r = c.post("/api/slack/events", content=body, headers=_slack_signed(body, slack_http))
    assert r.status_code == 200 and "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P" in r.text


def test_slack_requests_with_a_wrong_signature_are_rejected(c, slack_http):
    body = '{"type":"url_verification","challenge":"x"}'
    assert c.post("/api/slack/events", content=body, headers=_slack_signed(body, slack_http, tamper=True)).status_code == 401
    assert c.post("/api/slack/events", content=body, headers=_slack_signed(body, "some-other-secret")).status_code == 401


def test_slack_requests_with_an_old_timestamp_are_rejected_as_replays(c, slack_http):
    import time
    body = '{"type":"url_verification","challenge":"x"}'
    assert c.post("/api/slack/events", content=body,
                  headers=_slack_signed(body, slack_http, ts=int(time.time()) - 3600)).status_code == 401
