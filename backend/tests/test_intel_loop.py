"""The autonomous loop end to end, on the real database and engine:

    reply -> task state -> graph -> CP-SAT -> risk -> action plan -> group message -> reply -> repeat

Time is injected (`now=`), so promises expire and cooldowns pass without sleeping.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import channels
from app.config import settings
from app.db import engine as db_engine
from app.intel import engine, neo4j_mirror, planner, responses, schedule
from app.intel.graph import ProjectGraph
from app.main import app
from app.models import AgentAction, Forecast, Member, Message, Setting, Task

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture()
def world():
    with TestClient(app) as c:
        c.post("/api/reset")
        with Session(db_engine) as s:                        # deadlines relative to NOW so results are deterministic
            for m in s.exec(select(Member)).all():           # reset keeps app links on purpose; tests start unlinked
                m.tg_user_id = m.tg_chat_id = m.slack_user_id = m.slack_dm_id = m.preferred_channel = None
                s.add(m)
            for t in s.exec(select(Task)).all():
                t.deadline = (NOW + timedelta(hours=12)).isoformat()
                s.add(t)
            for row in s.exec(select(Setting)).all():
                s.delete(row)
            s.commit()
        yield c


def run(**kw):
    kw.setdefault("now", NOW)
    return asyncio.run(engine.run_once(**kw))


def say(c, who, text, group=False):
    return c.post("/api/dev/message", json={"member_key": who, "text": text, "group": group}).json()


def member(s, key):
    return s.exec(select(Member).where(Member.key == key)).first()


def task(c, tid):
    return next(t for t in c.get("/api/board").json()["tasks"] if t["id"] == tid)


def actions(**eq):
    with Session(db_engine) as s:
        q = select(AgentAction).order_by(AgentAction.id)
        for k, v in eq.items():
            q = q.where(getattr(AgentAction, k) == v)
        return list(s.exec(q).all())


def reply(who, text, at=NOW, reply_to=None):
    with Session(db_engine) as s:
        return asyncio.run(responses.try_handle(s, member(s, who), text, reply_to=reply_to, now=at))


def make_external_blocker(c, blocker_owner="meera", deps_cleared=True):
    """The brief's scenario: Kabir waits for something a teammate has to hand over (not a tracked task)."""
    with Session(db_engine) as s:
        if deps_cleared:
            t = s.get(Task, "T2")
            t.depends_on = ""
            t.status, t.blocker_text, t.blocked_by_id = "Blocked", "test credentials", member(s, blocker_owner).id
            s.add(t)
            s.commit()


# ================================================================ the main loop (seeded project)

def test_blocker_reply_leads_to_a_targeted_follow_up_with_evidence(world):
    run()                                                    # baseline forecast, nothing wrong yet
    assert actions() == []
    say(world, "kabir", "The API is connected, but I am waiting for test credentials.")
    assert task(world, "T2")["status"] == "Blocked" and task(world, "T2")["blocked_by"] == "Priya"

    report = run()
    assert [r["risk_type"] for r in report["risks"]] == ["CONFIRMED_BLOCKER"]
    (a,) = report["actions"]
    assert a["type"] == "REQUEST_ETA" and a["recipient"] == "Priya" and a["task_id"] == "T2"
    text = a["text"]
    assert text.startswith("@Priya") and "{M" not in text
    assert "Kabir" in text and "Connect the payment API" in text and "test credentials" in text

    (row,) = actions()
    assert row.status == "sent" and row.graph_version == report["version"]
    why = json.loads(row.reason)
    assert why["risks"][0]["risk_type"] == "CONFIRMED_BLOCKER"
    assert any("Kabir reported" in e for e in why["risks"][0]["evidence"])
    assert any("Connect the payment API -> Build the checkout page" in e for e in why["risks"][0]["evidence"])
    with Session(db_engine) as s:                            # recorded as an action message, too
        assert s.exec(select(Message).where(Message.kind == "agent_action")).first() is not None


def test_repeating_the_run_does_not_repeat_the_message(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    assert len(run()["actions"]) == 1
    again = run()
    assert again["actions"] == []
    assert any("cooldown" in x["reason"] or "min gap" in x["reason"] for x in again["suppressed"])
    assert len(actions()) == 1


def test_the_cooldown_holds_after_the_min_gap_but_expires_on_time(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    assert len(run()["actions"]) == 1
    mid = run(now=NOW + timedelta(minutes=settings.intel_min_gap_min + 5))      # past the gap, inside the cooldown
    assert mid["actions"] == [] and any("cooldown" in x["reason"] for x in mid["suppressed"])
    assert len(run(now=NOW + timedelta(minutes=settings.intel_cooldown_min + 1))["actions"]) == 1


def test_a_promise_is_recorded_not_treated_as_delivery(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert reply("priya", "I'll send them in 20 minutes") == []
    (a,) = actions()
    assert a.status == "committed" and a.commitment_at == NOW + timedelta(minutes=20)
    assert a.followup_at == a.commitment_at + timedelta(minutes=settings.intel_commit_grace_min)
    t2 = task(world, "T2")
    assert t2["status"] == "Blocked" and not t2["blocker_resolved"]        # still blocked until confirmed


def test_nobody_is_chased_before_their_promise_runs_out(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    reply("priya", "in 20 minutes")
    mid = run(now=NOW + timedelta(minutes=10))
    assert mid["actions"] == [] and "CONFIRMED_BLOCKER" not in [r["risk_type"] for r in mid["risks"]]


def test_a_broken_promise_triggers_one_reminder_after_the_grace_period(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    reply("priya", "in 20 minutes")
    just_after = run(now=NOW + timedelta(minutes=22))                     # inside the grace window
    assert just_after["actions"] == []
    late = run(now=NOW + timedelta(minutes=31))
    (a,) = late["actions"]
    assert a["type"] == "REMIND_OWNER" and a["recipient"] == "Priya"
    assert [r["risk_type"] for r in late["risks"]] == ["COMMITMENT_OVERDUE"]
    assert "not confirmed" in a["text"]
    assert run(now=NOW + timedelta(minutes=33))["actions"] == []          # and only one


def test_delivery_resolves_the_blocker_and_asks_the_owner_to_resume(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    outbox = reply("priya", "sent them on email", at=NOW + timedelta(minutes=5))
    assert any(o["member_id"] and "Is it done now?" in o["text"] for o in outbox)
    assert task(world, "T2")["blocker_resolved"]
    assert [a.status for a in actions()] == ["resolved"]
    nothing = run(now=NOW + timedelta(minutes=6))
    assert nothing["actions"] == [] and "CONFIRMED_BLOCKER" not in [r["risk_type"] for r in nothing["risks"]]


def test_completing_the_task_closes_the_loop_and_clears_the_risk(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    reply("priya", "sent them", at=NOW + timedelta(minutes=5))
    say(world, "kabir", "tested, all green, PR #14")
    assert task(world, "T2")["status"] == "Complete"
    report = run(now=NOW + timedelta(minutes=10))
    assert report["risks"] == [] and report["actions"] == []
    assert "T2" not in report["analysis"].get("blocked", [])


def test_duplicate_reply_is_handled_once(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    reply("priya", "in 20 minutes")
    before = len(actions())
    reply("priya", "in 20 minutes")
    assert len(actions()) == before and actions()[0].commitment_at == NOW + timedelta(minutes=20)


def test_a_new_promise_supersedes_the_old_one(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    reply("priya", "in 20 minutes")
    reply("priya", "sorry, make it 2 hours", at=NOW + timedelta(minutes=25))
    a = actions()[0]
    assert a.commitment_at == NOW + timedelta(minutes=25 + 120)


# ================================================================ the brief's scenario: external resource

def test_external_blocker_changes_the_forecast_and_is_attributed_to_the_blocked_task(world):
    with Session(db_engine) as s:                            # a tight deadline so the blocker matters
        s.get(Task, "T5").deadline = (NOW + timedelta(hours=7)).isoformat()
        s.get(Task, "T2").depends_on = ""
        s.commit()
    before = run()
    assert before["total_late_min"] == 0 and before["actions"] == []

    make_external_blocker(world)
    after = run(now=NOW + timedelta(minutes=1))
    types = {r["risk_type"] for r in after["risks"]}
    assert {"CONFIRMED_BLOCKER", "DEADLINE_RISK"} <= types
    assert after["total_late_min"] > 0 and after["confidence"] == "LOW"
    deadline = next(r for r in after["risks"] if r["risk_type"] == "DEADLINE_RISK")
    assert deadline["attribution"] == "direct" and deadline["contributing_task"] == "T2"
    assert deadline["delay_minutes"] > 0 and deadline["previous_forecast"] == before["project_end"]

    (a,) = after["actions"]                                  # one merged message, not two
    assert a["type"] == "REQUEST_ETA" and a["recipient"] == "Meera"
    assert "would finish" in a["text"] and "an estimate" in a["text"]
    assert {x["risk_type"] for x in json.loads(actions()[0].reason)["risks"]} == {"CONFIRMED_BLOCKER", "DEADLINE_RISK"}


def test_a_commitment_improves_the_forecast_and_stops_the_nagging(world):
    with Session(db_engine) as s:
        s.get(Task, "T5").deadline = (NOW + timedelta(hours=7)).isoformat()
        s.get(Task, "T2").depends_on = ""
        s.commit()
    run()
    make_external_blocker(world)
    worse = run(now=NOW + timedelta(minutes=1))
    reply("meera", "I'll send them in 20 minutes", at=NOW + timedelta(minutes=2))
    better = run(now=NOW + timedelta(minutes=3))
    assert better["total_late_min"] < worse["total_late_min"]
    assert better["confidence"] in ("MEDIUM", "HIGH")
    assert better["actions"] == []                           # we know when it is coming; no pestering
    assert any("unverified" in x for x in json.loads(_latest_forecast())["forecast"]["assumptions"])


def _latest_forecast():
    with Session(db_engine) as s:
        return s.exec(select(Forecast).order_by(Forecast.id.desc())).first().payload


def test_an_unknown_blocker_owner_prompts_a_clarifying_question_to_the_owner(world):
    with Session(db_engine) as s:
        t = s.get(Task, "T2")
        t.depends_on, t.status, t.blocker_text, t.blocked_by_id = "", "Blocked", "an API key", None
        s.commit()
    (a,) = run()["actions"]
    assert a["type"] == "REQUEST_CLARIFICATION" and a["recipient"] == "Kabir"
    assert "Who should I ask" in a["text"]


# ================================================================ resilience

def test_the_follow_up_still_goes_out_when_the_scheduler_crashes(world, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("solver exploded")
    monkeypatch.setattr(schedule, "plan", boom)
    say(world, "kabir", "stuck, waiting for test credentials")
    report = run()
    assert report["status"] == "NONE"
    assert "PLAN_UNAVAILABLE" in [r["risk_type"] for r in report["risks"]]
    (a,) = report["actions"]
    assert a["type"] == "REQUEST_ETA" and a["recipient"] == "Priya"       # Priority 0 survives


def test_the_follow_up_still_goes_out_when_cpsat_is_unavailable(world, monkeypatch):
    monkeypatch.setattr(schedule, "solve_cpsat", lambda *a, **k: (_ for _ in ()).throw(ImportError("no ortools")))
    say(world, "kabir", "stuck, waiting for test credentials")
    report = run()
    assert report["status"] == "FALLBACK_CPM" and report["solver"] == "cpm"
    assert report["actions"] and report["actions"][0]["recipient"] == "Priya"


def test_a_dependency_cycle_is_escalated_to_the_lead_and_does_not_crash(world):
    with Session(db_engine) as s:
        s.get(Task, "T3").depends_on = "T2"                  # T2 depends on T3: now a loop
        s.commit()
    report = run()
    assert report["status"] == "INVALID_GRAPH"
    (a,) = report["actions"]
    assert a["type"] == "REQUEST_CLARIFICATION" and a["recipient"] == "Aanya"       # the lead
    assert "loop" in a["text"]
    assert world.get("/api/board").status_code == 200


def test_results_for_outdated_data_are_discarded_and_recomputed(world, monkeypatch):
    real, calls = schedule.plan, []

    def racing_plan(g, now, commits=None, version=None, **kw):
        calls.append(version)
        if len(calls) == 1:                                  # the board changes while the solver is running
            with Session(db_engine) as s:
                s.get(Task, "T3").status = "Complete"
                s.commit()
        return real(g, now, commits, version, **kw)
    monkeypatch.setattr(schedule, "plan", racing_plan)
    report = run()
    assert len(calls) == 2 and calls[0] != calls[1]          # stale answer thrown away, then recomputed
    with Session(db_engine) as s:
        assert report["version"] == ProjectGraph.from_db(s).version(engine._commit_sig(planner.open_commitments(s)))
        assert "T3" not in json.loads(_latest_forecast())["forecast"]["tasks"]


def test_a_second_run_reuses_a_fresh_unchanged_forecast(world, monkeypatch):
    run()
    calls = []
    real = schedule.plan
    monkeypatch.setattr(schedule, "plan", lambda *a, **k: calls.append(1) or real(*a, **k))
    again = run(now=NOW + timedelta(minutes=1))
    assert again["reused"] is True and calls == []
    assert run(now=NOW + timedelta(minutes=engine.REUSE_MIN + 1))["reused"] is False


def test_dry_run_reports_what_would_be_said_without_contacting_anyone(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    report = run(send=False)
    assert report["actions"][0]["status"] == "dry-run" and report["actions"][0]["recipient"] == "Priya"
    assert actions() == []


# ================================================================ anti-spam

def test_repeated_unanswered_nudges_escalate_to_the_lead_once(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    sent = []
    for i in range(3):
        sent += run(now=NOW + timedelta(minutes=31 * i))["actions"]
    assert [a["recipient"] for a in sent] == ["Priya"] * 3
    esc = run(now=NOW + timedelta(minutes=31 * 3))
    assert [(a["type"], a["recipient"]) for a in esc["actions"]] == [("ESCALATE_BLOCKER", "Aanya")]
    assert "escalating" in esc["actions"][0]["text"]
    after = run(now=NOW + timedelta(minutes=31 * 4))
    assert after["actions"] == []
    assert any("already escalated" in x["reason"] for x in after["suppressed"])


def test_a_changed_situation_is_a_new_conversation_not_a_repeat(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    assert len(run()["actions"]) == 1
    with Session(db_engine) as s:
        s.get(Task, "T2").blocker_text = "the sandbox is down"
        s.commit()
    later = run(now=NOW + timedelta(minutes=settings.intel_min_gap_min + 1))
    assert len(later["actions"]) == 1                         # different blocker: worth saying, past the min gap


def test_at_most_a_few_messages_per_run(world, monkeypatch):
    monkeypatch.setattr(settings, "intel_max_actions_per_run", 1)
    with Session(db_engine) as s:
        for tid, key in (("T2", "meera"), ("T4", "priya")):
            t = s.get(Task, tid)
            t.depends_on, t.status, t.blocker_text, t.blocked_by_id = "", "Blocked", "something", member(s, key).id
        s.commit()
    report = run()
    assert len(report["actions"]) == 1
    assert any("run limit" in x["reason"] for x in report["suppressed"])


def test_completed_tasks_are_never_chased(world):
    with Session(db_engine) as s:
        s.get(Task, "T2").status = "Complete"
        s.commit()
    assert run()["actions"] == []


def test_sending_a_message_does_not_trigger_another_replan(world, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "request", lambda *a, **k: seen.append(a))
    say(world, "kabir", "stuck, waiting for test credentials")
    seen.clear()                                              # the reply itself legitimately asks for a replan
    run()
    assert seen == []                                         # but the agent's own message never does


# ================================================================ triggers and debounce

def test_only_scheduling_relevant_changes_ask_for_a_replan(world, monkeypatch):
    seen = []
    monkeypatch.setattr(engine, "request", lambda kind, tid=None: seen.append((kind, tid)))
    say(world, "kabir", "lol")
    say(world, "kabir", "which task?")
    assert seen == []
    say(world, "kabir", "stuck, waiting for test credentials")
    assert ("TASK_BLOCKED", "T2") in seen
    seen.clear()
    say(world, "kabir", "tested, all green, PR #14")
    assert ("TASK_COMPLETED", "T2") in seen


def test_a_no_op_progress_update_does_not_replan(world, monkeypatch):
    seen = []
    say(world, "meera", "working on it")                     # T4 already In Progress, nothing new in the message
    monkeypatch.setattr(engine, "request", lambda kind, tid=None: seen.append(kind))
    say(world, "meera", "working on it")
    assert seen == []


def test_a_burst_of_events_becomes_one_run(monkeypatch):
    monkeypatch.setattr(settings, "intel_autorun", True)
    monkeypatch.setattr(settings, "intel_debounce_s", 0.05)
    runs = []

    async def fake_run(trigger="", **kw):
        runs.append(trigger)
        return {}
    monkeypatch.setattr(engine, "run_once", fake_run)

    async def go():
        engine.attach(asyncio.get_running_loop())
        for kind in ("TASK_BLOCKED", "TASK_UPDATED", "TASK_BLOCKED", "BLOCKER_RESOLVED"):
            engine._on_request(kind, "T2")
        await asyncio.sleep(0.4)
        engine._on_request("TASK_COMPLETED", "T2")           # a later event is a second run
        await asyncio.sleep(0.3)
    asyncio.run(go())
    assert runs == ["BLOCKER_RESOLVED,TASK_BLOCKED,TASK_UPDATED", "TASK_COMPLETED"]


def test_request_is_safe_to_call_from_a_worker_thread(monkeypatch):
    import threading
    monkeypatch.setattr(settings, "intel_autorun", True)
    monkeypatch.setattr(settings, "intel_debounce_s", 0.01)
    monkeypatch.setattr(settings, "redis_url", "")
    runs = []

    async def fake_run(trigger="", **kw):
        runs.append(trigger)
        return {}
    monkeypatch.setattr(engine, "run_once", fake_run)

    async def go():
        engine.attach(asyncio.get_running_loop())
        t = threading.Thread(target=lambda: engine.request("TASK_BLOCKED", "T2"))
        t.start()
        t.join()
        await asyncio.sleep(0.2)
    asyncio.run(go())
    assert runs == ["TASK_BLOCKED"]


def test_autorun_off_means_no_background_work(monkeypatch):
    monkeypatch.setattr(settings, "intel_autorun", False)
    engine._triggers.clear()
    engine.request("TASK_BLOCKED", "T2")
    assert engine._triggers == []


# ================================================================ Telegram group chat

class FakeTelegram:
    name = "telegram"

    def __init__(self, fail_times=0):
        self.sent, self.fail_times = [], fail_times

    def ready(self):
        return True

    def linked(self, m):
        return False

    async def send(self, m, item):
        return None

    async def send_group(self, chat_id, html):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("Telegram unavailable")
        self.sent.append((chat_id, html))
        return str(5000 + len(self.sent))


@pytest.fixture()
def tg(world, monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setitem(channels.REGISTRY, "telegram", fake)
    with Session(db_engine) as s:
        s.add(Setting(key="tg_group_chat_id", value="-100123"))
        member(s, "priya").tg_user_id = 777
        s.commit()
    return fake


def test_the_message_goes_to_the_group_with_a_real_mention(world, tg):
    say(world, "kabir", "stuck, waiting for test credentials")
    (a,) = run()["actions"]
    (chat, html) = tg.sent[0]
    assert chat == -100123
    assert '<a href="tg://user?id=777">Priya</a>' in html          # a mention that actually notifies her
    assert "Kabir" in html and "test credentials" in html
    (row,) = actions()
    assert row.channel == "telegram" and row.ext_id == "5001" and a["channel"] == "telegram"


def test_a_group_id_in_the_environment_wins_and_a_missing_one_falls_back_to_dm(world, monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setitem(channels.REGISTRY, "telegram", fake)
    say(world, "kabir", "stuck, waiting for test credentials")
    (a,) = run()["actions"]
    assert fake.sent == [] and a["channel"] != "telegram"            # no group configured: delivered per person instead
    monkeypatch.setattr(settings, "telegram_group_chat_id", "-42")
    with Session(db_engine) as s:
        assert channels.group_chat_id(s) == -42


def test_a_reply_to_the_agents_message_binds_to_that_action(world, tg):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert reply("priya", "in 30 minutes", reply_to="5001") == []
    assert actions()[0].status == "committed"
    assert len(tg.sent) == 2 and "noted" in tg.sent[1][1]               # the acknowledgement was posted to the group


def test_someone_elses_reply_does_not_commit_for_the_person_who_was_asked(world, tg):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert reply("meera", "in 30 minutes", reply_to="5001") is None
    assert actions()[0].status == "sent"


def test_a_plain_message_binds_only_when_it_is_clearly_a_time_or_a_delivery(world, tg):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert reply("priya", "lol nice") is None
    assert reply("priya", "can someone review my PR?") is None
    assert actions()[0].status == "sent"
    assert reply("priya", "in 30 minutes") == []
    assert actions()[0].status == "committed"


def test_two_open_requests_make_a_bare_reply_ambiguous(world, tg):
    with Session(db_engine) as s:
        for tid in ("T2", "T4"):
            t = s.get(Task, tid)
            t.depends_on, t.status, t.blocker_text, t.blocked_by_id = "", "Blocked", "creds", member(s, "priya").id
        s.commit()
    monkeypatch_cap = settings.intel_max_actions_per_run
    settings.intel_max_actions_per_run = 5
    try:
        run()
    finally:
        settings.intel_max_actions_per_run = monkeypatch_cap
    assert len(actions(recipient_id=_id("priya"))) == 2
    assert reply("priya", "in 30 minutes") is None                      # which one? do not guess
    assert {a.status for a in actions()} == {"sent"}


def _id(key):
    with Session(db_engine) as s:
        return member(s, key).id


def test_an_ambiguous_direct_reply_asks_for_a_time(world, tg):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    out = reply("priya", "soon", reply_to="5001")
    assert out and "When do you expect" in out[0]["text"]
    assert actions()[0].status == "sent"


def test_telegram_outage_is_recorded_and_retried_on_the_next_run(world, tg):
    tg.fail_times = 1
    say(world, "kabir", "stuck, waiting for test credentials")
    (a,) = run()["actions"]
    assert a["status"] == "failed" and actions()[0].status == "failed"
    again = run(now=NOW + timedelta(minutes=1))                          # a failed send does not count against the cooldown
    assert again["actions"] and again["actions"][0]["status"] == "sent"
    assert [x.status for x in actions()] == ["failed", "sent"]


def test_the_first_group_a_member_speaks_in_is_remembered():
    from types import SimpleNamespace

    from telegram.constants import ChatType

    from app.channels import telegram as tgmod
    with TestClient(app) as c:
        c.post("/api/reset")
    with Session(db_engine) as s:
        for row in s.exec(select(Setting)).all():
            s.delete(row)
        s.commit()
        tgmod._remember_group(s, SimpleNamespace(type=ChatType.PRIVATE, id=1))
        assert s.get(Setting, "tg_group_chat_id") is None                # DMs are not the project group
        tgmod._remember_group(s, SimpleNamespace(type=ChatType.SUPERGROUP, id=-555))
        tgmod._remember_group(s, SimpleNamespace(type=ChatType.GROUP, id=-999))   # a second group does not replace it
        assert s.get(Setting, "tg_group_chat_id").value == "-555"
        s.delete(s.get(Setting, "tg_group_chat_id"))
        s.commit()


# ================================================================ API and graph mirror

def test_api_forecast_graph_and_actions(world):
    assert world.get("/api/intel/forecast").json()["status"] == "NONE"
    say(world, "kabir", "stuck, waiting for test credentials")
    r = world.post("/api/intel/replan").json()
    assert r["status"] == "OPTIMAL" and len(r["actions"]) == 1

    f = world.get("/api/intel/forecast").json()
    assert f["status"] == "OPTIMAL" and f["solver"] == "cp-sat" and f["version"] == r["version"]
    ids = [t["id"] for t in f["tasks"]]
    assert "T1" not in ids and {"T2", "T3", "T4", "T5"} <= set(ids)       # the finished task is not scheduled
    assert all(t["priority"] is not None for t in f["tasks"])
    assert f["analysis"]["blocked"] == ["T2"] and f["analysis"]["affected"]["T2"] == ["T4", "T5"]
    assert f["risks"][0]["risk_type"] == "CONFIRMED_BLOCKER"

    g = world.get("/api/intel/graph").json()
    assert {"source": "T3", "target": "T2"} in g["edges"]                 # prerequisite -> dependent
    assert next(n for n in g["nodes"] if n["id"] == "T2")["blocked"] is True

    acts = world.get("/api/intel/actions").json()
    assert acts[0]["recipient"] == "Priya" and acts[0]["task"] == "Connect the payment API" and acts[0]["evidence"]
    assert acts[0]["text"].startswith("@Priya") and "{M" not in acts[0]["text"]
    assert "<b>" not in acts[0]["text"] and "<i>" not in acts[0]["text"]       # plain text: the dashboard does not render HTML


def test_api_dry_run_does_not_send(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    r = world.post("/api/intel/replan?send=false").json()
    assert r["actions"][0]["status"] == "dry-run"
    assert world.get("/api/intel/actions").json() == []


def test_reset_clears_actions_and_forecasts(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    world.post("/api/reset")
    assert actions() == [] and world.get("/api/intel/forecast").json()["status"] == "NONE"


class FakeDriver:
    def __init__(self):
        self.calls = []

    def session(self):
        outer = self

        class S:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def run(self, stmt, **params):
                outer.calls.append((stmt, params))
        return S()


def test_neo4j_mirror_writes_dependencies_in_the_documented_direction(world):
    with Session(db_engine) as s:
        g = ProjectGraph.from_db(s)
    drv = FakeDriver()
    n = neo4j_mirror.sync(g, schedule.plan(g, NOW), driver=drv)
    assert n == len(g.tasks)
    deps = [p for stmt, p in drv.calls if stmt == neo4j_mirror.ADD_DEP]
    assert {"task": "T2", "dep": "T3"} in deps and {"task": "T4", "dep": "T2"} in deps     # (T2)-[:DEPENDS_ON]->(T3)
    assert "(b)-[:DEPENDS_ON]->(a)" in neo4j_mirror.ADD_DEP
    assert any("CONSTRAINT task_id" in stmt for stmt, _ in drv.calls)
    assert "<-[:DEPENDS_ON*1..]-" in neo4j_mirror.DOWNSTREAM            # downstream walks the arrow backwards


# ================================================================ Slack alert feed

class FakeSlack:
    name = "slack"

    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def ready(self):
        return True

    def linked(self, m):
        return bool(m.slack_dm_id)

    async def send(self, m, item):
        return "1700000000.000100"

    async def post(self, channel, text, blocks, thread_ts=None):
        if self.fail:
            raise RuntimeError("slack down")
        self.posts.append((channel, text))


def link_on_slack(*keys):
    with Session(db_engine) as s:
        for k in keys:
            member(s, k).slack_dm_id = "D-" + k
        s.commit()


def test_escalations_and_failed_sends_reach_the_slack_alert_channel(world, monkeypatch):
    link_on_slack("priya", "aanya")                            # the person asked, and the lead who gets the escalation
    slack = FakeSlack()
    monkeypatch.setitem(channels.REGISTRY, "slack", slack)
    monkeypatch.setattr(settings, "slack_alert_channel", "C-ALERTS")
    say(world, "kabir", "stuck, waiting for test credentials")
    for i in range(3):
        run(now=NOW + timedelta(minutes=31 * i))
    assert slack.posts == []                                   # ordinary nudges stay out of the alert feed
    run(now=NOW + timedelta(minutes=93))                       # the escalation
    assert len(slack.posts) == 1
    channel, text = slack.posts[0]
    assert channel == "C-ALERTS" and "Escalated to the lead" in text and "Connect the payment API" in text


def test_no_alert_channel_configured_means_no_slack_traffic(world, monkeypatch):
    slack = FakeSlack()
    monkeypatch.setitem(channels.REGISTRY, "slack", slack)
    monkeypatch.setattr(settings, "slack_alert_channel", "")
    say(world, "kabir", "stuck, waiting for test credentials")
    for i in range(4):
        run(now=NOW + timedelta(minutes=31 * i))
    assert slack.posts == []


def test_a_broken_alert_feed_does_not_stop_the_loop(world, monkeypatch):
    link_on_slack("priya", "aanya")
    monkeypatch.setitem(channels.REGISTRY, "slack", FakeSlack(fail=True))
    monkeypatch.setattr(settings, "slack_alert_channel", "C-ALERTS")
    say(world, "kabir", "stuck, waiting for test credentials")
    for i in range(3):
        run(now=NOW + timedelta(minutes=31 * i))
    esc = run(now=NOW + timedelta(minutes=93))
    assert esc["actions"][0]["type"] == "ESCALATE_BLOCKER" and esc["actions"][0]["status"] == "sent"


# ================================================================ people who have not connected any app

def test_a_connected_app_but_an_unlinked_recipient_is_unreachable_not_redirected(world, monkeypatch):
    class Ready(FakeTelegram):
        pass
    monkeypatch.setitem(channels.REGISTRY, "telegram", Ready())          # an app is up, but nobody is linked to it
    say(world, "kabir", "stuck, waiting for test credentials")
    report = run()
    (a,) = report["actions"]
    assert a["recipient"] == "Priya" and a["status"] == "unreachable" and a["channel"] == "unreachable"
    with Session(db_engine) as s:                                          # nobody else received the message meant for her
        assert s.exec(select(Message).where(Message.kind == "agent_action", Message.channel == "dev")).first() is None
        row = s.exec(select(Message).where(Message.kind == "agent_action")).first()
        assert row.channel == "unreachable" and row.member_id == _id("priya")


def test_an_unreachable_person_is_not_retried_every_minute(world, monkeypatch):
    monkeypatch.setitem(channels.REGISTRY, "telegram", FakeTelegram())
    say(world, "kabir", "stuck, waiting for test credentials")
    assert run()["actions"][0]["status"] == "unreachable"
    again = run(now=NOW + timedelta(minutes=settings.intel_min_gap_min + 5))
    assert again["actions"] == [] and any("cooldown" in x["reason"] for x in again["suppressed"])


def test_unreachable_teammates_are_reported_to_the_slack_alert_feed(world, monkeypatch):
    slack = FakeSlack()
    monkeypatch.setitem(channels.REGISTRY, "slack", slack)
    monkeypatch.setattr(settings, "slack_alert_channel", "C-ALERTS")
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert len(slack.posts) == 1 and "Could not reach a teammate" in slack.posts[0][1]


def test_the_person_asked_always_comes_before_the_task_owner(world, monkeypatch):
    seen = []

    async def spy(s, text, task_id=None, kind="agent_action", recipient_ids=None):
        seen.append(recipient_ids)
        return None, "sent"
    monkeypatch.setattr(channels, "deliver_group", spy)
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert seen[0][0] == _id("priya") and _id("kabir") in seen[0]         # Priya (asked) first, Kabir (owner) second


# ================================================================ answering the agent in a direct message

def dm_as(key, text, reply_to=None):
    from app import service
    with Session(db_engine) as s:
        me = member(s, key)
        return run_async(service.handle_dm(s, me, text, channel="telegram", reply_to=reply_to))


def run_async(coro):
    return asyncio.run(coro)


def test_a_dm_reply_with_a_time_is_recorded_as_a_promise(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert dm_as("priya", "I will send them in 20 minutes") == []
    (a,) = actions()
    assert a.status == "committed" and a.commitment_at is not None
    assert task(world, "T2")["status"] == "Blocked"                       # a promise is not a delivery


def test_a_dm_delivery_claim_resolves_the_blocker_and_asks_the_owner(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    out = dm_as("priya", "sent them on email")
    assert any("Is it done now?" in o["text"] for o in out)
    assert task(world, "T2")["blocker_resolved"] and [a.status for a in actions()] == ["resolved"]


def test_a_dm_that_is_not_an_answer_goes_through_the_normal_pipeline(world):
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    dm_as("priya", "working on it")                                        # no time, no delivery: not an answer
    assert actions()[0].status == "sent"
    assert task(world, "T3")["status"] == "In Progress"


def test_a_dm_without_any_open_request_is_untouched(world):
    dm_as("priya", "in 20 minutes")
    assert actions() == []


def test_an_action_is_pending_until_it_has_really_been_delivered(world, monkeypatch):
    seen = []
    real = channels.deliver_group

    async def spy(s, text, **kw):
        with Session(db_engine) as other:                                   # what anyone reading the log sees mid-delivery
            seen.append([a.status for a in other.exec(select(AgentAction)).all()])
        return await real(s, text, **kw)
    monkeypatch.setattr(channels, "deliver_group", spy)
    say(world, "kabir", "stuck, waiting for test credentials")
    run()
    assert seen == [["pending"]]
    assert [a.status for a in actions()] in (["sent"], ["unreachable"])     # dev mode: delivered to the dev log
