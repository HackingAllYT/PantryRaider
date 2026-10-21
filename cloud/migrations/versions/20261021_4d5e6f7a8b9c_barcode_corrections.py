"""barcode corrections

Adds community barcode corrections: the barcode_corrections table (one
account's vote on what a barcode is), barcode_decisions (an admin's ruling,
which wins over the vote), and accounts.barcode_trust (normal, trusted or
blocked). Purely additive: two new tables and a column that backfills to ""
(normal), so every existing account keeps its standing.

Revision ID: 4d5e6f7a8b9c
Revises: d58e2b7a91c3
Create Date: 2026-10-21 12:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d5e6f7a8b9c'
down_revision: Union[str, None] = 'd58e2b7a91c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill with a server_default, then drop it so the schema matches the
    # model (a Python-side default only), as the other account flags do.
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('barcode_trust', sa.String(length=10),
                                      nullable=False, server_default=''))
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.alter_column('barcode_trust', server_default=None,
                              existing_type=sa.String(length=10),
                              existing_nullable=False)

    op.create_table(
        'barcode_corrections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('barcode', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('name_key', sa.String(length=120), nullable=False),
        sa.Column('brand', sa.String(length=80), nullable=True),
        sa.Column('category', sa.String(length=20), nullable=True),
        sa.Column('was_name', sa.String(length=120), nullable=True),
        sa.Column('was_brand', sa.String(length=80), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('instance_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.String(length=40), nullable=False),
        sa.Column('updated_at', sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('barcode', 'account_id',
                            name='uq_barcode_corrections_account'),
    )
    with op.batch_alter_table('barcode_corrections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_barcode_corrections_barcode'),
                              ['barcode'], unique=False)
        batch_op.create_index(batch_op.f('ix_barcode_corrections_account_id'),
                              ['account_id'], unique=False)

    op.create_table(
        'barcode_decisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('barcode', sa.String(length=32), nullable=False),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('brand', sa.String(length=80), nullable=True),
        sa.Column('category', sa.String(length=20), nullable=True),
        sa.Column('admin_account_id', sa.Integer(), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=False),
        sa.Column('decided_at', sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('barcode_decisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_barcode_decisions_barcode'),
                              ['barcode'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('barcode_decisions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_barcode_decisions_barcode'))
    op.drop_table('barcode_decisions')
    with op.batch_alter_table('barcode_corrections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_barcode_corrections_account_id'))
        batch_op.drop_index(batch_op.f('ix_barcode_corrections_barcode'))
    op.drop_table('barcode_corrections')
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('barcode_trust')
