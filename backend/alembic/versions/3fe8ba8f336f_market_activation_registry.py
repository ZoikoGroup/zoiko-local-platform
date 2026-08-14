"""market activation registry: activation_state on supported_countries

Revision ID: 3fe8ba8f336f
Revises: 7d61853cb8ac
Create Date: 2026-08-13 15:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3fe8ba8f336f'
down_revision: Union[str, None] = '7d61853cb8ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    market_activation_state_enum = sa.Enum(
        'CLOSED', 'INTERNAL_TEST', 'CONTROLLED_BETA', 'PAID_OPEN', 'SUSPENDED',
        name='market_activation_state_enum',
    )
    market_activation_state_enum.create(op.get_bind())

    op.add_column(
        'supported_countries',
        sa.Column(
            'activation_state', market_activation_state_enum, nullable=False, server_default='CLOSED',
        ),
    )
    op.add_column('supported_countries', sa.Column('activation_notes', sa.String(length=1000), nullable=True))
    op.add_column('supported_countries', sa.Column('activation_changed_by', sa.String(length=100), nullable=True))
    op.add_column(
        'supported_countries', sa.Column('activation_changed_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Every country already on the list today was added before this
    # registry existed, under the old all-or-nothing gate ("row exists" =
    # fully sellable). Backfilling all of them to CLOSED would silently
    # break existing number purchase/reservation for every one of them -
    # a real functional regression, not a safety improvement, since no
    # actual decision was made to close any of these markets. Backfilling
    # to CONTROLLED_BETA instead reflects the honest current reality
    # (Production Readiness Standard Table 3: public paid launch is
    # NO-GO platform-wide right now, so nothing here is truthfully
    # PAID_OPEN either) without breaking existing dev/test/beta flows.
    # Going forward, any NEWLY added country defaults to CLOSED (this
    # column's own default) - default-deny per Annex B.
    op.execute("UPDATE supported_countries SET activation_state = 'CONTROLLED_BETA'")


def downgrade() -> None:
    op.drop_column('supported_countries', 'activation_changed_at')
    op.drop_column('supported_countries', 'activation_changed_by')
    op.drop_column('supported_countries', 'activation_notes')
    op.drop_column('supported_countries', 'activation_state')
    op.execute("DROP TYPE IF EXISTS market_activation_state_enum")
