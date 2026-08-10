"""seed canonical email template estate - ORG domain

Revision ID: 3d7e9a1b5c2f
Revises: 8f2a1c9d4e6b
Create Date: 2026-08-06 13:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d7e9a1b5c2f'
down_revision: Union[str, None] = '8f2a1c9d4e6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeds the Email Communications System spec doc's ORG domain (14 templates
# - "organizations and access"). Team management (add/remove member) is a
# real, already-built feature, so this domain belongs alongside the six
# imported earlier. Only 3 of the 14 have a real call site wired up (see
# app/notifications/service.py's notify_organization_verification_submitted/
# notify_administrator_added/notify_administrator_removed) - the rest have
# no matching feature (invite-with-acceptance flow, role changes, user
# suspend/reactivate, ownership transfer, org closure all don't exist yet)
# and are seeded for registry completeness only. ORG-004 "Organization
# Created" is deliberately NOT wired despite account signup being real -
# AUTH-003 "Account Activated" already covers that exact moment, and firing
# both would send two near-duplicate emails for one signup.


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
            'key': 'org.organization_invitation',
            'canonical_id': 'ZLOC-EM-ORG-001',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'You are invited to join {organization_name} on Zoiko Local',
            'body_template': 'Organization invitation\n\nHello {user_display_name}, {inviter_sanitized_name} invited you to join {organization_name} as {role_name}. Review the organization and permissions before accepting. The invitation expires on {invitation_expires_at}.\n\nNext: Review Invitation.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.invitation_accepted',
            'canonical_id': 'ZLOC-EM-ORG-002',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': '{user_display_name} joined {organization_name}',
            'body_template': 'New organization member\n\nHello {admin_display_name}, {user_display_name} accepted the invitation to join {organization_name} as {role_name} on {event_occurred_at}.\n\nNext: View Organization Members.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.invitation_declined_or_expired',
            'canonical_id': 'ZLOC-EM-ORG-003',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'An invitation to {organization_name} was not accepted',
            'body_template': 'Organization invitation closed\n\nHello {admin_display_name}, the invitation for {recipient_email_masked} was {invitation_final_status}. No organization access was granted.\n\nNext: View Invitations.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.organization_created',
            'canonical_id': 'ZLOC-EM-ORG-004',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': '{organization_name} is ready in Zoiko Local',
            'body_template': 'Your organization workspace is ready\n\nHello {user_display_name}, {organization_name} has been created. Next, confirm company details, administrators, billing, emergency-calling configuration, number requirements, and call-routing ownership.\n\nNext: Complete Organization Setup.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.verification_submitted',
            'canonical_id': 'ZLOC-EM-ORG-005',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'We received the verification for {organization_name}',
            'body_template': 'Verification submitted\n\nHello {user_display_name}, we received the business and authority information for {organization_name}. Reference: {case_reference}. Upload additional evidence only through the secure verification center.\n\nNext: View Verification Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.organization_verification_decision',
            'canonical_id': 'ZLOC-EM-ORG-006',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Decision on {organization_name} verification',
            'body_template': 'Organization verification updated\n\nHello {user_display_name}, the verification status for {organization_name} is {verification_status}. Reason or next step: {case_action_summary}. Verified status does not replace local licensing, numbering, tax, or emergency-service obligations.\n\nNext: Review Verification Decision.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.administrator_added',
            'canonical_id': 'ZLOC-EM-ORG-007',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Administrator access was granted in {organization_name}',
            'body_template': 'Administrator added\n\nHello {user_display_name}, {actor_display_name} was granted {role_name} access in {organization_name} on {event_occurred_at}. If this was not authorized, remove the access and contact security.\n\nNext: Review Administrators.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.administrator_removed',
            'canonical_id': 'ZLOC-EM-ORG-008',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Administrator access changed in {organization_name}',
            'body_template': 'Administrator removed\n\nHello {user_display_name}, {actor_display_name} no longer has {role_name} access in {organization_name}. Assigned resources and open approvals must be reassigned where necessary.\n\nNext: Review Administrators.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.role_or_permission_changed',
            'canonical_id': 'ZLOC-EM-ORG-009',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local permissions changed',
            'body_template': 'Organization access updated\n\nHello {user_display_name}, your role in {organization_name} changed from {role_previous_name} to {role_name}. Current permissions: {role_permission_summary}.\n\nNext: Review My Access.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.user_suspended',
            'canonical_id': 'ZLOC-EM-ORG-010',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Your access to {organization_name} was suspended',
            'body_template': 'Organization access suspended\n\nHello {user_display_name}, your access to {organization_name} was suspended effective {restriction_start_at}. Your personal account may remain available, but organization numbers, calls, messages, and records are restricted.\n\nNext: Review Access Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.user_reactivated',
            'canonical_id': 'ZLOC-EM-ORG-011',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your access to {organization_name} was restored',
            'body_template': 'Organization access restored\n\nHello {user_display_name}, your access to {organization_name} has been restored. Review assigned numbers, devices, call queues, routing, and recording permissions before resuming work.\n\nNext: Open Organization.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.organization_ownership_transfer_requested',
            'canonical_id': 'ZLOC-EM-ORG-012',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Ownership transfer requested for {organization_name}',
            'body_template': 'Organization ownership transfer\n\nHello {user_display_name}, an ownership transfer was requested for {organization_name}. Proposed owner: {transfer_proposed_owner}. The transfer remains pending until all required approvals and identity checks are complete.\n\nNext: Review Ownership Transfer.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.organization_ownership_transferred',
            'canonical_id': 'ZLOC-EM-ORG-013',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Ownership of {organization_name} was transferred',
            'body_template': 'Organization ownership updated\n\nHello {user_display_name}, ownership of {organization_name} transferred to {transfer_new_owner} on {event_occurred_at}. Existing administrators, billing authority, number ownership, and regulatory responsibilities should be reviewed immediately.\n\nNext: Review Organization Governance.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'org.organization_closure_scheduled_or_completed',
            'canonical_id': 'ZLOC-EM-ORG-014',
            'domain': 'ORG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': '{organization_name} closure status changed',
            'body_template': 'Organization closure update\n\nHello {user_display_name}, {organization_name} is {closure_status} effective {closure_effective_at}. Complete required number porting or release, settle balances, export eligible records, preserve regulated data, and remove integrations before closure.\n\nNext: Review Closure Plan.',
        },
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_templates WHERE domain = 'ORG'"))
