"""payment link public token

Revision ID: 0c6c8d21170f
Revises: eb7a79ed3166
Create Date: 2026-11-21 13:16:24.599228

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0c6c8d21170f'
down_revision: str | Sequence[str] | None = 'eb7a79ed3166'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("payment_links", "token_hash", new_column_name="public_token")
    op.execute("ALTER INDEX IF EXISTS payment_links_token_hash_key RENAME TO payment_links_public_token_key")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER INDEX IF EXISTS payment_links_public_token_key RENAME TO payment_links_token_hash_key")
    op.alter_column("payment_links", "public_token", new_column_name="token_hash")
