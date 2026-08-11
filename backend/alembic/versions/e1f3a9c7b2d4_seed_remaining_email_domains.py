"""seed remaining canonical email template estate domains - DEVICE SUP MKTG PART

Revision ID: e1f3a9c7b2d4
Revises: af272e35f51c
Create Date: 2026-08-11 15:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f3a9c7b2d4'
down_revision: Union[str, None] = 'af272e35f51c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeds the last 4 customer-facing domains of the Email Communications
# System spec doc's 234-template estate: DEVICE (11, SIP/desk-phone
# provisioning), SUP (5, support ticketing), MKTG (8, marketing/
# campaigns), PART (8, partner/reseller program) - 32 templates, all
# registry-only. None of these have ANY matching feature in this
# codebase - there is no desk-phone/SIP device concept (calling is
# browser/app-only), no support ticketing system, no marketing/campaign
# tool, and no partner/reseller program - so none can be wired to a real
# trigger without first building one of those as its own product
# feature. Seeded for registry completeness only, matching the "never
# fabricate a trigger" discipline used for every prior domain import
# this session.
#
# Deliberately NOT seeded here: the doc's separate "39 Internal and
# Administrative Alert Estate" (Section 10). Structurally different from
# every customer-facing domain above - each entry is a SUBJECT plus a
# "REQUIRED BODY" content BRIEF for a human to write from (e.g. "Include
# tenant, account reference, detected time, containment...") rather than
# finished template copy, and the doc frames it as internal security/ops
# paging, not customer email ("Internal email is an alert and routing
# mechanism, not an evidence container"). Inserting those briefs as
# literal body_template text would render nonsense to whoever received
# it. Would need a real internal paging/alerting system (e.g. SEV-1
# account-takeover pages to a security on-call) before this estate makes
# sense to import at all - out of scope here.


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
            'key': 'device.device_provisioning_invitation',
            'canonical_id': 'ZLOC-EM-DEVICE-001',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Set up your Zoiko Local calling device',
            'body_template': 'Connect your device\n\nHello {user_display_name}, {organization_name} assigned {device_name} to your Zoiko Local account. Complete provisioning from the intended device and confirm microphone, speaker, network, emergency location, and update settings.\n\nNext: Provision Device.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.device_provisioned',
            'canonical_id': 'ZLOC-EM-DEVICE-002',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'A device was connected to your Zoiko Local account',
            'body_template': 'Device connected\n\nHello {user_display_name}, {device_name} registered on {security_activity_time} from {security_approximate_location}. If you do not recognize it, revoke the device and secure your account.\n\nNext: Review Connected Devices.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.new_device_registration_blocked',
            'canonical_id': 'ZLOC-EM-DEVICE-003',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'We blocked a Zoiko Local device registration',
            'body_template': 'Device registration blocked\n\nHello {user_display_name}, registration of {device_name} was blocked on {security_activity_time}. Reason: {decision_reason_category}. The device cannot place or receive organization calls.\n\nNext: Review Device Security.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.device_offline',
            'canonical_id': 'ZLOC-EM-DEVICE-004',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local device is offline',
            'body_template': 'Device connectivity alert\n\nHello {user_display_name}, {device_name} assigned to {assignment_target} has been offline since {device_offline_since}. Other devices or failover routes may continue to receive calls.\n\nNext: Review Device.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.device_back_online',
            'canonical_id': 'ZLOC-EM-DEVICE-005',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local device is back online',
            'body_template': 'Device online\n\nHello {user_display_name}, {device_name} registered successfully at {event_occurred_at} after being offline for {device_outage_duration}.\n\nNext: View Device Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.firmware_or_application_update_required',
            'canonical_id': 'ZLOC-EM-DEVICE-006',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Update required for a Zoiko Local device',
            'body_template': 'Device update required\n\nHello {user_display_name}, {device_name} must update to {device_required_version}. Reason: {case_action_summary}. Calling may be restricted after the deadline if the device remains unsupported.\n\nNext: Review Update Instructions.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.device_assignment_changed',
            'canonical_id': 'ZLOC-EM-DEVICE-007',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'A Zoiko Local device assignment changed',
            'body_template': 'Device reassigned\n\nHello {user_display_name}, {device_name} changed from {assignment_previous_target} to {assignment_current_target} on {event_occurred_at}. Confirm emergency address and remove locally stored information before handover.\n\nNext: Review Device Assignment.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.device_removed',
            'canonical_id': 'ZLOC-EM-DEVICE-008',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'A device was removed from Zoiko Local',
            'body_template': 'Device access removed\n\nHello {user_display_name}, {device_name} was removed from {organization_name} on {event_occurred_at}. Locally retained call history or contacts must be handled under organization policy.\n\nNext: Review Connected Devices.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.sip_credential_rotated',
            'canonical_id': 'ZLOC-EM-DEVICE-009',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Zoiko Local SIP credentials were rotated',
            'body_template': 'SIP credential rotation\n\nHello {user_display_name}, credentials for {device_or_trunk_name} were rotated on {event_occurred_at}. Retrieve the new secret once from the secure console. Existing credentials expire on {security_old_secret_expires_at}.\n\nNext: Retrieve New Credential.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.sip_registration_anomaly',
            'canonical_id': 'ZLOC-EM-DEVICE-010',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'CRITICAL',
            'subject_template': 'Unusual Zoiko Local SIP registration activity',
            'body_template': 'SIP security alert\n\nHello {user_display_name}, unusual registration activity was detected for {device_or_trunk_name} at {security_activity_time}. Signal: {security_signal_summary}. Fraud controls may have restricted calling.\n\nNext: Review SIP Security.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'device.network_readiness_test_result',
            'canonical_id': 'ZLOC-EM-DEVICE-011',
            'domain': 'DEVICE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local network test is complete',
            'body_template': 'Network readiness result\n\nHello {user_display_name}, the test for {site_name} completed. Result: {quality_result}. Latency: {quality_latency}. Jitter: {quality_jitter}. Packet loss: {quality_packet_loss}. Firewall or media findings: {quality_action_summary}.\n\nNext: View Network Test.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'sup.support_request_received',
            'canonical_id': 'ZLOC-EM-SUP-001',
            'domain': 'SUP',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'We received your Zoiko Local support request',
            'body_template': 'Support request received\n\nHello {user_display_name}, we received your request about {case_category}. Reference: {case_reference}. Add information and review responses in the secure Help Center. Never send passwords, one-time codes, SIP secrets, full payment details, or identity documents by email.\n\nNext: View Support Case.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'sup.support_response_available',
            'canonical_id': 'ZLOC-EM-SUP-002',
            'domain': 'SUP',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local Support responded to case {case_reference}',
            'body_template': 'New support response\n\nHello {user_display_name}, a support specialist responded to your case about {case_category}. Open the Help Center for the full response and any requested diagnostic steps.\n\nNext: View Support Response.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'sup.support_case_resolved',
            'canonical_id': 'ZLOC-EM-SUP-003',
            'domain': 'SUP',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local case {case_reference} was resolved',
            'body_template': 'Support case resolved\n\nHello {user_display_name}, case {case_reference} was marked resolved. Resolution: {case_action_summary}. Reopen before {case_reopen_deadline} if the issue remains.\n\nNext: Review Resolution.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'sup.support_case_closing_for_inactivity',
            'canonical_id': 'ZLOC-EM-SUP-004',
            'domain': 'SUP',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local support case will close soon',
            'body_template': 'Do you still need assistance?\n\nHello {user_display_name}, we have not received a response for case {case_reference}. It will close on {case_close_date} unless you add information.\n\nNext: Continue Support Case.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'sup.support_experience_survey',
            'canonical_id': 'ZLOC-EM-SUP-005',
            'domain': 'SUP',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'How was your Zoiko Local support experience?',
            'body_template': 'Help us improve support\n\nHello {user_display_name}, tell us how we handled case {case_reference}. Your feedback does not change any billing, safety, porting, or regulatory decision.\n\nNext: Rate My Support Experience.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.product_update_newsletter',
            'canonical_id': 'ZLOC-EM-MKTG-001',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'What is new in Zoiko Local',
            'body_template': 'Zoiko Local product update\n\nHello {user_display_name}, this update covers {campaign_summary}. Availability depends on plan, market, carrier, permissions, and controlled rollout. Claims must match the approved Product and Claims Registers.\n\nNext: Read the Product Update.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.new_country_or_number_availability',
            'canonical_id': 'ZLOC-EM-MKTG-002',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local availability update for {market_name}',
            'body_template': 'Availability update\n\nHello {user_display_name}, Zoiko Local availability changed for {market_name}. New or expanded capability: {market_capability_summary}. Availability remains subject to inventory, eligibility, documentation, carrier, and local rules.\n\nNext: Check Availability.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.feature_education',
            'canonical_id': 'ZLOC-EM-MKTG-003',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Get more from Zoiko Local {product_feature_name}',
            'body_template': 'Use {product_feature_name} with confidence\n\nHello {user_display_name}, learn how {product_feature_name} can support {campaign_use_case_summary}. The guide includes prerequisites, security controls, limits, and implementation steps.\n\nNext: View the Guide.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.upgrade_recommendation',
            'canonical_id': 'ZLOC-EM-MKTG-004',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local plan option for {organization_name}',
            'body_template': 'Review a plan option\n\nHello {user_display_name}, based on authorized account and usage information, {subscription_recommended_plan} may better support {campaign_reason_summary}. This is a commercial recommendation, not an automatic plan change.\n\nNext: Compare Plans.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.win_back_campaign',
            'canonical_id': 'ZLOC-EM-MKTG-005',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Return to Zoiko Local',
            'body_template': 'Your local presence can be ready again\n\nHello {user_display_name}, Zoiko Local now offers {campaign_summary}. Returning does not guarantee recovery of any previously released number, configuration, recording, message, or plan.\n\nNext: Explore Zoiko Local.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.webinar_or_event_invitation',
            'canonical_id': 'ZLOC-EM-MKTG-006',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Invitation: {event_name}',
            'body_template': 'You are invited\n\nHello {user_display_name}, {event_name} takes place on {event_start_local} {event_timezone}. Topic: {event_summary}. Registration is optional and subject to the event privacy notice.\n\nNext: Review Event.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.partner_offer',
            'canonical_id': 'ZLOC-EM-MKTG-007',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local partner offer',
            'body_template': 'Partner offer\n\nHello {user_display_name}, {partner_name} is offering {campaign_offer_summary} to eligible Zoiko Local customers. Sponsor, material terms, data sharing, eligibility, and expiry are displayed before acceptance.\n\nNext: Review Offer.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'mktg.marketing_consent_confirmation',
            'canonical_id': 'ZLOC-EM-MKTG-008',
            'domain': 'MKTG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local marketing preferences changed',
            'body_template': 'Marketing preferences updated\n\nHello {user_display_name}, your marketing preferences changed on {event_occurred_at}. Enabled: {preferences_enabled_summary}. Disabled: {preferences_disabled_summary}. Essential account, security, service, transaction, privacy, and legal communications are unaffected.\n\nNext: Review Marketing Preferences.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_application_received',
            'canonical_id': 'ZLOC-EM-PART-001',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'We received your Zoiko Local partner application',
            'body_template': 'Partner application received\n\nHello {user_display_name}, we received the {partner_type} application for {organization_name}. Reference: {case_reference}. Review may cover company authority, technical capability, sanctions, fraud, privacy, security, support, and commercial terms.\n\nNext: View Application Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_application_decision',
            'canonical_id': 'ZLOC-EM-PART-002',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Decision on your Zoiko Local partner application',
            'body_template': 'Partner application updated\n\nHello {user_display_name}, application {case_reference} is {case_status}. Next step or reason: {case_action_summary}. No commercial right exists until the approved agreement and onboarding gates are complete.\n\nNext: Review Partner Decision.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_workspace_activated',
            'canonical_id': 'ZLOC-EM-PART-003',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local partner workspace is ready',
            'body_template': 'Partner access activated\n\nHello {user_display_name}, partner access for {organization_name} is active. Complete users, roles, credit controls, products, territories, support contacts, branding, API credentials, and order permissions.\n\nNext: Open Partner Workspace.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.customer_or_order_submitted_by_partner',
            'canonical_id': 'ZLOC-EM-PART-004',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local partner order {order_reference} was received',
            'body_template': 'Partner order received\n\nHello {user_display_name}, order {order_reference} for {customer_name_safe} was received. Scope: {order_summary}. The order remains subject to customer authority, eligibility, inventory, credit, number, and compliance checks.\n\nNext: Track Partner Order.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_commission_statement',
            'canonical_id': 'ZLOC-EM-PART-005',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local partner statement is ready',
            'body_template': 'Partner statement available\n\nHello {user_display_name}, statement {transaction_reference} for {billing_period} is ready. Eligible amount: {transaction_total} {transaction_currency}. Adjustments: {transaction_adjustment_summary}. Payment status: {transaction_status}.\n\nNext: View Partner Statement.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_credit_limit_alert',
            'canonical_id': 'ZLOC-EM-PART-006',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local partner credit alert',
            'body_template': 'Partner credit threshold reached\n\nHello {user_display_name}, {organization_name} has used {usage_threshold_percentage}% of its approved credit limit. Current exposure: {billing_balance} {billing_currency}. New orders or usage may be restricted according to the agreement.\n\nNext: Review Credit Position.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_credential_or_certification_expiring',
            'canonical_id': 'ZLOC-EM-PART-007',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local partner credentials expire soon',
            'body_template': 'Partner action required\n\nHello {user_display_name}, {partner_requirement_name} expires on {case_review_deadline}. Affected rights: {partner_affected_scope}. Upload replacement evidence through the secure Partner Center.\n\nNext: Renew Partner Requirement.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'part.partner_access_restricted_or_terminated',
            'canonical_id': 'ZLOC-EM-PART-008',
            'domain': 'PART',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local partner access changed',
            'body_template': 'Partner access restricted\n\nHello {user_display_name}, partner access for {organization_name} is {restriction_status} effective {restriction_start_at}. Reason category: {decision_reason_category}. Customer continuity, credentials, data, billing, numbers, and support handover follow the approved transition plan.\n\nNext: Review Partner Status.',
        },
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_templates WHERE domain IN ('DEVICE', 'SUP', 'MKTG', 'PART')"))
