"""add incidents and status subscriptions, seed OPS email domain

Revision ID: b8d4e2f6a1c9
Revises: 3f9a7c1e6d2b
Create Date: 2026-08-07 14:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'b8d4e2f6a1c9'
down_revision: Union[str, None] = '3f9a7c1e6d2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeds the Email Communications System spec doc's OPS domain (9
# templates - "service operations"). Only 4 of 9 have a real call site:
# ops.service_incident_declared / ops.incident_update / ops.incident_resolved
# (app.ops.service.create_incident/update_incident/resolve_incident, a new
# real Incident model - the public status page previously had no persisted
# incident record to notify subscribers about) and
# ops.status_subscription_confirmation (app.ops.service.subscribe_to_status).
# The other 5 (scheduled maintenance, emergency-calling-specific incident
# framing, carrier/regional degradation framing, material product change,
# data residency/region migration) have no matching feature yet - there is
# no maintenance-scheduling concept, no incident sub-classification, no
# product-change-announcement tool, and no region-migration workflow -
# seeded for registry completeness only, not fabricated triggers.


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("affected_service", sa.String(length=255), nullable=False),
        sa.Column(
            "status", sa.Enum("INVESTIGATING", "MONITORING", "RESOLVED", name="incident_status_enum"),
            nullable=False, server_default="INVESTIGATING",
        ),
        sa.Column("impact_summary", sa.Text(), nullable=False),
        sa.Column("mitigation_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "status_subscriptions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("account_id", UUID(as_uuid=False), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_status_subscriptions_account_id", "status_subscriptions", ["account_id"])

    templates_table = sa.table(
        "notification_templates",
        sa.column("id", sa.String),
        sa.column("key", sa.String),
        sa.column("canonical_id", sa.String),
        sa.column("domain", sa.String),
        sa.column("spec_version", sa.String),
        sa.column("category", sa.String),
        sa.column("priority", sa.String),
        sa.column("subject_template", sa.String),
        sa.column("body_template", sa.Text),
    )
    op.bulk_insert(
        templates_table,
        [
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.scheduled_maintenance',
            'canonical_id': 'ZLOC-EM-OPS-001',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Scheduled Zoiko Local maintenance',
            'body_template': 'Scheduled maintenance\n\nHello {user_display_name}, maintenance is scheduled from {maintenance_start_local} to {maintenance_end_local} {maintenance_timezone}. Affected services: {maintenance_affected_services}. Emergency-calling treatment and any required preparation are stated on the status page.\n\nNext: View Service Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.service_incident_declared',
            'canonical_id': 'ZLOC-EM-OPS-002',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local service incident',
            'body_template': 'We are investigating a service issue\n\nHello {user_display_name}, an incident began at {incident_started_local} and is affecting {incident_impact_summary}. Current status: {incident_status}. Avoid repeated port, payment, message, or configuration submissions unless instructed.\n\nNext: View Current Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.incident_update',
            'canonical_id': 'ZLOC-EM-OPS-003',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Update on a Zoiko Local service incident',
            'body_template': 'Service incident update\n\nHello {user_display_name}, incident {incident_reference} is now {incident_status}. Current impact: {incident_impact_summary}. Mitigation: {incident_mitigation_summary}. Next update: {incident_next_update_at}.\n\nNext: View Incident.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.incident_resolved',
            'canonical_id': 'ZLOC-EM-OPS-004',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local service incident resolved',
            'body_template': 'Service restored\n\nHello {user_display_name}, incident {incident_reference} was resolved at {incident_resolved_local}. Impact period: {incident_duration_summary}. A post-incident review will be published where appropriate.\n\nNext: View Incident Record.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.emergency_service_incident',
            'canonical_id': 'ZLOC-EM-OPS-005',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Urgent Zoiko Local emergency-calling notice',
            'body_template': 'Emergency-calling service issue\n\nHello {user_display_name}, emergency-calling capability for {scope_summary} may be unavailable or impaired. Impact: {incident_impact_summary}. Until resolved, use another available telephone service to contact emergency organizations.\n\nNext: View Emergency Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.carrier_or_regional_degradation',
            'canonical_id': 'ZLOC-EM-OPS-006',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local service degradation in {incident_region_safe}',
            'body_template': 'Regional service degradation\n\nHello {user_display_name}, an underlying provider issue is affecting {incident_affected_service} in {incident_region_safe}. Expected symptoms: {incident_impact_summary}. Failover is {incident_failover_status}.\n\nNext: View Regional Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.material_product_change',
            'canonical_id': 'ZLOC-EM-OPS-007',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Important change to a Zoiko Local service',
            'body_template': 'A Zoiko Local capability is changing\n\nHello {user_display_name}, {product_feature_name} changes on {product_effective_date}. What is changing: {product_change_summary}. Required action: {product_required_action}.\n\nNext: Review Product Change.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.data_residency_or_region_migration',
            'canonical_id': 'ZLOC-EM-OPS-008',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local service-region change',
            'body_template': 'Service region is changing\n\nHello {user_display_name}, {scope_summary} is scheduled to move from {migration_source_region} to {migration_target_region} on {migration_start_at}. Impact: {migration_impact_summary}. Legal, retention, and customer obligations remain governed by the approved migration plan.\n\nNext: Review Migration.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'ops.status_subscription_confirmation',
            'canonical_id': 'ZLOC-EM-OPS-009',
            'domain': 'OPS',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local status subscription is active',
            'body_template': 'Status notifications enabled\n\nHello {user_display_name}, status updates are enabled for {status_subscription_summary}. Change or end this subscription at any time using the direct link.\n\nNext: Manage Status Subscription.',
        },
        ],
    )
    # The ops.manage_incidents capability grant (SUPPORT + SUPER_ADMIN) is
    # seeded by the next migration (af272e35f51c), not here - both were
    # written concurrently and would otherwise double-insert the same rows
    # and violate uq_staff_capability_grant.


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_templates WHERE domain = 'OPS'"))
    op.drop_index("ix_status_subscriptions_account_id", table_name="status_subscriptions")
    op.drop_table("status_subscriptions")
    op.drop_table("incidents")
