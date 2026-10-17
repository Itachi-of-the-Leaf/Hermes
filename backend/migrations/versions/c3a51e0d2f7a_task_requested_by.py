"""task requested_by

Revision ID: c3a51e0d2f7a
Revises: b11b9cdd325b
Create Date: 2026-10-17 06:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a51e0d2f7a'
down_revision: Union[str, Sequence[str], None] = 'b11b9cdd325b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('task', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requested_by_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('task', schema=None) as batch_op:
        batch_op.drop_column('requested_by_id')
