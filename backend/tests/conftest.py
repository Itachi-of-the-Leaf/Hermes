import os
import tempfile

# Isolate tests: temp DB, no external services, and nothing from a developer's real backend/.env (which holds live
# tokens). Variables set here win over .env, so every integration is blank. Must run before the app is imported.
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_db}",
    "REDIS_URL": "",
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_GROUP_CHAT_ID": "", "LEAD_TELEGRAM_ID": "",
    "SLACK_BOT_TOKEN": "", "SLACK_SIGNING_SECRET": "", "SLACK_APP_TOKEN": "",
    "SLACK_PROJECT_CHANNEL": "", "SLACK_SUMMARY_CHANNEL": "", "SLACK_ALERT_CHANNEL": "",
    "TEAMS_CLIENT_ID": "", "TEAMS_CLIENT_SECRET": "", "TEAMS_TENANT_ID": "",
    "RECALL_API_KEY": "", "WEBHOOK_TOKEN": "change-me", "PUBLIC_BASE_URL": "http://localhost:8000",
    "OPENROUTER_API_KEY": "", "LLM_API_KEY": "", "LLM_MODEL": "", "LLM_FALLBACK_MODEL": "",
    "NEO4J_URI": "",
    "DATA_DIR": os.path.join(os.path.dirname(__file__), "fixtures", "demo"),   # the sample team, not the real roster
    "MODE": "auto",
    "INTEL_AUTORUN": "false",        # tests drive the engine explicitly; no background replans
})
