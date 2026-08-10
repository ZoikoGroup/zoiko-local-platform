"""reconcile schema drift from skipped merge migrations

Revision ID: 11fe863d9b8a
Revises: 0d31d1ab1f2d
Create Date: 2026-08-10 12:56:33.433361

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '11fe863d9b8a'
down_revision: Union[str, None] = '0d31d1ab1f2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op. Every target here was "drift" only against the dev DB this was
    # written against - on the merged chain (venky+anilupdated) each one is
    # already covered by a migration that's a common ancestor of both
    # branches: audit_events.account_id (c1d9a047e3f2), call_records.
    # is_suspected_spam + receptionist_calls.is_likely_spam/spam_reason
    # (17826ded87fb), contacts.created_by_user_id (9158690e2d3a, which -
    # see that file's own comment - deliberately never added
    # uq_contacts_account_phone in the first place, so dropping it here
    # would fail with "constraint does not exist"), notification_templates.
    # canonical_id/domain/spec_version (4b3c700763c1) + .priority
    # (a2a6fcc3d704), phone_numbers.ivr_greeting (75fa64bbaa08) + .
    # next_renewal_at (f2a7c583d9e1), usage_events.estimated_cost_cents
    # (a7c3e9f1d5b8), video_sessions.confidential (4a398a3d4ed6), and
    # zoikonex_sync_events.external_event_id (50a378c2734c). Only
    # video_participant_sessions.worst_connection_quality/reconnect_count
    # didn't already exist verbatim, but that column pair belongs to the
    # call-quality-telemetry feature added in 534b7aad220b, also a common
    # ancestor - so it's covered too. Running this migration's original DDL
    # here would fail outright on the first add_column.
    pass


def downgrade() -> None:
    pass
