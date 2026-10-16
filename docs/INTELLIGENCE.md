# Graph-aware scheduling and the autonomous follow-up loop

Code: `backend/app/intel/`. Dashboard: **Forecast & agent**. API: `/api/intel/*`.

```
status change ──► replan() ──► debounce ──► graph snapshot (version = hash of inputs)
                                                │
                       ┌────────────────────────┤
                       ▼                        ▼
               graph analysis             CP-SAT schedule ──(fails?)──► dependency-order fallback
          (blocked / affected / frontier /        │
           critical path / bottlenecks)           ▼
                       └──────────────► risk engine (structural + forecast, with evidence)
                                                │
                                                ▼
                         action planner (who, which action, cooldowns, dedupe, escalation)
                                                │
                                                ▼
                       message writer (template; optional LLM rewording, validated)
                                                │
                                                ▼
                       Telegram group (mention) ──► reply ──► commitment / resolution ──┐
                                                                                         │
                                       back into the normal reply pipeline ◄────────────┘
```

## Where this differs from the original proposal, and why

| Proposal | Built | Reason |
| --- | --- | --- |
| Neo4j as the authoritative project state | **SQL stays authoritative; Neo4j is an optional read-only mirror** (`neo4j_mirror.py`) | Status changes, Undo and the audit trail already live in SQL transactions. A second source of truth can disagree after a crash or an Undo. At tens of tasks, every traversal is microseconds in memory, so a database adds no algorithmic benefit; it earns its place for ad-hoc Cypher and visualisation. Not claimed to be faster than PostgreSQL. |
| CP-SAT drives interventions | CP-SAT informs them; **blocker follow-ups are structural** and fire without the solver | A confirmed blocker justifies a message even when durations are unknown or the solver is down (tested). |
| Continuous solving | **Event-driven, debounced, single-flight, versioned** | Ordinary chatter never triggers a solve. |
| Hard deadlines | **Soft deadlines** with weighted lateness | The model is always feasible and says *how late*; a hard deadline would return "INFEASIBLE" and no plan. |
| Cumulative shared resources | **People as capacity-1 resources** (`NoOverlap`) | Covers the demo and the realistic case. Shared equipment with capacity > 1 would use `AddCumulative`; not needed yet. |

CP-SAT is honest overkill for a 5-task chain: critical-path maths alone would answer most questions. It earns its keep as soon as one person has several ready tasks, because it then chooses the order that minimises weighted lateness.

## The model (`schedule.py`)

All times are integer minutes from "now". For every unfinished task *i*:
`start_i ≥ earliest_i`, interval of fixed length `d_i`, `start_b ≥ end_a` for every "b depends on a" (finished prerequisites vanish), `NoOverlap` over each owner's intervals, `late_i ≥ end_i − deadline_i`, `late_i ≥ 0`.

Objective, lexicographic by construction: `minimise SCALE · Σ wᵢ·lateᵢ + makespan`, with `SCALE` larger than any possible makespan and `wᵢ = 1 + (open tasks waiting behind i)`. So lateness always outranks finishing early, and slipping a bottleneck costs more. No hand-tuned coefficients.

**Unknowns are explicit, never zero:**

| Situation | Treatment | Effect |
| --- | --- | --- |
| No duration estimate | `INTEL_DEFAULT_TASK_MIN`, listed in `assumptions` | confidence MEDIUM, or LOW if over half the tasks are guesses |
| In progress, % unknown | assume half done | listed |
| Blocked, no unblock time known | `INTEL_UNKNOWN_UNBLOCK_MIN` (pessimistic placeholder) | confidence LOW |
| Blocked, unblock promised | earliest start = the promised time, marked *unverified* | confidence MEDIUM |
| Promise has passed | back to the placeholder | the agent chases (see below) |
| Blocked on a tracked prerequisite | precedence already encodes it | no placeholder |

Results carry a `status`: `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `UNKNOWN`, `FALLBACK_CPM`, `NO_WORK`, `INVALID_GRAPH`. Anything unusable falls back to a deterministic dependency-order schedule. The forecast is a forecast, not a guarantee; the UI and messages hedge when confidence is not HIGH.

## Contracts

* **Graph convention:** `(B)-[:DEPENDS_ON]->(A)` means B depends on A. *Downstream of A* walks `dependents[A]` (the arrow backwards).
* **Vocabulary:** *blocked* (owner reported, unresolved) ≠ *waiting* (prerequisite just not done) ≠ *affected* (reachable downstream, maybe not stuck) ≠ *late* (forecast misses deadline).
* **`ScheduleResult`** (`schedule.py`): `version, status, solver, origin, tasks{id → start,end,late_min,slack_min,critical,notes}, project_end, total_late_min, confidence, assumptions, inputs, note`.
* **`RiskEvent`** (`risk.py`): `risk_type, severity, task_id, responsible_member_id, contributing_task, blocker, affected[{task_id,path}], previous_forecast, new_forecast, delay_minutes, evidence[], attribution(direct|possible|none), confidence, assumptions`.
* **`ActionProposal`** (`planner.py`): `type ∈ {REQUEST_STATUS, REQUEST_CLARIFICATION, REQUEST_ETA, REMIND_OWNER, ESCALATE_BLOCKER, …}, recipient_id, task_id, risks[]`. Persisted as an `AgentAction` row with the justification, the platform message id, any commitment, and the reply.
* **Upstream integration:** anything that changes a task goes through `service.apply()`, which calls `replan(kind, task_id)`. Only real state changes do (a repeated "working on it" does not).

## Anti-spam, in one place (`planner.check_policy`)

cooldown (same action + situation + person, `INTEL_COOLDOWN_MIN`) · min gap (any two messages to one person about one task) · no chasing before a promised time (+ grace) · after `INTEL_MAX_ATTEMPTS` unanswered nudges, escalate to the lead once · at most `INTEL_MAX_ACTIONS_PER_RUN` per run · a changed situation (new blocker text, clearly worse forecast) is a new context. Task **priority** (`graph.priority_scores`, computed on demand, never stored) is separate from **notification priority** (this policy).

## The 20 failure cases

| # | Case | Behaviour | Tested in |
| --- | --- | --- | --- |
| 1–3 | Irrelevant message passes / relevant filtered / wrong task extracted | Upstream pipeline's job. Here: only real status changes call `replan`; a wrong extraction is undoable and reviewable via the existing lead review | `test_only_scheduling_relevant_changes_ask_for_a_replan` |
| 4, 18 | False completion claim | Existing gate and review flow; completion needs the usual confidence. GitHub/CI evidence is an upstream contract, not built here | existing `test_classifier` |
| 5 | Dependency cycle | `INVALID_GRAPH`, no schedule, one clarification to the lead, nothing crashes | `test_a_dependency_cycle_is_escalated…` |
| 6, 20 | No duration / not enough information | Flagged default, confidence drops, message hedges | `test_missing_estimate…`, `test_forecast_numbers_are_hedged…` |
| 7 | Blocker without resolution time | Pessimistic placeholder, LOW confidence, ETA requested | `test_blocked_with_unknown_unblock_time…` |
| 8, 9 | Infeasible / timeout | Fallback schedule, `FALLBACK_CPM`; follow-ups unaffected | `test_unusable_solver_status…`, `test_the_follow_up_still_goes_out_when_cpsat_is_unavailable` |
| 10, 16 | Concurrent changes / deadline edited mid-solve | Single-flight; version re-checked after solving | `test_results_for_outdated_data_are_discarded…` |
| 11 | Old solver result arrives late | Discarded and recomputed | same |
| 12 | Duplicate reply | Idempotent | `test_duplicate_reply_is_handled_once` |
| 13 | Repeated reminder | Cooldown, min gap, attempts cap | `test_the_cooldown_holds…`, `test_repeated_unanswered_nudges_escalate…` |
| 14b | Teammate never connected an app | `unreachable`: nothing is sent to anyone else, it is not retried until the cooldown passes, and it is reported to the Slack alert channel | `test_a_connected_app_but_an_unlinked_recipient_is_unreachable_not_redirected` |
| 14 | Telegram down | 3 retries with backoff; recorded as `failed`; retried next run (failure does not consume the cooldown) | `test_telegram_outage_is_recorded_and_retried…` |
| 15 | Agent hears itself | Bots do not receive their own messages; replies bind only to a person, and sending never calls `replan` | `test_sending_a_message_does_not_trigger_another_replan` |
| 17 | Several blockers on one milestone | One merged message per (task, person) with all reasons; run limit | `test_at_most_a_few_messages_per_run` |
| 19 | Promised handoff never confirmed | `COMMITMENT_OVERDUE` after the promised time + grace, one reminder, then escalation | `test_a_broken_promise_triggers_one_reminder…` |

Not built (and not claimed): GitHub/CI-verified completion, an explicit `Handoff` node with acknowledgement tracking (the commitment on an `AgentAction` covers the demo), counterfactual re-solving for stronger root-cause proof, cumulative shared resources.

## Telegram group chat

1. BotFather → `/setprivacy` → **Disable**, so the bot sees ordinary group messages (replies to the bot are delivered regardless). Add the bot to the group.
2. Each member presses Start via their invite link on the Team page (this stores their Telegram user id, which makes mentions real).
3. The first group a linked member writes in is remembered as the project group. Or pin it: `TELEGRAM_GROUP_CHAT_ID=-100…`.
4. Without a group the same message goes to the person's own channel, so Slack/Teams and local development still work.

A reply *to the agent's message* binds to that exact action. A plain message binds only if the sender has exactly one open request and the text clearly carries a time or a delivery claim; otherwise it flows through the normal pipeline. "I'll send them in 20 minutes" is recorded as a **promise** (the blocker stays open); "sent them" resolves it and asks the owner to confirm.

## Optional Neo4j mirror

Set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`. After each plan the graph is upserted (`MERGE`, idempotent). `neo4j_mirror.py` also holds the Cypher for schema/constraints, downstream traversal (`(a)<-[:DEPENDS_ON*1..]-(x)`), frontier owners, cycle detection, owner conflicts and bottlenecks. The Cypher is verified against a **real Neo4j 5** by `tests/test_neo4j_live.py` (10 tests: constraints, idempotent upserts, downstream traversal, frontier owners, bottlenecks, cycles, owner conflicts, forward-referenced dependencies). The live tests skip unless you point them at a throwaway database:

```bash
docker run -d --name coordinator-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5
NEO4J_LIVE_URI=bolt://localhost:7687 NEO4J_LIVE_PASSWORD=<password> pytest tests/test_neo4j_live.py   # wipes that database
```
Browse the graph at http://localhost:7474 (`MATCH (p:Person)-[:OWNS]->(t:Task) RETURN p, t`).

**In the dashboard:** the **Graph database** page draws every node (project, people, tasks) and relationship straight from Neo4j (`GET /api/intel/network`), with a badge saying whether it is live from Neo4j or built from the project database because Neo4j is not connected or is down. The mirror re-syncs after every change (including Reset), and deleted tasks and people are removed from it.

**Neo4j Aura (hosted, free tier):** in the Aura console create an instance and save the password it shows once, then set `NEO4J_URI=neo4j+s://<id>.databases.neo4j.io`, `NEO4J_USER=neo4j`, `NEO4J_PASSWORD=...` (and `NEO4J_DATABASE` if it is not the default). Check it with `python -m app.cli neo4j-check`. No code change is needed; the same driver speaks to Docker and Aura.

## Run and test

```bash
cd backend && pytest -q                       # includes tests/test_intel_*.py
curl -X POST localhost:8000/api/intel/replan?send=false   # what would the agent say right now?
```
Time is injectable (`run_once(now=…)`), so promises expire and cooldowns pass in tests without sleeping.
