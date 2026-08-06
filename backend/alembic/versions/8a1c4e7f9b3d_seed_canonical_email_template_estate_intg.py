"""seed canonical email template estate - INTG domain

Revision ID: 8a1c4e7f9b3d
Revises: 75fa64bbaa08
Create Date: 2026-08-06 14:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a1c4e7f9b3d'
down_revision: Union[str, None] = '75fa64bbaa08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeds the Email Communications System spec doc's INTG domain (10
# templates - "APIs, webhooks and integrations"). Wasn't importable when
# the original 7-domain estate was built, since none of API keys/
# webhooks/CRM existed yet - they do now. Only 4 of the 10 have a real
# call site wired up (see app/notifications/service.py's
# notify_api_client_created/notify_webhook_endpoint_added/
# notify_integration_installed/notify_integration_removed) - the rest
# (client/credential rotation, webhook verification, consecutive-failure
# and recovery tracking, authorization expiry) have no matching feature
# and are seeded for registry completeness only. Deliberately not wired:
# webhook delivery failure/recovery would need per-endpoint consecutive-
# failure tracking that does not exist, and firing on every single
# failed delivery would be notification spam.


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
            'key': 'intg.api_client_created',
            'canonical_id': 'ZLOC-EM-INTG-001',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local API client was created',
            'body_template': 'API client created\n\nHello {user_display_name}, API client {integration_name} was created in {environment} by {actor_display_name}. Scopes: {integration_scope_summary}. Client secrets are shown only once in the secure Developer Center.\n\nNext: Review API Client.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.api_client_changed',
            'canonical_id': 'ZLOC-EM-INTG-002',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local API client changed',
            'body_template': 'API access updated\n\nHello {user_display_name}, {integration_name} changed on {event_occurred_at}. Change: {integration_change_summary}. Revalidate least privilege and production authorization.\n\nNext: Review API Access.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.api_credential_rotated',
            'canonical_id': 'ZLOC-EM-INTG-003',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local integration credentials were rotated',
            'body_template': 'Integration credential rotation\n\nHello {user_display_name}, credentials for {integration_name} were rotated. Existing credential expiry: {security_old_secret_expires_at}. Retrieve the replacement securely; no secret is included in this email.\n\nNext: Retrieve Credential.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.webhook_endpoint_added',
            'canonical_id': 'ZLOC-EM-INTG-004',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local webhook configuration changed',
            'body_template': 'Webhook updated\n\nHello {user_display_name}, webhook {integration_name} was {change_action} by {actor_display_name}. Destination: {integration_destination_safe}. Events: {integration_event_summary}. Signing: {integration_signing_status}.\n\nNext: Review Webhook.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.webhook_verification_failed',
            'canonical_id': 'ZLOC-EM-INTG-005',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local could not verify a webhook endpoint',
            'body_template': 'Webhook verification failed\n\nHello {user_display_name}, verification failed for {integration_name}. Reason: {integration_failure_summary}. Production delivery remains disabled until verification passes.\n\nNext: Fix Webhook.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.webhook_delivery_failing',
            'canonical_id': 'ZLOC-EM-INTG-006',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local webhook deliveries are failing',
            'body_template': 'Webhook delivery alert\n\nHello {user_display_name}, {integration_name} has {integration_failure_count} failed deliveries since {integration_window_start}. Oldest pending event: {integration_oldest_pending_age}. Retry and retention behavior is shown in the Developer Center.\n\nNext: Review Delivery Failures.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.webhook_recovered',
            'canonical_id': 'ZLOC-EM-INTG-007',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local webhook delivery recovered',
            'body_template': 'Webhook healthy again\n\nHello {user_display_name}, {integration_name} resumed successful delivery at {event_occurred_at}. Events that reached terminal failure remain visible for controlled replay where supported.\n\nNext: Review Webhook History.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.integration_installed',
            'canonical_id': 'ZLOC-EM-INTG-008',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'An integration was connected to Zoiko Local',
            'body_template': 'Integration installed\n\nHello {user_display_name}, {integration_name} was connected to {organization_name} by {actor_display_name}. Access: {integration_scope_summary}. Data sharing follows the integration, tenant, and legal configuration.\n\nNext: Review Integration.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.integration_removed',
            'canonical_id': 'ZLOC-EM-INTG-009',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'An integration was removed from Zoiko Local',
            'body_template': 'Integration disconnected\n\nHello {user_display_name}, {integration_name} was disconnected from {organization_name} on {event_occurred_at}. External copies, automation, and credentials must be handled in the connected service separately.\n\nNext: Review Integrations.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'intg.integration_authorization_expiring',
            'canonical_id': 'ZLOC-EM-INTG-010',
            'domain': 'INTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local integration authorization expires soon',
            'body_template': 'Integration action required\n\nHello {user_display_name}, authorization for {integration_name} expires on {case_review_deadline}. Affected capabilities: {integration_scope_summary}.\n\nNext: Renew Authorization.',
        },
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_templates WHERE domain = 'INTG'"))
