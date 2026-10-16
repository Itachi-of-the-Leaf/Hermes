"""The brief's full scenario through the HTTP API (dev endpoints stand in for Telegram)."""
from fastapi.testclient import TestClient

from app.main import app


def task(c, tid):
    return next(t for t in c.get("/api/board").json()["tasks"] if t["id"] == tid)


def test_kabir_loop():
    with TestClient(app) as c:
        c.post("/api/reset")
        assert task(c, "T2")["status"] == "Pending"

        # 1. follow-up goes out
        assert c.post("/api/checkin", json={"task_id": "T2"}).json()["sent"] == 1

        # 2. Kabir: connected but waiting -> Blocked, not Complete
        r = c.post("/api/dev/message", json={"member_key": "kabir",
                                             "text": "The API is connected, but I am waiting for test credentials."}).json()
        t2 = task(c, "T2")
        assert t2["status"] == "Blocked"
        assert t2["blocker_text"] == "test credentials"
        assert t2["blocked_by"] == "Priya"                  # owner of the open dependency T3
        recipients = {o["to"] for o in r["outbox"]}
        assert {"Kabir", "Priya", "Meera"} <= recipients     # confirm, unblock request, at-risk warning
        assert task(c, "T4")["at_risk"]                      # checkout depends on T2

        # 3. Meera believes it's done -> conflict alert to both
        r = c.post("/api/dev/message", json={"member_key": "meera", "group": True,
                                             "text": "integration's done, starting checkout now"}).json()
        assert {"Meera", "Kabir"} == {o["to"] for o in r["outbox"]}

        # 4. Priya: "sent them" -> blocker resolved, Kabir re-asked
        r = c.post("/api/dev/message", json={"member_key": "priya", "text": "sent them on email"}).json()
        assert any(o["to"] == "Kabir" and o["kind"] == "followup" for o in r["outbox"])

        # 5. Kabir finishes with proof -> Complete
        c.post("/api/dev/message", json={"member_key": "kabir", "text": "tested, all green, PR #14"})
        t2 = task(c, "T2")
        assert t2["status"] == "Complete" and t2["proof_url"] == "PR #14"
        assert not task(c, "T4")["at_risk"]

        # 6. timeline shows every change with its evidence; undo works
        tl = c.get("/api/tasks/T2/timeline").json()
        evs = [i for i in tl["items"] if i["type"] == "event"]
        assert [e["new_status"] for e in evs] == ["Blocked", "Complete"]
        c.post(f"/api/events/{evs[-1]['id']}/undo")
        assert task(c, "T2")["status"] == "Blocked"


def test_unsure_goes_to_lead_review():
    with TestClient(app) as c:
        c.post("/api/reset")
        c.post("/api/dev/message", json={"member_key": "kabir", "text": "done I think but not totally sure"})
        reviews = c.get("/api/reviews").json()
        assert len(reviews) == 1
        rid = reviews[0]["id"]
        c.post(f"/api/reviews/{rid}/resolve", json={"verdict": "In Progress"})
        assert task(c, "T2")["status"] == "In Progress"
        assert c.get("/api/reviews").json() == []


def test_stuck_button_then_blocker_text():
    with TestClient(app) as c:
        c.post("/api/reset")
        c.post("/api/dev/button", json={"member_key": "kabir", "data": "st:T2:stuck"})
        c.post("/api/dev/message", json={"member_key": "kabir", "text": "no sandbox keys yet"})
        t2 = task(c, "T2")
        assert t2["status"] == "Blocked" and t2["blocker_text"] == "no sandbox keys yet"
