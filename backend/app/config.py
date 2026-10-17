"""All settings come from environment variables (.env in development, Secrets Manager on AWS)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _s(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _b(name: str, default: bool = False) -> bool:
    return _s(name, str(default)).lower() in ("1", "true", "yes", "on")


PRODUCTION_ORIGINS = ("https://www.saarathi.tech", "https://saarathi.tech")


class Settings:
    # ---- runtime
    env: str = _s("APP_ENV", "dev")                     # dev | prod
    # api    = HTTP only (dashboard, Slack/Teams/Recall webhooks); scale to N
    # worker = Telegram polling, scheduler, background jobs; exactly 1
    # all    = both in one process (local development)
    role: str = _s("ROLE", "all")
    # Recall and Slack call back here. Render sets RENDER_EXTERNAL_URL, so a deployed backend defaults to itself.
    public_base_url: str = _s("PUBLIC_BASE_URL", os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000")

    # ---- Telegram
    telegram_token: str = _s("TELEGRAM_BOT_TOKEN")
    lead_telegram_id: str = _s("LEAD_TELEGRAM_ID")

    # ---- Slack (Bolt). Socket Mode in dev (no public URL); HTTP events in prod.
    slack_bot_token: str = _s("SLACK_BOT_TOKEN")        # xoxb-...
    slack_signing_secret: str = _s("SLACK_SIGNING_SECRET")
    slack_app_token: str = _s("SLACK_APP_TOKEN")        # xapp-... only for Socket Mode
    slack_project_channel: str = _s("SLACK_PROJECT_CHANNEL")   # channel ID the bot watches for conflicts
    slack_summary_channel: str = _s("SLACK_SUMMARY_CHANNEL")   # where meeting summaries are posted
    slack_alert_channel: str = _s("SLACK_ALERT_CHANNEL")       # automated feed: escalations and failed sends

    # ---- Microsoft Teams (Teams SDK for Python + Azure Bot registration)
    teams_client_id: str = _s("TEAMS_CLIENT_ID")
    teams_client_secret: str = _s("TEAMS_CLIENT_SECRET")
    teams_tenant_id: str = _s("TEAMS_TENANT_ID")

    # ---- Meeting bot (Recall.ai joins Teams / Zoom / Meet as a participant)
    recall_api_key: str = _s("RECALL_API_KEY")
    recall_region: str = _s("RECALL_REGION", "us-east-1")
    recall_bot_name: str = _s("RECALL_BOT_NAME", "Saarathi")
    webhook_token: str = _s("WEBHOOK_TOKEN", "dev-webhook-token")   # shared secret in webhook URLs
    meeting_auto_apply: bool = _b("MEETING_AUTO_APPLY", False)       # default: meeting updates go to the lead

    # ---- Jev (typed decisions) via OpenRouter
    openrouter_key: str = _s("OPENROUTER_API_KEY")
    jev_url: str = _s("JEV_URL", "https://openrouter.ai/api/alpha/decisions")
    jev_model: str = _s("JEV_MODEL", "typesafe/jev-1.14")
    jev_min_confidence: float = _f("JEV_MIN_CONFIDENCE", 0.6)
    jev_concurrency: int = int(_f("JEV_CONCURRENCY", 8))

    # ---- LLM (OpenAI-compatible: OpenCode Zen, OpenRouter, Bedrock gateway...)
    llm_base_url: str = _s("LLM_BASE_URL", "https://opencode.ai/zen/v1")
    llm_api_key: str = _s("LLM_API_KEY")
    llm_model: str = _s("LLM_MODEL")
    llm_fallback_model: str = _s("LLM_FALLBACK_MODEL")

    # ---- GitHub read-only PR and PRD ingestion
    github_token: str = _s("GITHUB_READ_TOKEN")          # optional token; read-only contents + pull requests access
    github_allowed_repos: str = _s("GITHUB_ALLOWED_REPOS")  # comma-separated owner/repo allowlist; no implicit local gh auth

    # ---- behaviour
    mode: str = _s("MODE", "auto").lower()
    auto_apply_confidence: float = _f("AUTO_APPLY_CONFIDENCE", 0.75)
    complete_confidence: float = _f("COMPLETE_CONFIDENCE", 0.85)
    followup_interval_min: float = _f("FOLLOWUP_INTERVAL_MIN", 0)
    stale_after_min: float = _f("STALE_AFTER_MIN", 60)

    # ---- scheduling intelligence (graph analysis + CP-SAT + autonomous follow-ups)
    intel_autorun: bool = _b("INTEL_AUTORUN", True)                 # replan automatically after status changes
    intel_debounce_s: float = _f("INTEL_DEBOUNCE_S", 2.0)           # coalesce bursts of events
    intel_solver_time_s: float = _f("INTEL_SOLVER_TIME_S", 2.0)     # CP-SAT time limit per solve
    intel_default_task_min: int = int(_f("INTEL_DEFAULT_TASK_MIN", 60))      # used (and flagged) when a task has no estimate
    intel_unknown_unblock_min: int = int(_f("INTEL_UNKNOWN_UNBLOCK_MIN", 240))  # pessimistic placeholder, never zero
    intel_slip_threshold_min: int = int(_f("INTEL_SLIP_THRESHOLD_MIN", 30))  # forecast change worth reporting
    intel_cooldown_min: float = _f("INTEL_COOLDOWN_MIN", 30)        # same action, same context, same person
    intel_min_gap_min: float = _f("INTEL_MIN_GAP_MIN", 5)           # any two messages to one person about one task
    intel_max_attempts: int = int(_f("INTEL_MAX_ATTEMPTS", 3))      # then escalate to the lead instead of nagging
    intel_max_actions_per_run: int = int(_f("INTEL_MAX_ACTIONS_PER_RUN", 2))
    intel_commit_grace_min: float = _f("INTEL_COMMIT_GRACE_MIN", 5)  # after a promised time, wait this long before chasing
    intel_tick_s: float = _f("INTEL_TICK_S", 60)                    # periodic re-evaluation (commitments expire)
    # provisional priority weights (deadline urgency, dependency impact, blocker severity, resource/handoff risk)
    intel_w_deadline: float = _f("INTEL_W_DEADLINE", 0.35)
    intel_w_impact: float = _f("INTEL_W_IMPACT", 0.30)
    intel_w_blocker: float = _f("INTEL_W_BLOCKER", 0.20)
    intel_w_resource: float = _f("INTEL_W_RESOURCE", 0.15)
    auto_tasks: bool = _b("AUTO_TASKS", True)                       # create tasks and dependencies from ordinary conversation
    auto_task_min_confidence: float = _f("AUTO_TASK_MIN_CONFIDENCE", 0.7)   # below this the agent stays quiet
    telegram_group_chat_id: str = _s("TELEGRAM_GROUP_CHAT_ID")      # the team's group; learned automatically if empty
    neo4j_uri: str = _s("NEO4J_URI")                                # optional read-only mirror, e.g. bolt://localhost:7687
    neo4j_user: str = _s("NEO4J_USER", "neo4j")
    neo4j_password: str = _s("NEO4J_PASSWORD")
    neo4j_database: str = _s("NEO4J_DATABASE")                      # blank = the server default (Aura: neo4j)

    # ---- infrastructure
    database_url: str = _s("DATABASE_URL", f"sqlite:///{BASE_DIR / 'coordinator.db'}")
    redis_url: str = _s("REDIS_URL")                    # enables cross-instance SSE + job queue
    auto_create_tables: bool = _b("AUTO_CREATE_TABLES", True)   # prod: False, run Alembic instead
    # comma-separated; the dashboard calls the API from these sites (CORS). The production site is always allowed.
    frontend_origin: str = _s("FRONTEND_ORIGIN", "http://localhost:5173")
    data_dir: Path = Path(_s("DATA_DIR")) if _s("DATA_DIR") else BASE_DIR / "data"   # team.csv + tasks.csv
    log_json: bool = _b("LOG_JSON", False)
    otel_endpoint: str = _s("OTEL_EXPORTER_OTLP_ENDPOINT")
    service_name: str = _s("OTEL_SERVICE_NAME", "coordinator")

    def __init__(self):
        # On AWS the password comes from the RDS-managed secret (rotatable), so the URL is
        # assembled from parts instead of living in one long secret string.
        if not os.getenv("DATABASE_URL") and os.getenv("DB_HOST"):
            from urllib.parse import quote_plus
            self.database_url = (
                f"postgresql+psycopg://{quote_plus(_s('DB_USER', 'coordinator'))}:{quote_plus(_s('DB_PASSWORD'))}"
                f"@{_s('DB_HOST')}:{_s('DB_PORT', '5432')}/{_s('DB_NAME', 'coordinator')}?sslmode=require")
        # Render, Neon and Heroku hand out postgres:// URLs; only the psycopg (v3) driver is installed.
        for prefix in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefix):
                self.database_url = "postgresql+psycopg://" + self.database_url[len(prefix):]

    @property
    def cors_origins(self) -> list[str]:
        listed = [o.strip().rstrip("/") for o in self.frontend_origin.split(",") if o.strip()]
        return list(dict.fromkeys([*listed, *PRODUCTION_ORIGINS, "http://localhost:5173"]))

    @property
    def jev_enabled(self) -> bool:
        return bool(self.openrouter_key)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token)

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token)

    @property
    def teams_enabled(self) -> bool:
        return bool(self.teams_client_id and self.teams_client_secret)

    @property
    def recall_enabled(self) -> bool:
        return bool(self.recall_api_key)

    @property
    def runs_api(self) -> bool:
        return self.role in ("api", "all")

    @property
    def runs_worker(self) -> bool:
        return self.role in ("worker", "all")


settings = Settings()
