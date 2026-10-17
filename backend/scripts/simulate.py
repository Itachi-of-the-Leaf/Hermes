"""Drive the RUNNING system through 10 end-to-end scenarios and check every outcome.

    cd backend && python scripts/simulate.py            # all scenarios against http://localhost:8000
    python scripts/simulate.py 3 4                      # just scenarios 3 and 4
    python scripts/simulate.py --public                 # send the Slack events through PUBLIC_BASE_URL (ngrok)

What is real: the backend, SQLite, CP-SAT, Jev, the LLM, Neo4j, Slack (events are signed with your signing secret, exactly
like Slack signs them, and the bot's replies arrive in the Slack DM of the person the events are sent as).
What is simulated: other teammates (they have not connected an app, so they speak through the dev endpoint and any
message addressed to them is recorded as `unreachable`, never delivered to someone else), and Telegram users.

WARNING: every scenario starts with POST /api/reset, which wipes tasks and history. Do not run it against a live project.
"""
import hashlib
import hmac
import json
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from app.config import settings  # noqa: E402

LOCAL = "http://localhost:8000"
API = LOCAL + "/api"
USE_PUBLIC = "--public" in sys.argv
SLACK_URL = (settings.public_base_url.rstrip("/") if USE_PUBLIC else LOCAL) + "/api/slack/events"
ME = {"slack_id": "U0C9RDU7VTP", "key": "tejas"}           # the workspace owner; replies land in this person's DM

http = httpx.Client(timeout=60)
results: list[tuple[int, str, bool, str]] = []
_current = 0


# ------------------------------------------------------------------ helpers
def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((_current, name, bool(ok), detail))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
    return bool(ok)


def get(path):
    return http.get(API + path).json()


def post(path, body=None, **kw):
    return http.post(API + path, json=body if body is not None else {}, **kw)


def wait_for(fn, timeout=25, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:
            pass
        time.sleep(every)
    return None


def board():
    return get("/board")


def task(tid):
    return next((t for t in board()["tasks"] if t["id"] == tid), None)


def say(key, text, group=False):
    return post("/dev/message", {"member_key": key, "text": text, "group": group}).json()


def new_task(title, owner, deadline=None, est=None, deps=()):
    r = post("/tasks", {"title": title, "owner_key": owner, "deadline": deadline, "est_minutes": est, "depends_on": list(deps)})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def reset():
    post("/reset")
    wait_for(lambda: board()["tasks"] == [], 10)
    time.sleep(3)                                              # let the replan + Neo4j mirror catch up


def actions():
    return get("/intel/actions")


def network():
    return get("/intel/network")


def neo_tasks_match(expected: int, timeout=25):
    def ok():
        n = network()
        return n["source"] == "neo4j" and sum(1 for x in n["nodes"] if x["type"] == "Task") == expected
    return wait_for(ok, timeout)


# ------------------------------------------------------------------ Slack (signed events in, bot replies read back)
_slack = None


def slack():
    global _slack
    if _slack is None:
        from slack_sdk import WebClient
        c = WebClient(token=settings.slack_bot_token)
        auth = c.auth_test()
        dm = c.conversations_open(users=ME["slack_id"])["channel"]["id"]
        _slack = (c, auth, dm)
    return _slack


def slack_mark():
    """Slack's own timestamp of the newest message in the DM. Cutoffs must come from Slack's clock, not this machine's:
    the bot answers some messages in under a second, and a local clock that is a second ahead would hide the reply."""
    c, _, dm = slack()
    msgs = c.conversations_history(channel=dm, limit=1)["messages"]
    return msgs[0]["ts"] if msgs else "0"


def slack_event(text, channel_kind="im"):
    """Send a Slack-signed message event as ME. Returns the cutoff (Slack time) to read the bot's replies after."""
    c, auth, dm = slack()
    mark = slack_mark()
    ts = f"{time.time():.6f}"
    chan = dm if channel_kind == "im" else "C0SIMCHANNEL"
    body = json.dumps({
        "token": "unused", "team_id": auth["team_id"], "api_app_id": auth.get("app_id", "A0"), "type": "event_callback",
        "event_id": "Ev" + uuid.uuid4().hex[:10].upper(), "event_time": int(time.time()),
        "authorizations": [{"enterprise_id": None, "team_id": auth["team_id"], "user_id": auth["user_id"], "is_bot": True}],
        "event": {"type": "message", "user": ME["slack_id"], "text": text, "ts": ts, "channel": chan,
                  "channel_type": channel_kind if channel_kind == "im" else "channel", "event_ts": ts}})
    stamp = str(int(time.time()))
    sig = "v0=" + hmac.new(settings.slack_signing_secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    r = http.post(SLACK_URL, content=body, headers={"Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": sig})
    assert r.status_code == 200, f"Slack endpoint answered {r.status_code}"
    return mark


def bot_dms(since_ts, want=1, timeout=25):
    """Texts the bot posted in the DM after `since_ts` (waits until at least `want` have arrived)."""
    c, _, dm = slack()

    def fetch():
        msgs = c.conversations_history(channel=dm, oldest=since_ts, limit=30)["messages"]
        texts = [m.get("text", "") for m in msgs if m.get("bot_id")]
        return texts if len(texts) >= want else None
    return wait_for(fetch, timeout) or []


def slack_signed_raw(body, secret, ts=None, bad=False):
    stamp = str(ts if ts is not None else int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:{body}".encode(), hashlib.sha256).hexdigest()
    if bad:
        sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    return {"Content-Type": "application/json", "X-Slack-Request-Timestamp": stamp, "X-Slack-Signature": sig}


# ================================================================== the scenarios
def s1_empty_project():
    """A brand-new project: no tasks, the real people, every page loads, the graph shows project + people."""
    reset()
    b = board()
    check("the project starts with no tasks", b["tasks"] == [])
    check("the real team is there", [m["name"] for m in b["members"]] == ["Tejas", "Soham", "Samarth", "Atharva"], str([m["name"] for m in b["members"]]))
    check("no placeholder names anywhere", not any(n in json.dumps(b) for n in ("Aanya", "Kabir", "Meera", "Priya")))
    codes = {p: http.get(API + p).status_code for p in ("/board", "/activity", "/reviews", "/team", "/intel/forecast", "/intel/graph", "/intel/actions", "/intel/network", "/health", "/ready")}
    check("every dashboard endpoint answers on an empty project", all(c == 200 for c in codes.values()), str(codes))
    check("check-ins on an empty project send nothing", post("/checkin").json() == {"sent": 0})
    n = wait_for(lambda: (x := network())["source"] == "neo4j" and sum(1 for k in x["nodes"] if k["type"] == "Person") == 4 and x, 25) or network()
    types = {}
    for node in n["nodes"]:
        types[node["type"]] = types.get(node["type"], 0) + 1
    check("the graph database holds the project and the 4 people", n["source"] == "neo4j" and types == {"Project": 1, "Person": 4}, f"{n['source']} {types}")
    f = get("/intel/forecast")
    check("an empty project has no forecast and no risks", f["status"] in ("NONE", "NO_WORK") and f["risks"] == [], f["status"])


def s2_create_tasks_everywhere():
    """Tasks come from the dashboard and from Slack; bad commands are explained, never guessed."""
    reset()
    a = new_task("Set up the repository", "soham", deadline=(date.today() + timedelta(days=5)).isoformat(), est=120)
    b = new_task("Build the login page", "samarth", est=180, deps=[a])
    check("dashboard creates tasks with owner, estimate and a prerequisite", task(b)["depends_on_titles"] == ["Set up the repository"] and task(a)["owner_key"] == "soham")
    ts = slack_event("task: Write the user guide @atharva due friday est 90m after T1")
    reply = " ".join(bot_dms(ts, 1))
    check("Slack `task:` creates it and confirms in the DM", "Created T3" in reply and "Atharva" in reply, reply[:120])
    t3 = wait_for(lambda: task("T3"))
    check("the Slack task has the right owner, estimate and prerequisite", bool(t3) and t3["owner_key"] == "atharva" and t3["depends_on_titles"] == ["Set up the repository"])
    before = len(board()["tasks"])
    ts = slack_event("task: Fix it @nobody due blah after T99")
    reply = " ".join(bot_dms(ts, 1))
    check("a bad Slack command is explained and creates nothing", "did not create" in reply and len(board()["tasks"]) == before, reply[:160])
    bad = [post("/tasks", x).status_code for x in ({"title": "ab", "owner_key": "soham"}, {"title": "Fine title", "owner_key": "ghost"},
                                                   {"title": "Fine title", "owner_key": "soham", "depends_on": ["T99"]},
                                                   {"title": "Fine title", "owner_key": "soham", "deadline": "soon"})]
    check("the dashboard API refuses bad tasks", bad == [400, 400, 400, 400], str(bad))
    check("all 3 tasks reached the graph database with their dependencies",
          bool(neo_tasks_match(3)) and sum(1 for e in network()["edges"] if e["type"] == "DEPENDS_ON") == 2)


def s3_blocker_in_slack():
    """A blocker reported in Slack flows to the board, forecast, risk engine, graph and the agent."""
    reset()
    t1 = new_task("Get the API keys", "soham", est=60)
    t2 = new_task("Connect the payment API", "tejas", deadline=(date.today() + timedelta(days=1)).isoformat(), est=120, deps=[t1])
    ts = slack_event("I'm blocked on the payment API, waiting for the API keys")
    reply = " ".join(bot_dms(ts, 1))
    check("the bot confirms the blocker in Slack", "Blocked" in reply, reply[:140])
    t = wait_for(lambda: (x := task(t2)) and x["status"] == "Blocked" and x)
    check("the task is Blocked with the blocker recorded and Soham named as the person to ask", bool(t) and "keys" in (t["blocker_text"] or "") and t["blocked_by"] == "Soham", str(t and (t["blocker_text"], t["blocked_by"])))
    f = wait_for(lambda: (x := get("/intel/forecast"))["status"] == "OPTIMAL" and any(r["risk_type"] == "CONFIRMED_BLOCKER" for r in x["risks"]) and x)
    check("CP-SAT produced a forecast and the risk engine flagged a confirmed blocker", bool(f), str(get("/intel/forecast")["status"]))
    a = wait_for(lambda: [x for x in actions() if x["task_id"] == t2])
    check("the agent asked Soham for an ETA, with its evidence", bool(a) and a[0]["type"] == "REQUEST_ETA" and a[0]["recipient"] == "Soham" and len(a[0]["evidence"]) >= 2, str(a and a[0]["type"]))
    check("Soham is not connected to any app, so it is marked unreachable instead of going to someone else", bool(a) and a[0]["status"] == "unreachable")
    n = wait_for(lambda: next((x for x in network()["nodes"] if x["id"] == f"Task:{t2}" and x.get("status") == "Blocked"), None))
    check("the graph database shows the task Blocked", bool(n))


def s4_promise_and_delivery():
    """The full follow-up loop with a reachable person: ask, promise, wait, deliver, confirm."""
    reset()
    t1 = new_task("Provide the staging access", "tejas", est=30)
    t2 = new_task("Deploy the app", "soham", deadline=(date.today() + timedelta(days=2)).isoformat(), est=60, deps=[t1])
    since = slack_mark()
    say("soham", "I am blocked on deploying, waiting for the staging access")
    t = wait_for(lambda: (x := task(t2)) and x["status"] == "Blocked" and x)
    check("Soham's blocker names Tejas as the person who can unblock it", bool(t) and t["blocked_by"] == "Tejas", str(t and t["blocked_by"]))
    asked = bot_dms(since, 1, 30)
    check("the agent really asked Tejas in his Slack DM", any("Soham" in m and ("staging" in m.lower() or "Deploy" in m) for m in asked), str(asked)[:200])
    a = wait_for(lambda: [x for x in actions() if x["task_id"] == t2 and x["recipient"] == "Tejas" and x["status"] != "pending"])
    check("that request is recorded as sent via Slack", bool(a) and a[0]["status"] == "sent" and a[0]["channel"] == "slack", str(a and (a[0]["status"], a[0]["channel"])))
    ts = slack_event("I'll send it in 30 minutes")
    ack = bot_dms(ts, 0, 3)
    a = wait_for(lambda: [x for x in actions() if x["task_id"] == t2 and x["status"] == "committed"])
    check("his reply 'in 30 minutes' is recorded as a promise, not a delivery", bool(a) and a[0]["commitment_at"] is not None and not task(t2)["blocker_resolved"] and task(t2)["status"] == "Blocked")
    r = post("/intel/replan?send=false").json()
    check("the agent does not nag while the promise is pending", r["actions"] == [], str(r["actions"])[:120])
    f = get("/intel/forecast")
    check("the forecast uses the promised time", any("unverified" in x or "promised" in x for x in f["assumptions"]) or f["status"] == "OPTIMAL", str(f["assumptions"])[:120])
    ts = slack_event("sent them just now")
    wait_for(lambda: task(t2)["blocker_resolved"])
    check("'sent them' resolves the blocker and closes the request", task(t2)["blocker_resolved"] and any(x["status"] == "resolved" for x in actions() if x["task_id"] == t2))
    act = get("/activity")
    check("Soham is asked to confirm he can resume", any(x["type"] == "message" and x.get("who") == "Soham" and "Is it done now?" in (x["text"] or "") for x in act) or any("sorted" in (x["text"] or "") for x in act))
    ack  # noqa: B018


def s5_deadline_risk_and_recovery():
    """A chain that cannot fit before the deadline is detected, attributed along its dependency path, and improves when work finishes."""
    reset()
    now = datetime.now()
    left = max(60, int((datetime.combine(date.today(), dtime(23, 59)) - now).total_seconds() // 60))
    today = date.today().isoformat()
    t1 = new_task("Draft the specification", "soham", deadline=today, est=left // 2 + 120)
    t2 = new_task("Review the specification", "samarth", deadline=today, est=left // 2 + 120, deps=[t1])
    new_task("Publish the specification", "atharva", deadline=today, est=30, deps=[t2])
    f = wait_for(lambda: (x := get("/intel/forecast"))["status"] == "OPTIMAL" and x["total_late_min"] > 0 and x)
    check("CP-SAT finds that the chain misses its deadline", bool(f), str(get("/intel/forecast").get("total_late_min")))
    risk = next((r for r in (f or {}).get("risks", []) if r["risk_type"] == "DEADLINE_RISK"), None)
    check("a deadline risk is raised with a dependency-path explanation", bool(risk) and any("dependency path" in e for e in risk["evidence"]), str(risk and risk["evidence"])[:160])
    check("nothing is claimed about the cause that the evidence does not support", bool(risk) and risk["attribution"] in ("direct", "possible", "none"))
    late_before = f["total_late_min"] if f else 0
    say("soham", "finished, tested, all green, PR #21")
    f2 = wait_for(lambda: (x := get("/intel/forecast")) and x["total_late_min"] < late_before and x)
    check("finishing the first task lowers the forecast lateness", bool(f2), f"{late_before} -> {get('/intel/forecast')['total_late_min']}")
    check("the finished task leaves the schedule", t1 not in [x["id"] for x in get("/intel/forecast")["tasks"]])
    p = get("/intel/forecast")["analysis"].get("priority", {})
    check("priorities are scored 0-100 for every open task", len(p) == 2 and all(0 <= v["score"] <= 100 for v in p.values()), str(p)[:100])


def s6_belief_conflict_in_channel():
    """Someone in the project channel believes something the board does not: the agent catches it."""
    reset()
    t = new_task("Build the checkout page", "samarth", est=120)
    ts = slack_event("the checkout page is done, starting the demo video now", channel_kind="channel")
    reply = " ".join(bot_dms(ts, 1))
    check("the speaker gets a 'quick check' in Slack", "Quick check" in reply and "Build the checkout page" in reply, reply[:140])
    act = wait_for(lambda: [x for x in get("/activity") if x.get("kind") == "conflict"])
    check("the conflict shows in the Activity feed", bool(act))
    check("the owner is asked whether the board is out of date", any(x.get("who") == "Samarth" and "Is the board out of date" in (x["text"] or "") for x in get("/activity")))
    check("a claim does not change the board", task(t)["status"] == "Pending")


def s7_unsure_reply_review_override_undo():
    """A hedged 'done' never completes a task; the lead decides what is uncertain; every change can be undone."""
    reset()
    t = new_task("Prepare the demo script", "soham", est=60)
    say("soham", "done I think but not totally sure")
    time.sleep(4)
    check("a hedged 'done' never marks the task Complete", task(t)["status"] != "Complete", task(t)["status"])
    held = get("/reviews")
    check("it is either held for the lead or recorded as in progress, never silently accepted as done", bool(held) or task(t)["status"] in ("Pending", "In Progress"))
    ev = [i for i in get(f"/tasks/{t}/timeline")["items"] if i["type"] == "event" and not i["reverted"]]
    if ev:                                                     # the model recorded progress by itself: it must be undoable
        post(f"/events/{ev[-1]['id']}/undo")
        check("an automatic change is undone with one call", task(t)["status"] == "Pending", task(t)["status"])
    else:
        check("nothing changed while it waits for the lead", task(t)["status"] == "Pending")

    # the lead-review flow, through a meeting (status heard in a meeting always goes to the lead first)
    reset()
    t = new_task("Prepare the demo script", "soham", est=60)
    since = slack_mark()
    mid = http.post(API + "/meetings/upload", data={"title": "Review test", "text": "Soham: I am stuck on the demo script, waiting for the staging keys from Atharva."}).json()["id"]
    wait_for(lambda: get(f"/meetings/{mid}")["status"] == "done", 90, 2)
    rv = wait_for(lambda: get("/reviews"))
    check("status heard in a meeting is held for the lead and the board is untouched", bool(rv) and len(rv) == 1 and task(t)["status"] == "Pending")
    if not rv:
        return
    asked = bot_dms(since, 1, 40)
    check("the lead (Tejas) is asked in Slack", any("Please confirm" in m for m in asked), str(asked)[:160])
    post(f"/reviews/{rv[0]['id']}/resolve", {"verdict": "In Progress"})
    check("the lead's override is applied and the review is closed", task(t)["status"] == "In Progress" and get("/reviews") == [])
    ev = [i for i in get(f"/tasks/{t}/timeline")["items"] if i["type"] == "event" and not i["reverted"]][-1]
    check("the change records the lead as the decider", ev["engine"] == "lead", ev["engine"])
    post(f"/events/{ev['id']}/undo")
    check("undo restores the previous status", task(t)["status"] == "Pending")
    mid = http.post(API + "/meetings/upload", data={"title": "Review test 2", "text": "Soham: the demo script is blocked, still waiting for the staging keys."}).json()["id"]
    wait_for(lambda: get(f"/meetings/{mid}")["status"] == "done", 90, 2)
    rv = wait_for(lambda: get("/reviews"))
    if rv:
        post(f"/reviews/{rv[0]['id']}/resolve", {"verdict": "approve"})
    check("approving takes the agent's own proposal", bool(rv) and task(t)["status"] == "Blocked" and get("/reviews") == [], str(task(t)["status"]))
    check("resolving or undoing something that does not exist is a clean 404", post("/reviews/99999/resolve", {"verdict": "approve"}).status_code == 404 and post("/events/99999/undo").status_code == 404)


def s8_meeting_transcript():
    """A meeting becomes status signals for the lead, a summary, and action items; chatter is filtered out."""
    reset()
    new_task("Connect the payment API", "soham", est=120)
    new_task("Get sandbox test credentials", "atharva", est=45)
    new_task("Record the demo video", "tejas", est=60)
    text = ("Soham: The payment API is connected, but I'm still waiting for the sandbox test credentials from Atharva.\n"
            "Atharva: I'll send the sandbox credentials tonight.\n"
            "Tejas: Let's cut the refund flow from the demo scope.\n"
            "Tejas: I'll record the demo video on Saturday.\n"
            "Samarth: Did anyone catch the cricket score?")
    r = http.post(API + "/meetings/upload", data={"title": "Simulated sync", "text": text}).json()
    m = wait_for(lambda: (x := get(f"/meetings/{r['id']}"))["status"] == "done" and x, 90, 2)
    check("the transcript is analysed to completion", bool(m), str(get(f"/meetings/{r['id']}")["status"]))
    if not m:
        return
    check("Jev triaged every line", m["stats"]["triage_engine"] == "jev", str(m["stats"]))
    chatter = [u for u in m["utterances"] if u["text"].startswith("Did anyone catch")]
    check("the off-topic question is filtered out", len(chatter) == 1 and chatter[0]["flagged"] is False, str([(u["text"][:20], u["flagged"]) for u in m["utterances"]]))
    check("the promises and decisions are kept", all(u["flagged"] for u in m["utterances"] if u["speaker"] != "Samarth"))
    s = m["summary"] or {}
    check("a summary with a headline is written", bool(s.get("headline")), m["stats"].get("summary_engine", ""))
    check("Soham's blocker is captured", any("credentials" in (b.get("text") or "").lower() for b in s.get("blockers", [])))
    rv = get("/reviews")
    check("status heard in a meeting waits for the lead; only the real blocker does, promises do not", [(x["member"], x["proposal"]["status"]) for x in rv] == [("Soham", "Blocked")], str([(x["member"], x["proposal"]["status"]) for x in rv]))
    check("nothing on the board changed by itself", all(t["status"] == "Pending" for t in board()["tasks"]))


def s9_anti_spam_and_dry_run():
    """Repeated planning never repeats a message; a dry run sends nothing."""
    reset()
    t1 = new_task("Get the staging keys", "soham", est=60)
    t2 = new_task("Deploy to staging", "samarth", est=60, deps=[t1])
    say("samarth", "stuck, waiting for the staging keys")
    wait_for(lambda: [x for x in actions() if x["task_id"] == t2])
    first = len(actions())
    runs = [post("/intel/replan").json() for _ in range(5)]
    check("five more replans send nothing new", all(r["actions"] == [] for r in runs) and len(actions()) == first, f"{first} -> {len(actions())}")
    why = {x["reason"].split(" (")[0] for r in runs for x in r["suppressed"]}
    check("each suppression says why", bool(why) and all(w for w in why), str(why))
    d = post("/intel/replan?send=false").json()
    check("a dry run sends nothing and records nothing", d["actions"] == [] and len(actions()) == first)
    check("only one request exists for the situation", len([x for x in actions() if x["task_id"] == t2]) == 1)
    c = post("/checkin").json()
    check("'Send check-ins' reaches only people who are connected (others are recorded, not messaged)", isinstance(c["sent"], int) and c["sent"] >= 2, str(c))


def s10_consistency_security_ui():
    """The graph database matches the project, deletion cleans up, the endpoints reject bad input, and the UI shows it."""
    reset()
    a = new_task("Collect requirements", "tejas", est=60)
    b = new_task("Write the design", "soham", est=120, deps=[a])
    c = new_task("Build the prototype", "samarth", est=240, deps=[b])
    check("the graph database mirrors the project (3 tasks, 3 people owning them, 2 dependencies)",
          bool(neo_tasks_match(3)) and sum(1 for e in network()["edges"] if e["type"] == "DEPENDS_ON") == 2 and sum(1 for e in network()["edges"] if e["type"] == "OWNS") == 3)
    check("deleting a task removes it from the board and unhooks what waited for it", post(f"/tasks/{b}").status_code in (404, 405) and http.delete(API + f"/tasks/{b}").status_code == 200
          and task(c)["depends_on_titles"] == [] and task(b) is None)
    check("and from the graph database", bool(neo_tasks_match(2)) and not any(x["id"] == f"Task:{b}" for x in network()["nodes"]))
    check("unknown ids are clean 404s", http.delete(API + "/tasks/T999").status_code == 404 and get("/tasks/T999/timeline").get("detail") == "Not Found")
    body = '{"type":"url_verification","challenge":"c1"}'
    ok = http.post(SLACK_URL, content=body, headers=slack_signed_raw(body, settings.slack_signing_secret))
    check("Slack's URL handshake succeeds with the real signing secret", ok.status_code == 200 and "c1" in ok.text)
    check("a forged Slack signature is rejected", http.post(SLACK_URL, content=body, headers=slack_signed_raw(body, settings.slack_signing_secret, bad=True)).status_code == 401)
    check("a replayed (old) Slack request is rejected", http.post(SLACK_URL, content=body, headers=slack_signed_raw(body, settings.slack_signing_secret, ts=int(time.time()) - 7200)).status_code == 401)
    check("the meeting webhook rejects a wrong token", http.post(API + "/meetings/webhooks/recall?token=wrong&meeting_id=1", json={}).status_code == 401)
    h = get("/health")
    check("health reports every integration that is configured", all(h[k] for k in ("telegram", "slack", "jev", "llm", "neo4j")) and h["env"] == "dev", str({k: h[k] for k in ("telegram", "slack", "jev", "llm", "neo4j")}))
    tg = subprocess.run([sys.executable, "-m", "app.cli", "telegram-check"], cwd=BACKEND, capture_output=True, text=True, timeout=60).stdout
    check("the Telegram bot is reachable and has no blocking webhook", "ok    bot is @" in tg and "no webhook" in tg, tg[:120])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("   SKIP  UI checks (pip install playwright to enable)")
        return
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto("http://localhost:5173/dashboard/#/graph")
        pg.wait_for_timeout(2500)
        txt = pg.inner_text("main")
        check("the UI's Graph page shows the live graph with 2 tasks", "Live from Neo4j" in txt and "2 tasks" in txt, txt[:200].replace("\n", " "))
        check("every node is drawn", pg.locator("[data-node]").count() == 1 + 4 + 2, str(pg.locator("[data-node]").count()))
        pg.goto("http://localhost:5173/dashboard/#/tasks")
        pg.wait_for_timeout(1500)
        check("the Tasks page lists the tasks", "Collect requirements" in pg.inner_text("main") and "Build the prototype" in pg.inner_text("main"))
        pg.goto("http://localhost:5173/dashboard/#/forecast")
        pg.wait_for_timeout(1500)
        check("the Forecast page shows the schedule", "Schedule" in pg.inner_text("main"))
        check("no browser errors on any page", errors == [], str(errors)[:160])
        br.close()


SCENARIOS = [s1_empty_project, s2_create_tasks_everywhere, s3_blocker_in_slack, s4_promise_and_delivery, s5_deadline_risk_and_recovery,
             s6_belief_conflict_in_channel, s7_unsure_reply_review_override_undo, s8_meeting_transcript, s9_anti_spam_and_dry_run,
             s10_consistency_security_ui]

if __name__ == "__main__":
    wanted = {int(a) for a in sys.argv[1:] if a.isdigit()} or set(range(1, 11))
    try:
        http.get(API + "/health").raise_for_status()
    except Exception as e:
        raise SystemExit(f"The backend is not running on {LOCAL} ({e}). Start it: uvicorn app.main:app --port 8000")
    for i, fn in enumerate(SCENARIOS, 1):
        if i not in wanted:
            continue
        _current = i
        print(f"\n== Scenario {i}: {(fn.__doc__ or fn.__name__).strip().splitlines()[0]}")
        t0 = time.time()
        try:
            fn()
        except Exception as e:                                  # a crash is a failure, not a reason to stop the rest
            check(f"scenario {i} ran to the end", False, f"{type(e).__name__}: {str(e)[:200]}")
        print(f"   ({time.time() - t0:.0f}s)")
    bad = [r for r in results if not r[2]]
    print(f"\n{'=' * 60}\n{len(results) - len(bad)}/{len(results)} checks passed across {len({r[0] for r in results})} scenarios")
    for n, name, _, detail in bad:
        print(f"  FAILED  S{n}: {name}  [{detail}]")
    sys.exit(1 if bad else 0)
