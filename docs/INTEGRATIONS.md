# Connecting the apps

Every integration is optional and switched on by its environment variables. The Connections page in the dashboard shows what is active.

## Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, copy the token into `TELEGRAM_BOT_TOKEN`.
2. For conflict detection in the group chat: BotFather → `/setprivacy` → your bot → **Disable**, then add the bot to the group.
3. Each member opens their invite link from the Team page (`t.me/<bot>?start=<key>`) and presses Start.

Only one process may poll a bot; in production that is the worker.

**Check it end to end** (talks to the real Telegram API, never prints the token):

```bash
cd backend && python -m app.cli telegram-check
```
It reports the bot name, a blocking webhook, privacy mode, the project group, and who still has to press Start. Fix every `FAIL`/`warn`, then run the demo: each member presses Start from their link, someone writes once in the team group (the group is remembered), then block a task from the dashboard's "Test without apps" panel and watch the agent mention the right person in the group. Reply to the agent's message with a time ("in 20 minutes") or "sent them" to close the loop.

## Slack

1. api.slack.com/apps → **Create New App** → **From a manifest** → paste `deploy/slack-manifest.yaml` (replace `YOUR_DOMAIN`).
2. Install to the workspace. Copy the **Bot User OAuth Token** (`xoxb-…`) to `SLACK_BOT_TOKEN` and the **Signing Secret** to `SLACK_SIGNING_SECRET`.
3. Invite the bot to your project channel (`/invite @Project Coordinator`). Put the channel ID in `SLACK_PROJECT_CHANNEL` and the summaries channel in `SLACK_SUMMARY_CHANNEL`.
4. People are linked automatically by the email in `data/team.csv`. If an email does not match, the bot asks "Which team member are you?" in DM.

**Local development with a tunnel instead of Socket Mode** (e.g. `ngrok http 8000`): set `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` in `backend/.env` and restart *first*, because Slack verifies the URL the moment you save it. Then, in the Slack app settings, set the same URL `https://<tunnel>/api/slack/events` under **Event Subscriptions → Request URL** (wait for "Verified"), **Interactivity & Shortcuts → Request URL**, and the `/coordinator` **Slash Commands** URL. Subscribe to the bot events `message.im`, `message.channels`, `message.groups`. Requests are accepted only with a valid Slack signature and a timestamp under five minutes old.

**Local development without a public URL:** in the app settings turn on **Socket Mode**, create an app-level token with `connections:write`, put it in `SLACK_APP_TOKEN`. With `ROLE=all` the app connects over a WebSocket.

What it does: check-ins and buttons in DMs, conflict alerts from the project channel, `/coordinator status` and `/coordinator checkin`, meeting summaries with @mentions on action items.

## Your team (the roster)

`backend/data/team.csv` lists the people (`key,name,email,role`). `tasks.csv` ships **empty**: tasks are created from Telegram (`/task ...`), Slack (`task: ...` or `/coordinator task ...`) or the dashboard's **New task** button, never pre-loaded. (If you do put rows in `tasks.csv`, each `owner` must be a `key` from `team.csv`.) Slack and Teams link people **by email**, so use the address they signed up to Slack with. To start from the Slack workspace instead of typing:

```bash
cd backend && python -m app.cli slack-roster            # print the workspace's people as team.csv rows
python -m app.cli slack-roster --write                  # save them (the workspace owner becomes the lead)
```
Single-word Slack handles come out as-is (edit `name` to taste), and every task's `owner` in `tasks.csv` must be one of the `key`s. After changing either file press **Reset** in the dashboard (it reseeds and keeps app links for people whose `key` is unchanged). Tests use their own sample team (`tests/fixtures/demo`, via `DATA_DIR`), never this one.

A teammate who has not connected any app is shown as **unreachable** in the agent's log: the agent will not send their message to anyone else. They connect by pressing Start on their Telegram link, or by DMing the Slack bot once.

## Telegram group + Slack project channel together

The agent's team-wide messages (who is blocked, what is at risk, who is being asked) go to **every** group chat you configure, with a real mention in each:

* Telegram: add the bot to the team group; the first group a linked member writes in is remembered (or set `TELEGRAM_GROUP_CHAT_ID`).
* Slack: set `SLACK_PROJECT_CHANNEL` to the channel ID (or `#name` with the `channels:read`/`groups:read` scopes) and `/invite` the bot.

Replying to the agent's message in *either* chat (a Telegram reply, or a Slack thread reply) answers the same request. With neither group configured, the person being asked gets it as a direct message instead; a teammate who has connected no app is marked **unreachable** and nobody else receives it.

## Slack channel layout

Follow one prefix convention so channels sort and stay searchable. The agent needs only three IDs; everything else is optional.

| Channel | Purpose | Setting |
| --- | --- | --- |
| `#proj-<project>` | The project's working channel. The agent watches it for people who believe something different from the board. Invite the bot. | `SLACK_PROJECT_CHANNEL` |
| `#proj-<project>-meetings` | Meeting summaries with @mentions on action items | `SLACK_SUMMARY_CHANNEL` |
| `#alert-<project>` | Automated feed: escalations to the lead and messages that could not be delivered. Quiet on a healthy day. | `SLACK_ALERT_CHANNEL` |
| `#team-<squad>` | Long-lived squad channels. Not used by the agent; keep them separate from project noise. | none |
| `#help-<domain>` / `#social-<topic>` | Support and culture. Not used by the agent. | none |

You can write `SLACK_PROJECT_CHANNEL=#proj-coordinator` (names are looked up at startup; this needs the `channels:read` and `groups:read` scopes in the manifest, then reinstall) or use the channel *ID* (channel Details, at the bottom), which needs no extra scope. `/invite @Project Coordinator` in each of the three. The agent's own DMs to people (check-ins, unblock requests) need no channel. Group-chat mentions of the agent's follow-ups currently go to the Telegram group; Slack users receive the same message as a DM.

## Microsoft Teams

Teams talks to bots through the Bot Framework, so you need an Azure Bot registration (free tier is fine).

1. Azure portal → **Create** → **Azure Bot**. Type: Single Tenant. Let it create a new Microsoft Entra app.
2. In the bot's **Configuration**, set the messaging endpoint to `https://YOUR_DOMAIN/api/teams/messages`. Note the **Microsoft App ID**.
3. In the Entra app → **Certificates & secrets**, create a client secret.
4. Set `TEAMS_CLIENT_ID` (App ID), `TEAMS_CLIENT_SECRET`, `TEAMS_TENANT_ID`.
5. In the bot → **Channels**, add **Microsoft Teams**.
6. Edit `deploy/teams-manifest/manifest.json`: replace `{{TEAMS_CLIENT_ID}}` and `YOUR_DOMAIN`. Zip `manifest.json`, `color.png`, `outline.png` together.
7. Teams → Apps → **Manage your apps** → **Upload an app** (or ask your admin to publish it to the org).
8. Each member opens the app once (this installs it in their personal chat, which lets the bot message them first). They are linked by email or name.

The endpoint is served by the Teams SDK for Python inside our FastAPI app; it validates the Bot Framework token on every request. Buttons are Adaptive Card actions.

For local testing, use a tunnel (`ngrok http 8000` or dev tunnels) and set the messaging endpoint to the tunnel URL.

## Meeting agent (joins Teams, Zoom or Google Meet)

1. Create a Recall.ai account, copy the API key to `RECALL_API_KEY`, set `RECALL_REGION` to your account's region.
2. Set `PUBLIC_BASE_URL` to your public HTTPS URL (Recall must reach `/api/meetings/webhooks/recall`). Keep `WEBHOOK_TOKEN` secret; it is part of that URL.
3. Dashboard → Meetings → paste the Teams meeting link → **Send agent**. The bot asks to join like any guest; admit it from the lobby.
4. Lines appear live. When the call ends (or you press **End and summarise now**), the summary is written and posted to `SLACK_SUMMARY_CHANNEL`.

Recall bots join as visible participants; tell people the agent is listening. Status updates heard in a meeting go to the lead for approval unless `MEETING_AUTO_APPLY=true`.

**No Recall key?** Upload the transcript Teams saves (meeting recap → Download transcript, `.vtt`).

## PRD ingestion and GitHub pull-request analysis

Set `GITHUB_ALLOWED_REPOS` to a comma-separated list of `owner/repo` values. Optionally set `GITHUB_READ_TOKEN` to a fine-grained GitHub token restricted to read-only Contents and Pull requests permissions; the bot never reuses a developer's local `gh` credentials. The same allowlist protects GitHub PRD file URLs and PR analysis. Private repositories require the read token; if GitHub denies access, no plan/report is produced.

In the configured project Telegram group, a linked lead can reply to a `.md` or `.txt` attachment with `/prd`, or send `/prd https://github.com/<owner>/<repo>/blob/<branch>/<path>.md`. Telegram uploads are limited to 1 MiB UTF-8 text. GitHub PRDs are fetched through the GitHub Contents API; Git LFS pointer files are rejected. Extracted tasks are auto-created only when the model gives explicit supporting evidence, a high confidence score, and an unambiguous roster owner. Unassigned, duplicate, ambiguous, malformed, or cyclic work is held out and shown to the lead; the bot does not infer team skills or availability. Created tasks are sent to the existing linked channel for their owner.

Use `/reviewpr https://github.com/<owner>/<repo>/pull/<number>` for a read-only report, or write an explicit request such as “review this PR <url>” in Telegram. The bot reads PR metadata, changed-file patches and checks, then reports in Telegram. It never posts a review/comment, approves, requests changes, merges, checks out or runs PR code. Reports summarize the retrieved context rather than claiming a full local code/test review; GitHub response and diff sizes are bounded.

## Jev

1. Create a key at openrouter.ai/settings/keys → `OPENROUTER_API_KEY`. No TypeSafe waitlist.
2. Keep `JEV_MODEL` pinned to a version; tune `JEV_MIN_CONFIDENCE` on your own labelled replies (`tests/test_classifier.py` is the place to add them).

## LLM

Any OpenAI-compatible endpoint: OpenCode Zen (the brief recommends its free models), OpenRouter, or a gateway in front of Amazon Bedrock. Set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, and a `LLM_FALLBACK_MODEL`. Free OpenCode model IDs change; check opencode.ai/docs/zen.
