import csv
import shutil
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, delete, select

from .config import settings
from .models import (
    AgentAction,
    Appointment,
    Correction,
    Event,
    Forecast,
    Meeting,
    Member,
    Message,
    PRDIngestion,
    Review,
    Task,
    Utterance,
)

_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _sqlite else {},
    pool_pre_ping=not _sqlite,          # survive RDS failovers and idle disconnects
    pool_size=5 if not _sqlite else 5,
)

IDENTITY_FIELDS = ("tg_user_id", "tg_chat_id", "slack_user_id", "slack_dm_id", "teams_aad_id",
                   "teams_conv_id", "teams_service_url", "preferred_channel")


def add_missing_columns() -> list[str]:
    """Dev convenience: create_all never alters an existing table, so a new nullable column would otherwise force deleting the
    database. Adds any missing nullable column (production runs `alembic upgrade head` instead). Returns what it added."""
    from sqlalchemy import inspect, text
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have and col.nullable:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'))
                    added.append(f"{table.name}.{col.name}")
    return added


def init_db() -> None:
    if settings.auto_create_tables:      # dev convenience; production runs `alembic upgrade head`
        SQLModel.metadata.create_all(engine)
        add_missing_columns()
    if settings.role == "all":           # local dev: seed automatically. Prod: `python -m app.cli seed`
        seed_if_empty()


def seed_if_empty() -> bool:
    with Session(engine) as s:
        if s.exec(select(Member)).first():
            return False
        seed(s)
        return True


def seed(s: Session, keep: dict | None = None) -> None:
    """Load data/team.csv and data/tasks.csv. Channel links survive a reset."""
    keep = keep or {}
    with open(settings.data_dir / "team.csv", newline="") as f:
        for row in csv.DictReader(f):
            m = Member(key=row["key"].strip(), name=row["name"].strip(),
                       email=(row.get("email") or "").strip().lower() or None,
                       role=(row.get("role") or "member").strip())
            for k, v in keep.get(m.key, {}).items():
                setattr(m, k, v)
            s.add(m)
    s.commit()
    by_key = {m.key: m for m in s.exec(select(Member)).all()}
    with open(settings.data_dir / "tasks.csv", newline="") as f:
        for row in csv.DictReader(f):
            s.add(Task(
                id=row["id"].strip(), title=row["title"].strip(),
                owner_id=by_key[row["owner"].strip()].id,
                status=(row.get("status") or "Pending").strip(),
                deadline=(row.get("deadline") or "").strip() or None,
                depends_on=(row.get("depends_on") or "").strip(),
                aliases=(row.get("aliases") or "").strip(),
                est_minutes=int(row["est_minutes"]) if (row.get("est_minutes") or "").strip() else None,
            ))
    s.commit()


KEEP_BACKUPS = 5


def backup_database() -> Path | None:
    """SQLite (dev) only: copy the database file aside before anything destroys it. Keeps the newest KEEP_BACKUPS.
    A reset is the one operation that wipes the whole project, so it must never be unrecoverable."""
    if not _sqlite:
        return None
    path = Path(settings.database_url.removeprefix("sqlite:///"))
    if not path.is_file():
        return None
    dest = path.with_name(f"{path.stem}.before-reset-{datetime.now():%Y%m%d-%H%M%S-%f}{path.suffix}")
    engine.dispose()                                      # release the file so the copy is consistent on Windows too
    shutil.copy2(path, dest)
    for old in sorted(path.parent.glob(f"{path.stem}.before-reset-*{path.suffix}"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def reset_db() -> None:
    """Demo reset between rehearsals: reseed tasks, wipe history, keep who connected which app.
    The database file is backed up first (SQLite)."""
    backup_database()
    with Session(engine) as s:
        keep = {m.key: {f: getattr(m, f) for f in IDENTITY_FIELDS} for m in s.exec(select(Member)).all()}
        for model in (Utterance, Meeting, PRDIngestion, Appointment, Event, Review, Message, Correction, AgentAction, Forecast, Task, Member):
            s.exec(delete(model))
        s.commit()
        seed(s, keep)


def get_session():
    with Session(engine) as s:
        yield s
