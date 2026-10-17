"""add connect_request table

Revision ID: x9s0t1u2v3w4
Revises: w8r9s0t1u2v3
Create Date: 2026-10-17 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'x9s0t1u2v3w4'
down_revision = 'w8r9s0t1u2v3'
branch_labels = None
depends_on = None


def table_exists(table_name):
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def upgrade():
    # Short-lived requests of the "connect an app" flow. Only the sha256 of the
    # device code is stored; the upload token is minted when the app collects it.
    if not table_exists('connect_request'):
        op.create_table(
            'connect_request',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('device_code_hash', sa.String(length=64), nullable=False),
            sa.Column('user_code', sa.String(length=16), nullable=False),
            sa.Column('client_name', sa.String(length=64), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        with op.batch_alter_table('connect_request', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_connect_request_device_code_hash'), ['device_code_hash'], unique=True)
            batch_op.create_index(batch_op.f('ix_connect_request_user_code'), ['user_code'], unique=True)


def downgrade():
    if table_exists('connect_request'):
        with op.batch_alter_table('connect_request', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_connect_request_user_code'))
            batch_op.drop_index(batch_op.f('ix_connect_request_device_code_hash'))
        op.drop_table('connect_request')
