"""seed number eligibility case approved/rejected notification templates

Revision ID: 9f4b2a7c1e83
Revises: 7c2e9a48b1d5
Create Date: 2026-08-25 00:00:00.000000

The Twilio Regulatory Bundle flow (see NumberEligibilityCase's docstring)
already notified the customer when documents were required
(number.verification_required), but had no template for the two outcomes
that actually resolve the case: Twilio approving or rejecting the
submission. Without these two rows, send_notification() raises
NotificationTemplateMissingError the first time either outcome fires -
a real gap, not a placeholder. canonical_id is left NULL (not invented):
these two postdate the Email Communications System spec doc's 195-family
registry and have no entry there, same as any other custom template per
NotificationTemplate.canonical_id's own docstring.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f4b2a7c1e83'
down_revision: Union[str, None] = '7c2e9a48b1d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TEMPLATES = [
    {
        'id': str(uuid.uuid4()),
        'key': 'number.eligibility_case_approved',
        'canonical_id': None,
        'domain': 'NUM',
        'spec_version': None,
        'category': 'TRANSACTIONAL',
        'priority': 'STANDARD',
        'subject_template': 'You are cleared to complete your Zoiko Local number purchase',
        'body_template': (
            'Eligibility review approved\n\n'
            'Hello {user_display_name}, Twilio has approved the identity review for your '
            '{case_number_type} number request in {case_country}. Go back to Numbers in your '
            'dashboard to complete the purchase.\n\n'
            'Next: Complete Number Purchase.'
        ),
    },
    {
        'id': str(uuid.uuid4()),
        'key': 'number.eligibility_case_rejected',
        'canonical_id': None,
        'domain': 'NUM',
        'spec_version': None,
        'category': 'TRANSACTIONAL',
        'priority': 'STANDARD',
        'subject_template': 'Your Zoiko Local number verification was not approved',
        'body_template': (
            'Eligibility review rejected\n\n'
            'Hello {user_display_name}, Twilio could not approve the identity review for your '
            '{case_number_type} number request in {case_country}. Reason: {case_rejection_reason}. '
            'Correct the required information and resubmit from your dashboard.\n\n'
            'Next: Review Eligibility Case.'
        ),
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    table = sa.table(
        'notification_templates',
        sa.column('id', sa.String),
        sa.column('key', sa.String),
        sa.column('canonical_id', sa.String),
        sa.column('domain', sa.String),
        sa.column('spec_version', sa.String),
        sa.column('category', sa.String),
        sa.column('priority', sa.String),
        sa.column('subject_template', sa.String),
        sa.column('body_template', sa.String),
    )
    conn.execute(table.insert(), TEMPLATES)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM notification_templates WHERE key IN "
            "('number.eligibility_case_approved', 'number.eligibility_case_rejected')"
        )
    )
