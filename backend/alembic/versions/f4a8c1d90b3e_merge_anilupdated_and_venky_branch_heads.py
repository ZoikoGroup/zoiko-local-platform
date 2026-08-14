"""merge anilupdated and venky branch heads

Revision ID: f4a8c1d90b3e
Revises: c4d8e1a5b9f0, 4ebb299b8b5f
Create Date: 2026-08-14 00:00:00.000000

No-op merge point joining the anilupdated branch tip (c4d8e1a5b9f0 - Market
Activation Registry + trial-abuse step-up risk_state) with the venky branch
tip (4ebb299b8b5f - pro/scale plan tiers, tail of the price-book-engine +
trial/fraud-control-plane chain). The two chains built overlapping features
independently (see app.risk.models.AccountRiskState's and
app.numbering.numbers.models.MarketActivationStatus's docstrings) -
bcddcc835028 was edited during this merge to drop its now-redundant
accounts.risk_state column add (b2e6c4a19f03, on the anilupdated side,
already added the same column under a different enum name) so both chains
can run back-to-back without a duplicate-column error. Not yet run against
a real database - see this merge's commit message.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'f4a8c1d90b3e'
down_revision: Union[str, Sequence[str], None] = ('c4d8e1a5b9f0', '4ebb299b8b5f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
