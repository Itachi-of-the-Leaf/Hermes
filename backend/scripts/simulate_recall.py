"""Exercise the Recall.ai meeting-agent pipeline end to end WITHOUT a live call.

    cd backend && python scripts/simulate_recall.py

What is real: the Recall account (API key + region are verified), the public webhook URL (events are POSTed to
PUBLIC_BASE_URL with the secret token, exactly like Recall's bot does), live triage by Jev, finishing the call, the LLM summary,
the lead reviews, and the dashboard.
What is simulated: only the bot's presence in a call. Recall itself is not asked to join anything.
To test the joining too, give the Meetings page a real Teams/Zoom/Meet link (that uses billable Recall minutes).

WARNING: starts with POST /api/reset (wipes tasks and history).
"""
import sys
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from sqlmodel import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import Meeting  # noqa: E402

LOCAL = "http://localhost:8000/api"
HOOK = settings.public_base_url.rstrip("/") + "/api/meetings/webhooks/recall"
http = httpx.Client(timeout=60)
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def wait_for(fn, timeout=40, every=1.5):
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


def segment(name, text, start):
    """What Recall's real-time `transcript.data` webhook carries for one utterance."""
    words = [{"text": w, "start_timestamp": {"relative": start + i * 0.4}, "end_timestamp": {"relative": start + i * 0.4 + 0.35}}
             for i, w in enumerate(text.split())]
    return {"event": "transcript.data", "data": {"data": {"participant": {"id": abs(hash(name)) % 1000, "name": name}, "words": words}}}


def hook(mid, payload, token=None):
    return http.post(HOOK, params={"token": token or settings.webhook_token, "meeting_id": mid},
                     json=payload, headers={"ngrok-skip-browser-warning": "1"})


print("== The Recall account and the public webhook")
r = http.get(f"https://{settings.recall_region}.recall.ai/api/v1/bot/", params={"page_size": 1}, headers={"Authorization": f"Token {settings.recall_api_key}"})
check(f"the Recall key works in region {settings.recall_region}", r.status_code == 200, f"HTTP {r.status_code}")
check("the app reports the meeting agent as configured", http.get(LOCAL + "/health").json().get("recall") is True)
check("PUBLIC_BASE_URL reaches this backend from the internet", http.get(settings.public_base_url.rstrip("/") + "/api/health", headers={"ngrok-skip-browser-warning": "1"}).status_code == 200, settings.public_base_url)
check("the webhook rejects a wrong token", hook(1, {}, token="wrong").status_code == 401)

print("\n== A simulated team call: tasks, then the bot 'joins', transcribes live, and the call ends")
http.post(LOCAL + "/reset")
time.sleep(3)
for title, owner, est, deps in (("Get sandbox test credentials", "atharva", 45, []), ("Connect the payment API", "soham", 120, ["T1"]),
                                ("Record the demo video", "tejas", 60, [])):
    assert http.post(LOCAL + "/tasks", json={"title": title, "owner_key": owner, "est_minutes": est, "depends_on": deps}).status_code == 200
# the page's "Send agent" creates this row (and asks Recall for a bot); here the bot is not asked to join anything
with Session(engine) as s:
    m = Meeting(title="Simulated Recall call", meeting_url="https://meet.example/simulated", provider="recall", status="joining")
    s.add(m)
    s.commit()
    s.refresh(m)
    mid = m.id
check("the meeting is waiting for the bot to join", http.get(f"{LOCAL}/meetings/{mid}").json()["status"] == "joining")
check("a webhook for a meeting that does not exist is still answered 2xx (Recall must never see an error)", hook(99999, segment("X", "hello there", 0)).status_code == 200)

LINES = [("Soham", "Okay the payment API is connected but I'm still waiting for the sandbox test credentials from Atharva", 5),
         ("Atharva", "I'll send the sandbox credentials tonight", 14),
         ("Tejas Nalawade", "Let's cut the refund flow from the demo scope", 22),
         ("Tejas Nalawade", "I'll record the demo video on Saturday", 29),
         ("Samarth", "Did anyone catch the cricket score", 36)]
codes = [hook(mid, segment(*ln)).status_code for ln in LINES]
check("Recall's transcript events are accepted through the public URL", codes == [200] * 5, str(codes))
empty = hook(mid, {"event": "transcript.data", "data": {"data": {"participant": {"id": 1, "name": "Silent"}, "words": []}}}).status_code
check("an empty segment is accepted and ignored", empty == 200)
check("the first transcript event moves the meeting to 'in call'", wait_for(lambda: http.get(f"{LOCAL}/meetings/{mid}").json()["status"] == "in_call", 15) is True)

live = wait_for(lambda: (x := http.get(f"{LOCAL}/meetings/{mid}").json()) and len(x["utterances"]) == 5 and all(u["triaged"] for u in x["utterances"]) and x, 60)
check("every line was triaged LIVE, during the call", bool(live))
if live:
    flags = {u["text"][:12]: u["flagged"] for u in live["utterances"]}
    check("the important lines are flagged and the cricket question is not", [u["flagged"] for u in live["utterances"]] == [True, True, True, True, False], str(flags))
    check("speakers keep their names (including a full name)", [u["speaker"] for u in live["utterances"]][2] == "Tejas Nalawade" and live["utterances"][2]["member"] == "Tejas")
    check("Jev did the triage", all(u["flags"].get("engine") == "jev" for u in live["utterances"]), str({u["flags"].get("engine") for u in live["utterances"]}))

print("\n== The call ends: summary, action items, and what the lead must confirm")
check("'End and summarise now' is accepted", http.post(f"{LOCAL}/meetings/{mid}/finish").status_code == 200)
done = wait_for(lambda: (x := http.get(f"{LOCAL}/meetings/{mid}").json())["status"] == "done" and x, 90, 2)
check("the call is analysed to completion", bool(done), str(http.get(f"{LOCAL}/meetings/{mid}").json()["status"]))
if done:
    s = done["summary"] or {}
    check("a summary with a headline is written", bool(s.get("headline")), str(done["stats"]))
    print("        headline:", s.get("headline"))
    check("the summary came from the LLM", done["stats"].get("summary_engine") == "llm", str(done["stats"].get("summary_engine")))
    check("the blocker is captured", any("credentials" in (b.get("text") or "").lower() for b in s.get("blockers", [])))
    check("action items name the people who committed", {"Atharva", "Tejas"} <= {a.get("owner") for a in s.get("action_items", [])}, str([a.get("owner") for a in s.get("action_items", [])]))
    rv = http.get(LOCAL + "/reviews").json()
    check("status heard in the call waits for the lead (only the real blocker; promises do not)", [(x["member"], x["proposal"]["status"]) for x in rv] == [("Soham", "Blocked")], str([(x["member"], x["proposal"]["status"]) for x in rv]))
    board = http.get(LOCAL + "/board").json()
    check("nothing on the board changed by itself", all(t["status"] == "Pending" for t in board["tasks"]))
    check("ending the call twice is harmless", http.post(f"{LOCAL}/meetings/{mid}/finish").status_code == 200)

print("\n== The dashboard")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True)
        pg = br.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto("http://localhost:5173/#/meetings")
        pg.wait_for_timeout(2000)
        txt = pg.inner_text("main")
        check("the Meetings page lists the call", "Simulated Recall call" in txt, txt[:160].replace("\n", " "))
        pg.goto(f"http://localhost:5173/#/meetings/{mid}")
        pg.wait_for_timeout(2500)
        pg.click("summary:has-text('Transcript')")                 # collapsed once a summary exists
        pg.check("text=Show every line")
        pg.wait_for_timeout(500)
        txt = pg.inner_text("main")
        check("the meeting page shows the summary and the full transcript", "Atharva" in txt and "cricket" in txt.lower(), txt[:160].replace("\n", " "))
        check("no browser errors", errors == [], str(errors)[:150])
        br.close()
except ImportError:
    print("   SKIP  UI checks (pip install playwright)")

print(f"\n{'=' * 60}\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
