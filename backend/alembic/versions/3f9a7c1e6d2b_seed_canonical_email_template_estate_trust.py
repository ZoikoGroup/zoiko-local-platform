"""seed canonical email template estate - TRUST domain

Revision ID: 3f9a7c1e6d2b
Revises: f5cd5f74d272
Create Date: 2026-08-07 12:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f9a7c1e6d2b'
down_revision: Union[str, None] = 'f5cd5f74d272'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeds the Email Communications System spec doc's TRUST domain (10
# templates - "trust and safety"). Wasn't wireable when the original
# domains were imported, since account-level fraud/risk scoring and
# auto-suspend (app/risk/service.py) didn't exist yet. Only 2 of 10 have
# a real call site: trust.account_warning (a fraud case opening at
# REVIEW_THRESHOLD - app.risk.service.open_fraud_case_if_needed) and
# trust.account_suspended_or_disabled (crossing AUTO_SUSPEND_THRESHOLD -
# app.risk.service.maybe_auto_suspend_for_risk). The other 8 (abuse
# report submission/outcome, partial capability restriction, appeal
# submission/decision, compromised-account protective action, data
# export, formal security/privacy incident notices) have no matching
# feature yet - there's no customer-facing abuse-report or appeal
# submission flow, no partial (as opposed to full) capability
# restriction, no data export tool, and no incident-response process to
# hook into - seeded for registry completeness only, not fabricated
# triggers.


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
            'key': 'trust.abuse_report_received',
            'canonical_id': 'ZLOC-EM-TRUST-001',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'We received your Zoiko Local report',
            'body_template': 'Report received\n\nHello {user_display_name}, we received your report concerning {case_category}. Reference: {case_reference}. For privacy and investigation integrity, we may not disclose every action or finding.\n\nNext: View Report Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.abuse_report_outcome',
            'canonical_id': 'ZLOC-EM-TRUST-002',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Update on your Zoiko Local report',
            'body_template': 'Report review completed\n\nHello {user_display_name}, review of case {case_reference} is complete. Outcome: {case_action_summary}. You may still block numbers, restrict callers, change routing, or contact appropriate authorities where necessary.\n\nNext: Review Outcome.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.account_warning',
            'canonical_id': 'ZLOC-EM-TRUST-003',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Warning concerning your Zoiko Local account',
            'body_template': 'Account warning\n\nHello {user_display_name}, activity associated with {scope_summary} did not meet the Zoiko Local Acceptable Use Policy. Policy area: {decision_policy_area}. Reference: {case_reference}. Repeated or severe issues may result in restrictions.\n\nNext: Review Warning.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.service_capability_restricted',
            'canonical_id': 'ZLOC-EM-TRUST-004',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'A Zoiko Local capability was restricted',
            'body_template': 'Service capability restricted\n\nHello {user_display_name}, {restriction_feature_name} for {scope_summary} was restricted effective {restriction_start_at}. Reason category: {decision_reason_category}. End or review date: {restriction_end_at_or_review}.\n\nNext: Review Restriction.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.account_suspended_or_disabled',
            'canonical_id': 'ZLOC-EM-TRUST-005',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Your Zoiko Local account status changed',
            'body_template': 'Account access restricted\n\nHello {user_display_name}, your account was {restriction_status} effective {restriction_start_at}. Reason: {decision_reason_category}. Reference: {case_reference}. Do not create another account to evade an active restriction.\n\nNext: Review Account Decision.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.appeal_received',
            'canonical_id': 'ZLOC-EM-TRUST-006',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'We received your Zoiko Local appeal',
            'body_template': 'Appeal received\n\nHello {user_display_name}, we received your appeal concerning {case_original_action}. The original action remains effective unless the decision page states otherwise.\n\nNext: View Appeal Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.appeal_decision',
            'canonical_id': 'ZLOC-EM-TRUST-007',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Decision on your Zoiko Local appeal',
            'body_template': 'Appeal review completed\n\nHello {user_display_name}, appeal {case_reference} is complete. Outcome: {case_action_summary}. Restoration of numbers, routes, messaging, integrations, or billing may require separate technical processing.\n\nNext: Review Appeal Outcome.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.compromised_account_protected',
            'canonical_id': 'ZLOC-EM-TRUST-008',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'We secured your Zoiko Local account',
            'body_template': 'Protective action was taken\n\nHello {user_display_name}, we detected possible unauthorized access and may have signed out sessions, blocked calling or messages, rotated credentials, paused porting, or restricted administrator actions.\n\nNext: Recover My Account.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.data_export_ready',
            'canonical_id': 'ZLOC-EM-TRUST-009',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local data export is ready',
            'body_template': 'Data archive available\n\nHello {user_display_name}, export request {case_reference} is ready until {export_expires_at}. Reauthentication is required. The archive may contain call metadata, messages, configuration, billing, or recordings according to authorization and legal limits.\n\nNext: Download Data Archive.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'trust.security_or_privacy_incident_notice',
            'canonical_id': 'ZLOC-EM-TRUST-010',
            'domain': 'TRUST',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Important security notice about your Zoiko Local information',
            'body_template': 'A security incident may have involved your information\n\nHello {user_display_name}, {breach_summary} Information involved: {breach_data_categories}. Incident period: {breach_incident_window}. Actions taken: {breach_remediation_summary}. Recommended steps: {breach_recommended_actions}. Read the full jurisdiction-approved notice securely.\n\nNext: Read the Full Notice.',
        },
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_templates WHERE domain = 'TRUST'"))
