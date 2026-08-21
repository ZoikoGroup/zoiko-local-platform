"""seed the 39 internal operational alert templates (Email Communications System doc section 10)

Revision ID: 66711565c20f
Revises: 0f20fbb33954
Create Date: 2026-08-18 12:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66711565c20f'
down_revision: Union[str, None] = '0f20fbb33954'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Email Communications System doc §10 "Internal Operational Alerts" - the
# 39-template estate that was 0% built (the 195-template customer-facing
# estate, seeded across earlier migrations, is a complete match). Different
# audience and delivery path from every other template here: staff, not
# customers - see notifications.service.send_internal_alert.
#
# Subject lines are the doc's exact text, with its {{x.y}} mustache
# variables flattened to {x_y} (this codebase's existing .format(**context)
# convention - see any BILL/ORG template above). Body templates are
# deliberately generic ({summary} + {console_link}) rather than 39 bespoke
# multi-field bodies: the doc's own "REQUIRED BODY" text is a list of
# content categories an alert must cover ("include tenant, account
# reference, detected time..."), not literal copy - the caller composes
# `summary` from the real facts of the real event, same posture already
# used for HTML `alt`-text-style descriptive fields elsewhere. Only 3 of
# 39 are wired to a real, already-existing event with zero prior staff
# visibility (see send_internal_alert's call sites) - the rest are seeded
# for registry completeness, same "seed the estate, wire what's real"
# discipline the ORG/TRUST/etc. customer-facing migrations already used.
_ALERTS = [
    # (key, canonical_id, domain, category, priority, subject_template)
    ('sec_int.account_takeover', 'ZLOC-EM-SEC-INT-001', 'SEC_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Zoiko Local account takeover — {case_reference}'),
    ('sec_int.authentication_abuse_spike', 'ZLOC-EM-SEC-INT-002', 'SEC_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Authentication abuse threshold exceeded'),
    ('sec_int.privileged_access_anomaly', 'ZLOC-EM-SEC-INT-003', 'SEC_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Privileged access anomaly — {case_reference}'),

    ('voice_int.emergency_call_failure', 'ZLOC-EM-VOICE-INT-001', 'VOICE_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[SEV-1] Emergency call failure — {incident_reference}'),
    ('voice_int.carrier_route_degradation', 'ZLOC-EM-VOICE-INT-002', 'VOICE_INT', 'TRANSACTIONAL', 'STANDARD',
     '[SEV-2] Carrier route degradation — {incident_region_safe}'),
    ('voice_int.call_completion_collapse', 'ZLOC-EM-VOICE-INT-003', 'VOICE_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[SEV-1] Call completion rate collapse'),
    ('voice_int.sip_trunk_outage', 'ZLOC-EM-VOICE-INT-004', 'VOICE_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[SEV-1] SIP trunk outage — {trunk_reference}'),

    ('num_int.provisioning_backlog', 'ZLOC-EM-NUM-INT-001', 'NUM_INT', 'TRANSACTIONAL', 'STANDARD',
     'Number provisioning SLA breach'),
    ('num_int.inventory_low', 'ZLOC-EM-NUM-INT-002', 'NUM_INT', 'TRANSACTIONAL', 'STANDARD',
     'Number inventory threshold reached — {market_name}'),
    ('num_int.allocation_conflict', 'ZLOC-EM-NUM-INT-003', 'NUM_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[SEV-1] Number allocation conflict — {number_masked_or_formatted}'),
    ('num_int.reclamation_risk', 'ZLOC-EM-NUM-INT-004', 'NUM_INT', 'TRANSACTIONAL', 'STANDARD',
     'Number reclamation deadline approaching'),

    ('port_int.sla_breach', 'ZLOC-EM-PORT-INT-001', 'PORT_INT', 'TRANSACTIONAL', 'STANDARD',
     'Porting SLA breach — {port_reference}'),
    ('port_int.rejection_spike', 'ZLOC-EM-PORT-INT-002', 'PORT_INT', 'TRANSACTIONAL', 'STANDARD',
     'Port rejection spike — {carrier_name}'),
    ('port_int.confirmed_port_at_risk', 'ZLOC-EM-PORT-INT-003', 'PORT_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[URGENT] Confirmed port at risk — {port_reference}'),
    ('port_int.unauthorized_port_out_signal', 'ZLOC-EM-PORT-INT-004', 'PORT_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Possible unauthorized port-out — {case_reference}'),

    ('trust_int.caller_auth_downgrade', 'ZLOC-EM-TRUST-INT-001', 'TRUST_INT', 'SECURITY', 'STANDARD',
     'Caller identity attestation downgrade'),
    ('trust_int.robocall_complaint_spike', 'ZLOC-EM-TRUST-INT-002', 'TRUST_INT', 'SECURITY', 'CRITICAL',
     '[URGENT] Robocall complaint spike — {scope_summary}'),
    ('trust_int.fraud_spend_spike', 'ZLOC-EM-TRUST-INT-003', 'TRUST_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Calling fraud spend spike — {tenant_reference}'),

    ('msg_int.delivery_degradation', 'ZLOC-EM-MSG-INT-001', 'MSG_INT', 'TRANSACTIONAL', 'STANDARD',
     'Messaging delivery degradation — {market_name}'),
    ('msg_int.opt_out_suppression_failure', 'ZLOC-EM-MSG-INT-002', 'MSG_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Messaging opt-out suppression failure'),
    ('msg_int.registration_rejection_spike', 'ZLOC-EM-MSG-INT-003', 'MSG_INT', 'TRANSACTIONAL', 'STANDARD',
     'Messaging registration rejection spike'),

    ('rec_int.pipeline_failure', 'ZLOC-EM-REC-INT-001', 'REC_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Call recording pipeline failure'),
    ('rec_int.access_anomaly', 'ZLOC-EM-REC-INT-002', 'REC_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Recording access anomaly — {case_reference}'),

    ('route_int.publish_failure', 'ZLOC-EM-ROUTE-INT-001', 'ROUTE_INT', 'TRANSACTIONAL', 'STANDARD',
     'Call-flow publication failed — {route_reference}'),
    ('route_int.no_reachable_route_spike', 'ZLOC-EM-ROUTE-INT-002', 'ROUTE_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[SEV-1] Unreachable routing spike'),

    ('intg_int.webhook_backlog', 'ZLOC-EM-INTG-INT-001', 'INTG_INT', 'TRANSACTIONAL', 'STANDARD',
     'Zoiko Local webhook backlog'),
    ('intg_int.api_abuse_or_credential_leak', 'ZLOC-EM-INTG-INT-002', 'INTG_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] API credential abuse — {case_reference}'),

    ('bill_int.reconciliation_exception', 'ZLOC-EM-BILL-INT-001', 'BILL_INT', 'TRANSACTIONAL', 'STANDARD',
     'Billing reconciliation exception — {transaction_reference}'),
    ('bill_int.payment_failure_spike', 'ZLOC-EM-BILL-INT-002', 'BILL_INT', 'TRANSACTIONAL', 'STANDARD',
     'Payment failure spike — {provider_name}'),
    ('bill_int.partner_credit_exposure', 'ZLOC-EM-BILL-INT-003', 'BILL_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[URGENT] Partner credit exposure — {partner_reference}'),

    ('comp_int.review_backlog', 'ZLOC-EM-COMP-INT-001', 'COMP_INT', 'TRANSACTIONAL', 'STANDARD',
     'Compliance review SLA breach — {queue_name}'),
    ('comp_int.emergency_address_validation_failure', 'ZLOC-EM-COMP-INT-002', 'COMP_INT', 'TRANSACTIONAL', 'STANDARD',
     'Emergency address validation failure spike'),

    ('priv_int.suspected_privacy_incident', 'ZLOC-EM-PRIV-INT-001', 'PRIV_INT', 'SECURITY', 'CRITICAL',
     '[SEV-1] Suspected Zoiko Local privacy incident — {incident_reference}'),

    ('ops_int.critical_email_delivery_failure', 'ZLOC-EM-OPS-INT-001', 'OPS_INT', 'TRANSACTIONAL', 'CRITICAL',
     'Critical Zoiko Local email delivery failure — {delivery_reference}'),
    ('ops_int.communications_backlog', 'ZLOC-EM-OPS-INT-002', 'OPS_INT', 'TRANSACTIONAL', 'STANDARD',
     'Zoiko Local communications queue outside SLA'),
    ('ops_int.deliverability_threshold_exceeded', 'ZLOC-EM-OPS-INT-003', 'OPS_INT', 'TRANSACTIONAL', 'STANDARD',
     'Zoiko Local email reputation threshold exceeded'),
    ('ops_int.production_incident', 'ZLOC-EM-OPS-INT-004', 'OPS_INT', 'TRANSACTIONAL', 'CRITICAL',
     '[{incident_severity}] Zoiko Local incident — {incident_reference}'),
    ('ops_int.status_publication_failure', 'ZLOC-EM-OPS-INT-005', 'OPS_INT', 'TRANSACTIONAL', 'STANDARD',
     'Status publication failed — {incident_reference}'),

    ('sup_int.sla_breach', 'ZLOC-EM-SUP-INT-001', 'SUP_INT', 'TRANSACTIONAL', 'STANDARD',
     'Zoiko Local support SLA breach — {queue_name}'),
]

_BODY_TEMPLATE = "{summary}\n\nConsole: {console_link}"


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
                'key': key,
                'canonical_id': canonical_id,
                'domain': domain,
                'spec_version': '1.0.0',
                'category': category,
                'priority': priority,
                'subject_template': subject,
                'body_template': _BODY_TEMPLATE,
            }
            for key, canonical_id, domain, category, priority, subject in _ALERTS
        ],
    )


def downgrade() -> None:
    keys = [row[0] for row in _ALERTS]
    placeholders = ", ".join(f"'{k}'" for k in keys)
    op.execute(f"DELETE FROM notification_templates WHERE key IN ({placeholders})")
