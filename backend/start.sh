#!/bin/sh
# Container entrypoint. Single-instance hosts (Render, a VM) set MIGRATE_ON_START=true; ECS runs a separate migrate task.
set -e
if [ "${MIGRATE_ON_START:-false}" = "true" ]; then
  alembic upgrade head
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "*" --timeout-keep-alive 75
