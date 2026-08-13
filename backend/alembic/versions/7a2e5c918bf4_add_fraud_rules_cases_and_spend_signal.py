"""add fraud_rules, fraud_cases tables and spend_limit_exceeded signal

Revision ID: 7a2e5c918bf4
Revises: ce2bebedfe43
Create Date: 2026-08-10 13:10:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7a2e5c918bf4'
down_revision: Union[str, None] = 'ce2bebedfe43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE (venky/anilupdated merge, 2026-08-11): the anilupdated side of
    # this merge already has its own migration (0d31d1ab1f2d) that created
    # fraud_rules, fraud_cases, and the fraudcasestatus enum, and it was
    # already applied to the shared live DB before this branch merged in -
    # so the create_table/create-enum statements this migration originally
    # had are redundant here (they'd fail with "relation already exists").
    # Left only the parts that are still genuinely new on top of that:
    # the spend_limit_exceeded enum value, and first-pass weight seeds.
    # risksignaltype's existing labels are lowercase .value-style (a
    # documented exception to this codebase's usual uppercase-.name
    # convention for enums - see 0d31d1ab1f2d's note on adding
    # 'geographic_dispersion') - must match, not the uppercase this
    # migration originally used.
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'spend_limit_exceeded'")

    # Seed weights matching the values service.py already hardcodes for
    # these three pre-existing signal types (see risk/service.py's
    # _DEFAULT_WEIGHTS) - a real, staff-editable FraudRule row is better
    # than only ever relying on the code fallback. spend_limit_exceeded is
    # deliberately NOT seeded here - it falls back to _DEFAULT_WEIGHTS
    # (same "no active row -> conservative built-in default" design
    # FraudRule's docstring already describes), which also sidesteps
    # Postgres's rule against using a freshly ALTER TYPE ADD VALUE'd label
    # inside the same transaction that added it.
    fraud_rules_table = sa.table(
        'fraud_rules',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('signal_type', sa.String),
        sa.column('weight', sa.Integer),
        sa.column('is_active', sa.Boolean),
    )
    op.bulk_insert(
        fraud_rules_table,
        [
            {"id": str(uuid.uuid4()), "signal_type": "velocity_exceeded", "weight": 30, "is_active": True},
            {"id": str(uuid.uuid4()), "signal_type": "blocked_destination_attempt", "weight": 40, "is_active": True},
            {"id": str(uuid.uuid4()), "signal_type": "geographic_dispersion", "weight": 25, "is_active": True},
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM fraud_rules WHERE signal_type IN "
        "('velocity_exceeded', 'blocked_destination_attempt', 'geographic_dispersion')"
    )
    # Removing an enum VALUE (as opposed to the whole type) isn't supported
    # by Postgres - SPEND_LIMIT_EXCEEDED stays defined even on downgrade,
    # same tradeoff the original risksignaltype migration already accepted.
