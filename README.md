# AI Group Project Coordinator

An agent that keeps a college group project moving. It checks in with each task owner on **Telegram, Slack or Microsoft Teams**, understands their reply, records blockers, asks whoever can unblock them, joins **Teams meetings** to pick out updates and decisions, posts the summary to **Slack**, and keeps a Notion-style board everyone can trust.

Built for worcflow.ai W-PS-1 (Agentic AI Track).

```
coordinator/
  backend/            FastAPI API + worker, channel adapters, decision pipeline, meeting agent (Python)
  frontend/           Notion-style dashboard (React + TypeScript + Vite)
  deploy/terraform/   AWS: VPC, ALB + Cognito, ECS Fargate, RDS, ElastiCache, ECR, Secrets, CloudWatch, X-Ray
  deploy/             Slack manifest, Teams manifest, deploy script, local gateway config
  .github/workflows/  CI (tests, lint, migrations, build, terraform validate) and Deploy (OIDC → ECR → ECS)
  docs/               ARCHITECTURE, INTEGRATIONS, DEPLOY_AWS, OBSERVABILITY
  docker-compose.yml  production-shaped local stack with Grafana
```

## How it works in one paragraph

A reply arrives from any app and is linked to its task. **Rules** handle buttons and one-word answers, **Jev** (a typed decision model) classifies the rest in milliseconds, and an **LLM** steps in only when Jev is unsure. A **gate written in plain code** makes the final call: anything still waiting on someone is Blocked even if it says "done", Complete needs no hedging, and uncertain updates go to the team lead. Every change is an event with the quoted message, the engine and its confidence, and can be undone. In meetings, Jev reads every transcript line and only the few that matter reach the LLM, which writes the summary; status heard in a meeting always goes to the lead first.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for diagrams.

**Beyond status tracking:** the dependency graph feeds a CP-SAT scheduler that forecasts deadline risk, and an action planner decides who to contact (and not to nag). It reasons from the task graph, never from the LLM, and keeps working if the solver fails. See [docs/INTELLIGENCE.md](docs/INTELLIGENCE.md).

---

## Run it locally (fastest)

Requires Python 3.11+ and Node 20+.

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                    # add any tokens you have; none are required
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend
npm install
npm run dev                                             # http://localhost:5173
```

With no keys at all, the rules engine runs and the sidebar's **Test without apps** panel lets you play the scenario. Add tokens in `.env` to connect real apps; see [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md):

| To turn on | Set |
| --- | --- |
| Telegram | `TELEGRAM_BOT_TOKEN` |
| Slack | `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` (Socket Mode, no public URL needed locally) |
| Microsoft Teams | `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET`, `TEAMS_TENANT_ID` + a tunnel |
| Meeting agent | `RECALL_API_KEY` + public `PUBLIC_BASE_URL` (or upload a `.vtt` transcript instead) |
| Jev | `OPENROUTER_API_KEY` |
| LLM | `LLM_API_KEY`, `LLM_MODEL` |

## Run the production-shaped stack (Docker)

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

App at http://localhost:8080 (a gateway routes like the AWS load balancer), two API replicas, one worker, Postgres, Redis, and Grafana with traces and metrics at http://localhost:3000.

## Is everything connected?

```bash
cd backend && python -m app.cli doctor     # checks Slack, Telegram, Neo4j, models and who the agent can reach; prints the fix for each problem
```
Step-by-step end-to-end testing for Slack and Telegram: [docs/TESTING.md](docs/TESTING.md).

## Tests

```bash
cd backend && source .venv/bin/activate
pytest -q          # ~200 tests: labelled replies, Jev contract, the full blocker loop, channel routing,
                   # Slack/Teams rendering, meeting triage → reviews → conflict → summary → Slack post,
                   # HTTP contract, and the graph → CP-SAT → risk → action → Telegram loop (tests/test_intel_*.py)
cd frontend && npm test   # dashboard components, API client, forecast page
ruff check app tests
```

## Deploy to AWS

`deploy/terraform` builds the whole environment in `ap-south-1` (Mumbai); GitHub Actions builds arm64 images, runs migrations as a one-off task, and rolls ECS services with automatic rollback. Step by step: [docs/DEPLOY_AWS.md](docs/DEPLOY_AWS.md). Monitoring and alarms: [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

## Adding tasks (the project starts empty)

No tasks are pre-loaded, and **nobody has to type commands or tag the bot**. With an LLM configured the agent reads the team's ordinary
chat and builds the plan itself: *"Soham please build the backend API, about 4 hours, by Friday"* creates the task for Soham with its
estimate and deadline; *"I'll build the dashboard once Soham finishes the API"* creates a task that **waits for** the API (an explicit
dependency on the graph); *"the dashboard must wait for the design review that Tejas does"* creates the review and links it. Status
reports without a task name ("I've completed my task") are matched to the speaker's own open task, jokes and questions are ignored,
and every change comes with an **Undo** button. Switch it off with `AUTO_TASKS=false`. Everything it does is checked first (real
people, real tasks, no dependency loops, no past dates, no duplicates, nothing below `AUTO_TASK_MIN_CONFIDENCE`). `/deps` (or the
**Link dependencies** button) links obvious prerequisites between tasks that already exist, and any task's prerequisites can be edited in its panel.

You can still create tasks explicitly, from wherever the team already talks:

| Where | How |
| --- | --- |
| Telegram (group or DM) | `/task Build the login page @soham due friday est 3h after T1` |
| Slack (project channel or DM) | `task: Build the login page @soham due friday est 3h` or `/coordinator task ...` |
| Dashboard | **Tasks → New task** |

`@name` is the owner (default: you), `due` takes today / tomorrow / a weekday / `2026-10-25`, `est` is `90m` or `3h`, `after` lists prerequisite ids or titles. Anything it cannot read is reported back instead of guessed. The agent then follows up, schedules and warns on both the Telegram group and the Slack project channel.

## The demo, in order

0. Add a few tasks as above (the first one with no `after`, the next ones waiting for it).
1. **Send check-ins** → phones buzz on Telegram, Slack and Teams, each member on their own app.
2. The owner of the API task: "The API is connected, but I am waiting for test credentials." → **Blocked**, the owner of the credentials task is asked, and the checkout task turns **At risk**.
3. The checkout owner in the Slack project channel: "integration's done, starting checkout" → both get a **belief conflict** alert.
4. A judge sends any reply; unsure ones land in **Needs your call**.
5. Send the agent into a Teams call; afterwards the summary appears as a page and in Slack, with action items DM'd to owners.
6. The credentials owner taps **Sorted** → the API owner is re-asked → "tested, all green, PR #14" → **Complete** with proof.
7. Open the task: quoted messages, which engine decided, its confidence, the full trace, and Undo.

The **Test without apps** panel is for development only; the brief requires real messages in the demo, and it is hidden when `APP_ENV=prod`.

## API

Interactive docs at `/docs` when the backend runs. Main routes: `/api/board`, `/api/stream` (SSE), `/api/tasks/{id}/timeline`, `/api/reviews`, `/api/checkin`, `/api/events/{id}/undo`, `/api/team`, `/api/meetings` (+ `/upload`, `/{id}/finish`), and webhooks `/api/slack/events`, `/api/teams/messages`, `/api/meetings/webhooks/recall`.
