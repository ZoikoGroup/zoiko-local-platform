"""split voice notification domain into finer sub categories

Revision ID: 91b990e44d15
Revises: c49b46c6bd85
Create Date: 2026-08-22 18:59:07.090093

Email Communications System doc §11 splits VOICE-domain activity into 5
independent preference keys (calling.missed_calls/voicemail/
call_summaries/scheduled_reminders/quality_alerts) - this codebase's
NotificationPreference.disabled_domains only had domain-granularity
(everything tagged 'VOICE'), so disabling one silently disabled all of
them, contradicting doc §11.1's "changing one category never silently
changes another" invariant that NotificationPreference's own docstring
claims to implement.

Only re-tags the 6 template groups that map cleanly onto one of the doc's
5 named sub-categories (13 templates + the receptionist callback one).
Deliberately leaves calling_suspended/calling_restored/
international_calling_enabled/disabled/high_risk_destination_blocked/
spend_threshold_reached on the generic VOICE tag - these are account-
level calling-policy events the doc's 5 named keys don't actually cover,
and inventing a 6th category not in the doc isn't this migration's call
to make.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91b990e44d15'
down_revision: Union[str, None] = 'c49b46c6bd85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASSIGNMENTS = {
    "VOICE_MISSED": ["voice.missed_call"],
    "VOICE_VOICEMAIL": ["voice.voicemail_received", "voice.voicemail_transcription_ready"],
    "VOICE_SUMMARY": ["voice.call_summary_available"],
    "VOICE_SCHEDULE": [
        "voice.scheduled_call_reminder", "voice.scheduled_call_changed_or_canceled",
        "voice.receptionist_callback_requested",
    ],
    "VOICE_QUALITY": ["voice.call_quality_issue_detected"],
}


def upgrade() -> None:
    conn = op.get_bind()
    for new_domain, keys in _REASSIGNMENTS.items():
        conn.execute(
            sa.text("UPDATE notification_templates SET domain = :domain WHERE key = ANY(:keys)"),
            {"domain": new_domain, "keys": keys},
        )


def downgrade() -> None:
    conn = op.get_bind()
    all_keys = [key for keys in _REASSIGNMENTS.values() for key in keys]
    conn.execute(
        sa.text("UPDATE notification_templates SET domain = 'VOICE' WHERE key = ANY(:keys)"),
        {"keys": all_keys},
    )
