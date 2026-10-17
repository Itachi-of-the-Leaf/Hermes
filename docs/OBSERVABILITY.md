# Observability

Three signals, one ID to join them: every log line written inside a request carries the same `trace_id` as its X-Ray trace.

## Traces (OpenTelemetry → ADOT → X-Ray; locally → Grafana Tempo)

Auto-instrumented: FastAPI requests, SQLAlchemy queries, Redis, and every outbound HTTP call (Jev, LLM, Recall, Slack, Teams, Telegram). Custom spans show the agent's reasoning:

| Span | Attributes |
| --- | --- |
| `coordinator.decide` | task_id, member, engine, intent, confidence, action (apply / review / clarify) |
| `jev.decide` | questions, model, cost_usd |
| `llm.complete_json` | model |
| `recall.create_bot` | meeting_id |
| `meeting.analyze` | meeting_id |

So "why did that task go to Blocked?" is one trace: webhook → decide → Jev call (with its cost) → DB writes → Slack/Telegram sends.

## Metrics (OpenTelemetry → CloudWatch EMF; locally → Prometheus)

| Metric | Labels | Watch for |
| --- | --- | --- |
| `coordinator.replies.classified` | engine, intent, action | Share of replies handled by rules vs Jev vs LLM; review rate |
| `coordinator.decision.latency` (ms) | engine | Reply-to-decision time (target under 5 s) |
| `coordinator.messages.sent` | channel, result | Delivery failures per app |
| `coordinator.model.cost_usd` | provider | Jev spend (from its `usage.cost`) |
| `coordinator.model.errors` | provider | Outages; replies fall back to rules |
| `coordinator.meeting.utterances` | flagged, engine | How much of each meeting reaches the LLM |
| `coordinator.reviews.created` | source | Lead workload |

Plus AWS metrics: ALB requests/latency/5xx, ECS Container Insights (per-task CPU and memory), RDS, ElastiCache.

## Logs (JSON → CloudWatch Logs)

```json
{"ts": "2026-10-16T17:40:36", "level": "INFO", "logger": "meetings", "msg": "meeting 1 analysed: {...}",
 "service": "coordinator-worker", "role": "worker", "trace_id": "b7b5edc5…", "span_id": "54be0e34…"}
```

Useful Logs Insights queries:

```
fields ts, logger, msg, trace_id | filter level = "ERROR" | sort @timestamp desc
fields ts, msg | filter logger = "meetings" | sort @timestamp desc | limit 20
stats count() by logger, level | sort count() desc
```

## Alarms (Terraform, email via SNS)

| Alarm | Meaning |
| --- | --- |
| api-5xx | More than 10 server errors in 5 minutes |
| api-p95-latency | Dashboard API p95 above 1.5 s for 10 minutes |
| api/web unhealthy targets | A task is failing ALB health checks |
| worker-not-running | No worker: Telegram replies, check-ins and meeting summaries have stopped |
| app-errors | More than 5 ERROR log lines in 5 minutes |
| model-failures | Jev/LLM failing repeatedly; replies falling back to rules |
| rds-cpu, rds-free-storage, redis-memory | Data tier pressure |

The CloudWatch dashboard `coordinator-prod` shows requests, latency, errors, ECS CPU and a live table of recent warnings.

## Health checks

- `GET /api/health`: liveness, used by the ALB and the container health check; also reports which integrations are on.
- `GET /api/ready`: database and Redis reachable.
- Worker: writes a heartbeat file every 20 s; the container health check fails if it is older than 90 s, and ECS replaces the task.

## Locally

`docker compose up` starts `grafana/otel-lgtm`. Open http://localhost:3000 → Explore → Tempo for traces, Prometheus for metrics. JSON logs print to `docker compose logs -f api worker`.
