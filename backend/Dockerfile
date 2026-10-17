# One image, three commands: api (default), worker, and migrate (one-off task).
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /venv && /venv/bin/pip install -r requirements.txt

FROM python:3.12-slim
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=prod LOG_JSON=true AUTO_CREATE_TABLES=false PORT=8000
RUN useradd --create-home --uid 10001 app
COPY --from=build /venv /venv
WORKDIR /app
RUN chown app:app /app
COPY --chown=app:app alembic.ini start.sh ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app data ./data
COPY --chown=app:app app ./app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=2)" || exit 1
# start.sh: optional migrations, then uvicorn on $PORT with proxy headers trusted (ALB / Render)
CMD ["sh", "start.sh"]
