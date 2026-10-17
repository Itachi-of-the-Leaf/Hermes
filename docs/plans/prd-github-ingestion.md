# PRD & GitHub Ingestion Implementation Plan

> Local-only plan for the `feat/prd-github-ingestion` branch. Never commit or push as part of this work.

**Goal:** Let a Telegram project lead ingest an approved PRD from a Markdown/text upload or GitHub file URL, extract and allocate work using the team roster, and request a read-only analysis of a linked GitHub PR in chat.

**Architecture:** Preserve existing explicit task creation (`service.create_task`) and chat-to-task validation. Add a dedicated, testable PRD extraction/planning flow which produces candidate work, resolves only against known members, and auto-creates only high-confidence, adequately supported assignments; ambiguity, uncertain ownership, dependency errors or low confidence produce a lead-facing proposal/question and no task write. Keep GitHub API calls in a narrow provider client with read-only endpoints, repository allowlisting and strict URL validation. Telegram routes both workflows to services and sends user-facing output; no GitHub write API, repo checkout, `gh` shell execution, commits or pushes are allowed.

**Tech Stack:** Existing FastAPI/SQLModel, Telegram Bot API via python-telegram-bot, existing OpenAI-compatible LLM wrapper, HTTPX, Alembic, pytest.

---

## Repository facts verified before planning

- Start from a clean working tree on `feat/prd-github-ingestion`; `main`, `origin/main`, and `upstream/main` are all at `9806b5c`. `git fetch origin --prune` succeeded without changing local refs. Do not push or alter any main branch.
- Telegram currently registers commands and `filters.TEXT` in `backend/app/channels/telegram.py`; document/attachment messages are not handled. Group text runs through `service.handle_group` → `converse.handle` (LLM) only when the message looks actionable.
- `Member` fields are `key`, `name`, `email`, `role` and per-channel identity fields. Existing roster CSV has key/name/email/role only. There are no skills/capacity/availability fields, and none should be invented from roles.
- `service.create_task()` validates the member, title, due date, dependencies, and commits. `service.handle_task_command()` is deterministic but depends on `@member`/task title/date tokens.
- No GitHub application integration exists. Existing `backend/app/meetings/providers.py` is an example of a small HTTP provider, and `backend/app/meetings/analyze.py` is an example of a gated extraction/validation pipeline.
- Alembic current head should be confirmed immediately before generating a revision; migrations are chained files in `backend/migrations/versions`. Tests use an isolated SQLite DB and fake providers (`backend/tests/conftest.py`).
- Existing offline backend suite: 525 passed, 13 skipped on Python 3.14; Ruff passed on the clean checkout.

## Safety and scope contract

- Only a linked `Member` whose current role is `lead` can ingest/activate a PRD or request a GitHub PR review. Require the configured/remembered project group for group ingestion; do not change project-group selection.
- Accept only `.md` and `.txt` uploads (and GitHub blob/raw URL for an allowlisted repository). Enforce a 1 MiB source limit, UTF-8 decode with explicit rejection on malformed content, bounded text length/model tokens, and no archive/document macros. A GitHub file URL is fetched only from `github.com`/`raw.githubusercontent.com` and an allowlisted repo.
- Treat PRD text and GitHub issue/PR content as untrusted data, never as agent instructions. Send only bounded source text and roster/task context to the existing LLM; strict JSON schema-like validation follows.
- Do not infer member skill, availability, or role from PRD prose. Resolve only explicit named assignments to canonical team members and known roles. The initial feature uses the existing roster; `role` can be surfaced in the prompt/context but is not an authorization signal. Unassigned work gets proposed to the lead for distribution rather than guessed.
- Create tasks automatically only when each item is explicit, confidence meets a conservative threshold (initially >=0.90), the owner resolves exactly to a roster member, dependencies reference validated items/tasks, and the task is not a duplicate. Otherwise do not create any tasks from that item and ask the lead to clarify/review the proposal. No automatic broad assignment by fabricated skills.
- Make ingestion idempotent by Telegram message/file identifier and/or source hash. Avoid storing PRD body unless necessary; if persisted, document retention, store bounded content and hash, never log it. Audit uploader, source, extracted-task outcome, and actor for writes.
- GitHub operation is read-only. Fetch PR metadata, changed files/diff (bounded), and checks; analyze and report in Telegram. Never approve, submit review/comment, request changes, merge, push, checkout PR code, execute PR-controlled code, or execute arbitrary `gh` commands. Restrict `owner/repo` to configured allowlist, validate canonical PR URL, cap response/diff bytes and timeouts, use read-only token; gracefully support public anonymous reads only where GitHub allows.
- No secrets are committed or logged. GitHub credentials are optional deployment secrets with the minimum read-only permission. Existing `gh` auth on the developer machine must not be implicitly reused by the production bot.
- User explicitly asked for surgical edits and local-only work. Do not stage, commit, push, or touch `main`, `origin/main`, or `upstream/main`.

## Task 1: Characterize requirements and add PRD extraction unit tests

**Objective:** Lock the structured extraction contract and conservative assignment behavior before implementation.

**Files:**
- Test: `backend/tests/test_prd.py`
- Create later: `backend/app/prd.py`

**Step 1: Write tests first** for parsing a valid multi-task PRD into candidates; explicit owner canonicalization including case/unique prefix; reject unknown/ambiguous owner and unsupported fields; dates and estimates validation; dependency cycle/missing task; duplicate task rejection; model malformed/empty/failure; no create before authorized lead path. Mock `pipeline.llm.complete_json`; tests must not call external models.

**Step 2: Run targeted tests** from `backend/`: `.venv/bin/python -m pytest tests/test_prd.py -q`; expected initially fails because module is absent.

**Step 3:** Implement data-only parser/validator dataclasses and prompt with strict limits; do not touch DB or Telegram in this module.

**Step 4:** Re-run targeted tests, then `ruff check app tests`.

## Task 2: Add narrow PRD ingestion orchestration with idempotency and audit

**Objective:** Persist only what is needed to safely deduplicate and review ingestion; route approved, high-confidence items through existing task creation.

**Files:**
- Modify: `backend/app/models.py` (minimal ingestion record: source hash/ID, uploader FK, state, created time; avoid PRD raw text unless a concrete use requires it)
- Create: `backend/migrations/versions/<revision>_prd_ingestion.py` (parent is current Alembic head)
- Modify: `backend/app/prd.py`
- Test: `backend/tests/test_prd.py`

**Step 1:** Add tests asserting duplicate source is idempotent, only lead may ingest, no task row is created for any rejected/low-confidence/ambiguous candidate, accepted task records `requested_by_id`, and repeated callbacks do not duplicate work.

**Step 2:** Add the smallest model/migration required. Check migration chain and run `alembic upgrade head` + `alembic check` using a disposable SQLite DB before continuing; production migration must also be syntactically compatible with Postgres.

**Step 3:** Service coordinates source retrieval, candidate extraction, validation, authorization inputs, persistence, and use of `service.create_task()` for accepted items. If one candidate is ambiguous, report it to the lead while allowing only independent valid high-confidence candidates to be created; show exactly which were created and which need clarification. Store source hash + message/file id for idempotency.

## Task 3: Support Telegram Markdown/text attachments and GitHub PRD file links

**Objective:** Provide a lead-only `/prd` intake and authorized document handler without changing general message dispatch.

**Files:**
- Modify: `backend/app/channels/telegram.py`
- Test: `backend/tests/test_telegram_group.py` or dedicated `backend/tests/test_prd_telegram.py`
- Modify docs: `docs/INTEGRATIONS.md`, `docs/TESTING.md`

**Step 1:** Add tests for non-lead denial, private chat vs configured project group policy, supported extension/MIME, oversize file rejection, malformed text, Telegram get_file/download mock, and safe response escaping.

**Step 2:** Register a narrowly scoped `/prd` command and document-only `MessageHandler` with filters for `document`; do not broaden/replace `on_text` or let arbitrary attachments run through normal group chat extraction. Verify `/prd <allowlisted GitHub blob/raw URL>` path is accepted and foreign host/repo rejected.

**Step 3:** Call the PRD service from the command/handler, return concise HTML-escaped summary and task IDs/owners. Handle Telegram retries/idempotency. Do not publicly echo PRD body or secrets.

**Step 4:** Update bot command menu and help only for the new command; update docs for accepted formats, lead permissions, configuration, and no-skill-guessing policy.

## Task 4: Add read-only GitHub PR provider and review orchestration

**Objective:** Turn an explicit Telegram command/recognized PR link into a bounded, read-only review report.

**Files:**
- Create: `backend/app/github.py` (small provider; configurable allowlist and token; HTTPX)
- Test: `backend/tests/test_github.py`
- Modify: `backend/app/channels/telegram.py`
- Modify: `backend/app/config.py`, `backend/.env.example`

**Step 1:** Unit-test parsing/canonicalization of GitHub PR URLs and issue references; reject wrong host, owner/repo not on allowlist, malformed path, PR number overflow, non-PR URL, redirects to other host, and no configured repo. Test client makes GET only; assert no POST/PUT/PATCH/DELETE endpoints are invoked. Mock API results for metadata, files/diff/checks, pagination, API errors, truncated response, and timeout.

**Step 2:** Implement strict parser for `https://github.com/{owner}/{repo}/pull/{number}`; resolve repo allowlist from config, use API base URL constant, bounded timeouts and per-response/diff limits. Fetch metadata, changed-files summary/diff and check status through GitHub REST read endpoints; summarize findings with LLM only as untrusted diff analysis, or return deterministic metadata if LLM unavailable. Provide explicit caveats and link citations; never run code.

**Step 3:** Add lead-only Telegram `/reviewpr <URL>` command. Additionally, when normal group text explicitly asks for a PR review and contains a valid allowlisted PR URL, route to the same read-only service before `handle_group`; do not treat any generic push/deploy/task statement as a GitHub action. Reuse existing `service`/channel output escaping carefully. Report findings in Telegram only.

## Task 5: End-to-end tests, docs, and verification

**Objective:** Prove both workflows work together and preserve existing project behavior.

**Files:**
- Tests: `backend/tests/test_prd.py`, `backend/tests/test_github.py`, `backend/tests/test_prd_telegram.py`
- Docs: `README.md` only if warranted; otherwise `docs/INTEGRATIONS.md`, `docs/TESTING.md`, `docs/ARCHITECTURE.md`, `backend/.env.example`

**Checks:**
- `cd backend && .venv/bin/ruff check app tests`
- `cd backend && .venv/bin/python -m pytest -q`
- `cd backend && DATABASE_URL=sqlite:////home/soham/.hermes/cache/scratch/prd-migration.db .venv/bin/alembic upgrade head && DATABASE_URL=sqlite:////home/soham/.hermes/cache/scratch/prd-migration.db .venv/bin/alembic check`
- `cd frontend && npm test -- --reporter=dot && npm run build` only if UI touched; likely no frontend edits.
- `git diff --check`, review every changed file/diff; verify `git status --short --branch` still shows only `feat/prd-github-ingestion`, no unexpected tracked artifacts, and `git rev-parse main origin/main upstream/main` unchanged.
- Keep all new behavior covered with mocks; no live PRD uploads, external LLM, or GitHub writes as a test.

## Decisions needed before coding

1. User has accepted first-version input: Telegram Markdown/text file uploads plus a GitHub PRD file URL.
2. User has accepted policy: auto-create/assign only when extraction and member matching are high-confidence; otherwise ask the lead.
3. User has accepted policy: GitHub PR analysis report in Telegram only; never post a GitHub review.

## Not in first version

- PDF, DOCX, archives, image OCR, Notion/Linear ingestion.
- Skill inference, team capacity calculation, schedule optimization changes.
- GitHub webhooks, GitHub write actions, code checkout/build/test, PR approval/comments, merge/deploy/push.
- Adding member skill/availability fields until the user supplies a reliable structured roster source and clear meaning for those attributes.
