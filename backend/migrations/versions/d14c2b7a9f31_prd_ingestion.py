"""PRD ingestion audit and idempotency record.

Revision ID: d14c2b7a9f31
Revises: c3a51e0d2f7a
Create Date: 2026-10-17 08:35:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = "d14c2b7a9f31"
down_revision: Union[str, Sequence[str], None] = "c3a51e0d2f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prd_ingestion",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("source_ref", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("source_name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["member.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("prd_ingestion", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_prd_ingestion_uploaded_by_id"), ["uploaded_by_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_prd_ingestion_source_hash"), ["source_hash"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("prd_ingestion", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_prd_ingestion_source_hash"))
        batch_op.drop_index(batch_op.f("ix_prd_ingestion_uploaded_by_id"))
    op.drop_table("prd_ingestion")
