"""pay lane step-up check

Revision ID: ed3aa82f74f9
Revises: 2196b8d62ff7
Create Date: 2026-11-21 15:28:55.614201

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'ed3aa82f74f9'
down_revision: Union[str, Sequence[str], None] = '2196b8d62ff7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_verify_calls_card_id'), table_name='verify_calls')
    op.drop_table('verify_calls')
    op.alter_column('payment_cards', 'verify_call_required', new_column_name='step_up_required')
    op.drop_column('payment_cards', 'verify_call_id')
    op.add_column('payment_cards', sa.Column('step_up_hash', sa.String(length=64), nullable=True))
    op.add_column('payment_cards', sa.Column('step_up_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('payment_cards', 'step_up_at')
    op.drop_column('payment_cards', 'step_up_hash')
    op.add_column('payment_cards', sa.Column('verify_call_id', sa.VARCHAR(length=32), autoincrement=False,
                                             nullable=True))
    op.alter_column('payment_cards', 'step_up_required', new_column_name='verify_call_required')
    op.create_table(
        'verify_calls',
        sa.Column('id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column('card_id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column('card_version', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('approver_id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column('callee_id', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column('questions', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False),
        sa.Column('outcome', sa.VARCHAR(length=12), autoincrement=False, nullable=True),
        sa.Column('summary', sa.VARCHAR(length=600), autoincrement=False, nullable=True),
        sa.Column('duration_s', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.Column('ended_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(['approver_id'], ['users.id'], name=op.f('verify_calls_approver_id_fkey')),
        sa.ForeignKeyConstraint(['callee_id'], ['users.id'], name=op.f('verify_calls_callee_id_fkey')),
        sa.ForeignKeyConstraint(['card_id'], ['payment_cards.id'], name=op.f('verify_calls_card_id_fkey'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('verify_calls_pkey')),
    )
    op.create_index(op.f('ix_verify_calls_card_id'), 'verify_calls', ['card_id'], unique=False)
