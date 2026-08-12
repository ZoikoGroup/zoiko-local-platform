"""add fraud rules and fraud cases for proprietary fraud model

Revision ID: 0d31d1ab1f2d
Revises: 6702184e75db
Create Date: 2026-08-10 10:57:50.686114

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0d31d1ab1f2d'
down_revision: Union[str, None] = '6702184e75db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op. This was written against a dev DB where fraud_rules/fraud_cases
    # didn't exist yet, but on the merged chain (venky+anilupdated) they're
    # already created by 7a2e5c918bf4_add_fraud_rules_cases_and_spend_signal.py,
    # and risksignaltype already has GEOGRAPHIC_DISPERSION (uppercase, this
    # codebase's actual .name-based storage convention - see
    # a80b7b11ce8e_add_risk_signals_table.py) baked in at table-creation time.
    # Running this migration's original DDL on top of that chain would fail
    # with "relation fraud_rules already exists" and would additionally add
    # an incorrect, unused lowercase 'geographic_dispersion' enum label
    # alongside the real uppercase one. See 11fe863d9b8a for the same
    # reconciliation pattern applied to the next migration in this branch.
    pass


def downgrade() -> None:
    pass
