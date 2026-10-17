# Testing it end to end (Slack, Telegram, graph, forecast, agent, "Needs your call")

## 0. One command tells you what is connected

```bash
cd backend && python -m app.cli doctor
```
It checks Slack (token, scopes, public URL, **whether Slack itself has ever sent us an event**), Telegram (token, webhook, privacy mode, group, last update), who has linked which app, whether the **lead is reachable** (otherwise "Needs your call" has nowhere to go), Neo4j (and whether it matches the project), Jev, the LLM and Recall. Every problem comes with the exact fix. Run it until it says `0 problem(s)`, then do the walkthrough below.

## 1. One-time setup per chat app

**Slack** (api.slack.com/apps → your app): *Event Subscriptions* on, Request URL `<PUBLIC_BASE_URL>/api/slack/events` (wait for **Verified**), bot events `message.im`, `message.channels`, `message.groups`; *Interactivity* on (same URL, needed for the buttons); *Slash Commands* `/coordinator` (same URL). Create `#proj-<name>` (+ optionally `#proj-<name>-meetings`, `#alert-<name>`), `/invite @project_coordinator`, put the channel IDs in `backend/.env`. Then everyone DMs the bot once (people are matched by email).
**Telegram**: BotFather → `/setprivacy` → Disable; add the bot to the team group; each person presses Start on their link (Team page); someone writes once in the group.

## 2. The walkthrough (about 10 minutes, same steps in Slack and in Telegram)

Open the dashboard (http://localhost:5173/dashboard/) next to your chat app. Press **Reset** first.

| # | Do this | You should see |
| --- | --- | --- |
| 1 | In the chat: Slack `task: Set up the repository @soham est 2h due friday` · Telegram `/task Set up the repository @soham est 2h due friday` | Confirmation in the chat; **Tasks** page shows it; **Graph database** page shows the new task node (and the Neo4j browser/Aura console shows it too) |
| 2 | `task: Build the login page @samarth est 3h after T1` | Prerequisite shown in the confirmation; an arrow on the **Graph** page; a **Forecast** with HIGH confidence (every task has an estimate) |
| 3 | As the task owner, reply in chat: *"I'm blocked on the login page, waiting for the repository"* | Task turns **Blocked**; **Forecast & agent** shows a *confirmed blocker* risk with its evidence; the agent messages whoever can unblock it (in the group with a real @mention, or as a DM) |
| 4 | As that person, reply *"in 20 minutes"* (reply directly to the agent's message) | Recorded as a **promise** (blocker stays open); no nagging; the forecast notes the promised time as *unverified* |
| 5 | Reply *"sent it"* | Blocker resolved; the owner is asked to confirm they can resume; the request closes |
| 6 | Give a task a tight deadline (Tasks → New task, due today, 20 h of estimate) | **Forecast & agent** shows *deadline risk* with the dependency path that causes it; finishing the first task lowers the lateness |
| 7 | **Needs your call:** upload a transcript (Meetings page) or send an unsure reply like *"done I think, not totally sure"* | A card appears under **Needs your call**, and the **lead** gets the question in Slack/Telegram with buttons; approve or override in either place; the board updates live; **Undo** on the task restores it |
| 8 | In the project channel, say *"the login page is done, starting the video"* while it is not | The agent challenges both people (*belief conflict*); the board does not change by itself |
| 9 | Press **Reset** | Tasks vanish from the board, the forecast and (within seconds) the graph database |

Every step also shows up in **Activity** (what was sent, to whom, which engine decided).

### The no-commands, no-tagging path (Telegram group, admin bot)

Write normally in the group and watch the **Tasks** and **Graph database** pages:

| Say this | Expect |
| --- | --- |
| `<name> please build the backend API, about 4 hours, by Friday` | A task for that person with the estimate and deadline, with an **Undo** button and a real @mention |
| `I'll build the dashboard once <name> finishes the backend API` | A task for you that *waits for* the API; an arrow on the **Graph** page |
| `the dashboard must wait for the design review that <name> does` | A review task for them, and the dashboard linked to it |
| `I've completed my task` | Your only open task is updated (several open: it asks which one) |
| `is the dashboard blocked on anything?` · `lol` · `grab me a samosa` | Silence |
| `/deps` | The obvious prerequisites between existing tasks get linked |

## 3. Automated versions of the same thing

```bash
cd backend
pytest -q                          # ~380 offline tests
python scripts/simulate.py         # the 10 scenarios above against the RUNNING stack (real Slack DMs go to the workspace owner; wipes data)
python scripts/simulate_recall.py  # the meeting agent's webhook pipeline (no live call)
cd ../frontend && npm test         # dashboard components
```
`simulate.py` checks the same outcomes as the table: tasks created from the dashboard and Slack, blockers, promises, deadline risk, conflicts, reviews and undo, meetings, anti-spam, and that Neo4j mirrors the project.

## 4. When something does not work

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Nothing arrives from Slack, `doctor` says "Slack has not sent any event" | Event Subscriptions URL not saved/verified, or the tunnel URL changed | Re-save the Request URL; a free ngrok URL changes when ngrok restarts: update `PUBLIC_BASE_URL` and the three Slack URLs |
| Slack buttons do nothing | Interactivity URL missing | Slack app → Interactivity & Shortcuts |
| The agent's request shows `unreachable` | That person has not connected any app | They DM the Slack bot once or press Start on Telegram |
| Telegram group messages are ignored | Privacy mode on | BotFather → `/setprivacy` → Disable (replies to the bot work regardless) |
| "Needs your call" is empty or the lead never hears | The lead is not linked | `doctor` flags it; the lead DMs the bot |
| Graph page says "From the project database" | Neo4j not reachable | `python -m app.cli neo4j-check`; for Aura use the values from its downloaded credentials file (the database name is the instance ID) |
| Forecast confidence LOW | Tasks without estimates, or blockers without a time | Add `est 2h`; ask for an ETA (the agent does) |
