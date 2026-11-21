"""add video_info.webhook_pending to databases that skipped upstream's migration

Revision ID: fork_002_webhook_pending
Revises: x9s0t1u2v3w4
Create Date: 2026-11-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fork_002_webhook_pending'
down_revision = 'x9s0t1u2v3w4'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    bind = op.get_bind()
    return column_name in {c['name'] for c in sa.inspect(bind).get_columns(table_name)}


def upgrade():
    # Fork releases up to 1.10.0 shipped the Discord migration as v7q8r9s0t1u2, the
    # same id upstream later gave add_webhook_pending. Databases stamped by those
    # releases therefore skip upstream's webhook_pending column; add it here if missing.
    if not column_exists('video_info', 'webhook_pending'):
        with op.batch_alter_table('video_info', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column('webhook_pending', sa.Boolean(), nullable=True, server_default=sa.false())
            )


def downgrade():
    # Nothing to undo: on a clean history the column belongs to v7q8r9s0t1u2.
    pass
