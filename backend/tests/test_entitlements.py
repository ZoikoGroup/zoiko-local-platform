"""ZL-COM-ENT-001 "Commercial Entitlement Governance" standard, Phase 1:
explicit entitlement keys gating real features by plan. Before this, these
keys' features were completely ungated by plan - any account on any plan
could create API keys, publish call flows, or pull analytics for free. See
app.billing.service.has_entitlement and app.core.deps.require_entitlement/
require_entitlement_for_api_key. AI Receptionist's own base-plan-or-addon
gate is separate - see billing.service.is_ai_receptionist_enabled_for_account,
covered in test_voice.py.
"""

from app.billing import service
from app.billing.models import SubscriptionStatus
from app.numbering.identity.models import Account, AccountType


def _make_account(db_session, name: str) -> Account:
    account = Account(name=name, account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    return account


def _synced_paid_subscription(db_session, name: str, plan_code: str = "starter"):
    account = _make_account(db_session, name)
    service.get_or_create_subscription(db_session, account.id)  # free_trial, synced via conftest's autouse stub
    sub = service.change_plan(db_session, account.id, plan_code, actor="test-actor")
    return account, sub


def test_has_entitlement_denies_by_default_with_no_row(db_session):
    """ZL-COM-ENT-001's core principle: 'No entitlement record means no
    runtime access.' A free_trial account has zero seeded PlanEntitlement
    rows - every key must deny, not error or implicitly grant."""
    account = _make_account(db_session, "Entitlement Free Trial Co")
    service.get_or_create_subscription(db_session, account.id)

    assert service.has_entitlement(db_session, account.id, "developer.api") is False
    assert service.has_entitlement(db_session, account.id, "routing.advanced") is False


def test_has_entitlement_true_for_a_plan_that_grants_the_key(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Entitlement Pro Co", "pro")
    assert service.has_entitlement(db_session, account.id, "developer.api") is True
    assert service.has_entitlement(db_session, account.id, "routing.advanced") is True
    assert service.has_entitlement(db_session, account.id, "reporting.advanced") is True


def test_has_entitlement_false_for_a_plan_that_does_not_grant_the_key(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Entitlement Starter Co", "starter")
    assert service.has_entitlement(db_session, account.id, "developer.api") is False
    assert service.has_entitlement(db_session, account.id, "routing.advanced") is False


def test_has_entitlement_denies_for_a_canceled_subscription_regardless_of_plan_code(db_session):
    """A CANCELED subscription must never have any entitlement, even
    though its plan_code row still says 'pro' and would otherwise grant
    every key checked here."""
    account, sub = _synced_paid_subscription(db_session, "Entitlement Canceled Co", "pro")
    sub.status = SubscriptionStatus.CANCELED
    db_session.commit()

    assert service.has_entitlement(db_session, account.id, "developer.api") is False


def test_assert_entitlement_raises_with_key_and_plan_code(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Assert Entitlement Co", "starter")
    try:
        service.assert_entitlement(db_session, account.id, "developer.api")
        assert False, "expected EntitlementRequiredError"
    except service.EntitlementRequiredError as e:
        assert e.key == "developer.api"
        assert e.plan_code == "starter"
