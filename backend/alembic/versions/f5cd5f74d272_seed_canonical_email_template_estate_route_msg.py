"""seed canonical email template estate - ROUTE and MSG domains

Revision ID: f5cd5f74d272
Revises: be10f507545c
Create Date: 2026-08-10 17:30:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5cd5f74d272'
down_revision: Union[str, None] = 'be10f507545c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seeds the Email Communications System spec doc's ROUTE ("Call routing and
# queues", 10 templates) and MSG ("Business messaging", 10 templates)
# domains - not importable when the original 7-domain estate was built
# (this session), since the Advanced IVR builder / call-flow designer
# (app/routing/) and WhatsApp/SMS messaging (app/messaging/) didn't exist
# yet. They do now (Phase 3, commits 1646b83 and da6660b).
#
# 3 of the 20 have a real call site wired up: route.call_flow_published and
# route.call_flow_rollback (app/routing/service.py's publish_flow/
# rollback_flow), and msg.recipient_opted_out (app/messaging/service.py's
# _record_inbound, on a genuine new STOP - re-notifying on a repeat STOP
# from an already-opted-out sender is deliberately suppressed). The other
# 17 are seeded with real doc copy but have no call site yet - registry
# completeness without fabricating triggers, same posture as every other
# domain import this session. Deliberately not wired despite having a
# plausible source: route.call_flow_changed (no clean way to distinguish
# "first publish" from "changed" without a live-version diff, and
# call_flow_published already covers "something changed" either way),
# msg.message_delivery_failed (a per-message Twilio status webhook callback
# with no account-level throttling - firing on every single failed message
# would be notification spam, same reasoning INTG's webhook-delivery-
# failure templates were left unwired for), and the queue/failover/holiday-
# schedule templates (their underlying features - queue SLA thresholds,
# automatic failover detection, holiday scheduling - aren't built yet,
# only the plain queue/business-hours features are).


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
            'key': 'route.call_flow_published',
            'canonical_id': 'ZLOC-EM-ROUTE-001',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local call flow was published',
            'body_template': 'Call Flow Published\n\nHello {user_display_name}, {route_name} version {route_version} was published for {route_number_summary} by {actor_display_name}. Effective: {route_effective_at}. Confirm business hours, queue behavior, voicemail, emergency bypass, and failover.\n\nNext: Review Live Call Flow.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.call_flow_changed',
            'canonical_id': 'ZLOC-EM-ROUTE-002',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A live Zoiko Local call flow changed',
            'body_template': 'Call Flow Changed\n\nHello {user_display_name}, {route_name} changed from version {route_previous_version} to {route_version}. Summary: {route_change_summary}. Run a controlled inbound test after every material change.\n\nNext: Review Routing Change.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.call_flow_rollback',
            'canonical_id': 'ZLOC-EM-ROUTE-003',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local call flow was rolled back',
            'body_template': 'Call Flow Rollback\n\nHello {user_display_name}, {route_name} was rolled back to version {route_version} on {event_occurred_at}. Reason: {route_rollback_reason}.\n\nNext: Review Restored Flow.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.business_hours_changed',
            'canonical_id': 'ZLOC-EM-ROUTE-004',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Business hours changed in Zoiko Local',
            'body_template': 'Business Hours Changed\n\nHello {user_display_name}, {schedule_name} changed effective {schedule_effective_at}. Affected routes: {schedule_route_summary}. Confirm timezone, holidays, closed-state announcements, and voicemail behavior.\n\nNext: Review Business Hours.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.queue_membership_changed',
            'canonical_id': 'ZLOC-EM-ROUTE-005',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Your Zoiko Local queue assignments changed',
            'body_template': 'Queue Membership Changed\n\nHello {user_display_name}, your assignment in {organization_name} changed. Added: {queue_added_summary}. Removed: {queue_removed_summary}. Review ring strategy, availability, wrap-up, and recording rules.\n\nNext: Review Queue Assignments.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.queue_sla_alert',
            'canonical_id': 'ZLOC-EM-ROUTE-006',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local queue exceeded its service threshold',
            'body_template': 'Queue SLA Alert\n\nHello {user_display_name}, {queue_name} exceeded {queue_threshold_name} at {event_occurred_at}. Waiting: {queue_waiting_count}. Longest wait: {queue_longest_wait}. This is an organization alert, not a platform incident.\n\nNext: Open Queue Dashboard.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.failover_activated',
            'canonical_id': 'ZLOC-EM-ROUTE-007',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local call-routing failover activated',
            'body_template': 'Failover Activated\n\nHello {user_display_name}, failover activated for {route_number_summary} at {event_occurred_at}. Current destination: {route_failover_destination}. Reason: {incident_impact_summary}.\n\nNext: Review Failover.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.failover_cleared',
            'canonical_id': 'ZLOC-EM-ROUTE-008',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local routing returned to normal',
            'body_template': 'Failover Cleared\n\nHello {user_display_name}, primary routing resumed for {route_number_summary} at {event_occurred_at}. Confirm expected call handling and review the incident record where available.\n\nNext: Test Primary Routing.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.no_reachable_destination',
            'canonical_id': 'ZLOC-EM-ROUTE-009',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Action required: a Zoiko Local route cannot receive calls',
            'body_template': 'Route Has No Reachable Destination\n\nHello {user_display_name}, {route_name} for {route_number_summary} has no currently reachable destination. Incoming calls may fail or follow an unintended fallback.\n\nNext: Fix Call Route.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'route.holiday_schedule_reminder',
            'canonical_id': 'ZLOC-EM-ROUTE-010',
            'domain': 'ROUTE',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Reminder: a Zoiko Local holiday schedule starts soon',
            'body_template': 'Holiday Schedule Reminder\n\nHello {user_display_name}, {schedule_name} begins on {schedule_start_local} {schedule_timezone} and affects {schedule_route_summary}. Confirm announcements, queues, voicemail, and emergency contacts.\n\nNext: Review Holiday Schedule.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.business_messaging_activated',
            'canonical_id': 'ZLOC-EM-MSG-001',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local business messaging is active',
            'body_template': 'Business Messaging Activated\n\nHello {user_display_name}, business messaging is active for {scope_summary}. Before sending, confirm sender identity, recipient consent, required disclosures, STOP/HELP handling, content restrictions, throughput, and jurisdictional rules.\n\nNext: Review Messaging Setup.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.registration_submitted',
            'canonical_id': 'ZLOC-EM-MSG-002',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'We received your Zoiko Local messaging registration',
            'body_template': 'Messaging Registration Submitted\n\nHello {user_display_name}, registration {case_reference} was submitted for {messaging_scope_summary}. Delivery remains restricted until required approvals are active.\n\nNext: View Registration Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.registration_decision',
            'canonical_id': 'ZLOC-EM-MSG-003',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Decision on your Zoiko Local messaging registration',
            'body_template': 'Messaging Registration Decision\n\nHello {user_display_name}, registration {case_reference} is {case_status}. Reason or next step: {case_action_summary}. Approval applies only to the registered sender, use case, content, and markets.\n\nNext: Review Registration.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.inbound_message_received',
            'canonical_id': 'ZLOC-EM-MSG-004',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'New message in Zoiko Local',
            'body_template': 'Inbound Message Received\n\nHello {user_display_name}, a message was received for {number_masked_or_formatted} from {message_sender_safe} at {message_received_local}. Message content is not included in email.\n\nNext: Open Conversation.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.unread_message_digest',
            'canonical_id': 'ZLOC-EM-MSG-005',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'You have unread Zoiko Local messages',
            'body_template': 'Unread Message Digest\n\nHello {user_display_name}, you have unread activity across {digest_conversation_count} conversations. Sender names and message content are omitted unless your organization has approved previews.\n\nNext: Open Messages.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.message_delivery_failed',
            'canonical_id': 'ZLOC-EM-MSG-006',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A Zoiko Local message was not delivered',
            'body_template': 'Message Delivery Failed\n\nHello {user_display_name}, message {message_reference} to {message_destination_masked} was not delivered. Reason category: {message_failure_category}. Do not repeatedly resend prohibited or opted-out content.\n\nNext: Review Failed Message.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.recipient_opted_out',
            'canonical_id': 'ZLOC-EM-MSG-007',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'A recipient opted out of Zoiko Local messages',
            'body_template': 'Recipient Opted Out\n\nHello {user_display_name}, {message_destination_masked} opted out from {messaging_sender_summary} on {event_occurred_at}. The suppression applies according to the configured sender and use-case scope.\n\nNext: Review Messaging Suppression.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.messaging_limit_approaching',
            'canonical_id': 'ZLOC-EM-MSG-008',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local messaging is approaching a limit',
            'body_template': 'Messaging Limit Approaching\n\nHello {user_display_name}, {scope_summary} reached {usage_threshold_percentage}% of its {usage_limit_name}. Current usage: {usage_current}. Limits may be plan, carrier, number, campaign, or jurisdiction specific.\n\nNext: Review Messaging Usage.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.messaging_suspended',
            'canonical_id': 'ZLOC-EM-MSG-009',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local messaging was suspended',
            'body_template': 'Messaging Suspended\n\nHello {user_display_name}, messaging for {scope_summary} was suspended effective {restriction_start_at}. Reason: {decision_reason_category}. Inbound and outbound treatment may differ.\n\nNext: Review Messaging Status.',
        },
        {
            'id': str(uuid.uuid4()),
            'key': 'msg.messaging_restored',
            'canonical_id': 'ZLOC-EM-MSG-010',
            'domain': 'MSG',
            'spec_version': '1.0.0',
            'category': 'TRANSACTIONAL',
            'priority': 'STANDARD',
            'subject_template': 'Zoiko Local messaging was restored',
            'body_template': 'Messaging Restored\n\nHello {user_display_name}, messaging for {scope_summary} was restored on {event_occurred_at}. Run a controlled test and verify suppression and consent handling before resuming campaigns or workflows.\n\nNext: Review Messaging Setup.',
        },
        ],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM notification_templates WHERE domain IN ('ROUTE', 'MSG')"))
