"""fix risksignaltype enum casing to uppercase

Revision ID: 351fca0d8b24
Revises: a4cc9cb0ae0a
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '351fca0d8b24'
down_revision: Union[str, None] = 'a4cc9cb0ae0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Found live: the risksignaltype enum ended up with lowercase .value-style
# labels (velocity_exceeded, ...) instead of this codebase's usual
# uppercase .name-style convention that every other enum column follows
# (call_direction_enum etc.) and that app.risk.models.RiskSignal/FraudRule
# already assume with no values_callable override. Root cause: two
# independent migrations (7a2e5c918bf4 and a80b7b11ce8e) disagreed on
# casing when creating this type; whichever's CREATE TYPE actually ran
# first silently won, since a Postgres enum type is looked up by name, not
# recreated, once it already exists.
#
# RENAME VALUE (Postgres 10+) relabels an existing enum value in place -
# any row already using the old label transparently shows the new one, no
# data rewrite needed. Confirmed live there are zero risk_signals rows and
# only the 3 migration-seeded fraud_rules rows before this ran, so there
# was nothing at risk either way.
#
# Guarded with an existence check (not a bare RENAME VALUE): 7a2e5c918bf4
# and 9c1f4a0d2e77 (the migrations that originally introduced these lowercase
# labels) were fixed at the source after this migration was first written -
# on a fresh database run today, the labels are already uppercase by the
# time this runs, and an unguarded RENAME VALUE on a label that doesn't
# exist fails with "is not an existing enum label". This migration now only
# does something on a database whose risksignaltype drifted lowercase
# before that source fix existed (this project's one long-lived dev
# database) - a no-op everywhere else, same "correct regardless of which
# state it finds" posture as 7a2e5c918bf4's own has_table() guards.
_LABEL_RENAMES = [
    ('velocity_exceeded', 'VELOCITY_EXCEEDED'),
    ('blocked_destination_attempt', 'BLOCKED_DESTINATION_ATTEMPT'),
    ('geographic_dispersion', 'GEOGRAPHIC_DISPERSION'),
    ('spend_limit_exceeded', 'SPEND_LIMIT_EXCEEDED'),
    ('device_fingerprint_abuse', 'DEVICE_FINGERPRINT_ABUSE'),
]


def _rename_if_exists(old: str, new: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumtypid = 'risksignaltype'::regtype AND enumlabel = '{old}'
            ) THEN
                ALTER TYPE risksignaltype RENAME VALUE '{old}' TO '{new}';
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for old, new in _LABEL_RENAMES:
        _rename_if_exists(old, new)


def downgrade() -> None:
    for old, new in _LABEL_RENAMES:
        _rename_if_exists(new, old)
