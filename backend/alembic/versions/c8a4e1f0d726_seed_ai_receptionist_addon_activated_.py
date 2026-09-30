"""Bug ZL-6 (retest, addon variant): seed billing.ai_receptionist_addon_activated template

Revision ID: c8a4e1f0d726
Revises: b3f7a2e8c451
Create Date: 2026-09-30 10:00:00.000000

Tester-reported: enabling the AI Receptionist add-on (via a real, paid
Stripe checkout - confirmed live, disable/re-enable round trip) sends no
confirmation email at all. Unlike a full plan change (change_plan already
calls notify_plan_changed), app.billing.service.set_ai_receptionist_addon
never called any notify_* function for either direction - this gap
existed from when the add-on feature was first built, not something the
ZL-8 payment-gating fix introduced. This is a new template, not part of
the Email Communications System doc's original 234-item estate (the doc
predates this add-on's checkout-based purchase flow) - canonical_id
follows the existing BILL domain's numbering for consistency, not because
it's cited by that doc.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8a4e1f0d726'
down_revision: Union[str, None] = 'b3f7a2e8c451'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    templates_table = sa.table(
        'notification_templates',
        sa.column('id', sa.String),
        sa.column('key', sa.String),
        sa.column('canonical_id', sa.String),
        sa.column('domain', sa.String),
        sa.column('spec_version', sa.String),
        sa.column('category', sa.String),
        sa.column('priority', sa.String),
        sa.column('subject_template', sa.String),
        sa.column('body_template', sa.Text),
    )
    op.bulk_insert(
        templates_table,
        [
            {
                'id': str(uuid.uuid4()),
                'key': 'billing.ai_receptionist_addon_activated',
                'canonical_id': 'ZLOC-EM-BILL-023',
                'domain': 'BILL',
                'spec_version': '1.0.0',
                'category': 'TRANSACTIONAL',
                'priority': 'STANDARD',
                'subject_template': 'AI Receptionist is now active on your Zoiko Local plan',
                'body_template': 'AI Receptionist add-on activated\n\nHello {user_display_name}, the AI Receptionist add-on for {organization_name} is now active - {addon_included_minutes} AI-handled minutes are included per month. It will start answering calls per each number\'s own AI Receptionist settings.\n\nNext: Review AI Receptionist Settings.',
            },
        ],
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM notification_templates WHERE key = 'billing.ai_receptionist_addon_activated'"),
    )
