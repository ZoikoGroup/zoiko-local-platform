from datetime import datetime, timedelta, timezone

from app.billing import service
from app.billing.models import Subscription, SubscriptionStatus
from app.numbering.identity.models import Account, AccountType


def _signup_and_login(client, email: str, account_type: str = "individual") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Billing Test Co", "account_type": account_type, "email": email, "password": "supersecret123"},
    )
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    client.post(
        "/compliance/consent",
        json={"consent_type": "emergency_calling_acknowledged"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _reserve_and_purchase(client, headers, e164: str, country: str = "US"):
    client.post("/numbers/reserve", json={"e164": e164, "country": country}, headers=headers)
    return client.post("/numbers/purchase", json={"e164": e164}, headers=headers)


def _stub_buy_number(monkeypatch):
    # Keyed by e164 (not a constant sid) - phone_numbers.provider_sid is
    # unique, and several tests in this file purchase more than one number
    # per test.
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": f"PN_fake_{e164}", "phone_number": e164, "capabilities": {}},
    )


# --- Service layer ---


def test_get_or_create_subscription_defaults_to_free_trial(db_session):
    account = Account(name="Sub Default Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    sub = service.get_or_create_subscription(db_session, account.id)
    assert sub.plan_code == "free_trial"
    assert sub.status == SubscriptionStatus.TRIALING
    assert sub.trial_ends_at is not None
    # Synced to the mock ZoikoNex adapter on creation - see test_zoikonex_mock.py.
    assert sub.zoikonex_ref is not None

    # Idempotent - a second call returns the same row.
    again = service.get_or_create_subscription(db_session, account.id)
    assert again.id == sub.id


def test_subscription_rolls_over_an_expired_period(db_session):
    account = Account(name="Sub Rollover Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    now = datetime.now(timezone.utc)
    sub = Subscription(
        account_id=account.id, plan_code="free_trial", status=SubscriptionStatus.TRIALING,
        trial_ends_at=now - timedelta(days=1),
        current_period_start=now - timedelta(days=40), current_period_end=now - timedelta(days=10),
    )
    db_session.add(sub)
    db_session.commit()

    refreshed = service.get_or_create_subscription(db_session, account.id)
    assert refreshed.current_period_end > now
    # Trial already lapsed with no payment processor to charge - Phase 1
    # behavior is to keep the account active, not lock it out.
    assert refreshed.status == SubscriptionStatus.ACTIVE


def test_change_plan_ends_trial_and_updates_plan(db_session):
    account = Account(name="Change Plan Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    sub = service.change_plan(db_session, account.id, "business", actor="test-actor")
    assert sub.plan_code == "business"
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.trial_ends_at is None


def test_change_plan_rejects_an_unknown_plan_code(db_session):
    account = Account(name="Bad Plan Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    try:
        service.change_plan(db_session, account.id, "not_a_real_plan", actor="test-actor")
        assert False, "expected PlanNotFoundError"
    except service.PlanNotFoundError:
        pass


def test_usage_summary_reflects_recorded_usage(db_session):
    from app.usage.service import record_usage_event

    account = Account(name="Usage Summary Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    service.get_or_create_subscription(db_session, account.id)  # ensure a period exists

    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=120, unit="seconds",
        country_band="US", idempotency_key="test-call-1",
    )
    record_usage_event(
        db_session, account_id=account.id, event_type="video_participant_minutes", quantity=15, unit="minutes",
        country_band=None, idempotency_key="test-video-1",
    )
    record_usage_event(
        db_session, account_id=account.id, event_type="ai_summary", quantity=1, unit="summaries",
        country_band=None, idempotency_key="test-ai-1",
    )

    summary = service.get_usage_summary(db_session, account.id)
    resources = {r["resource"]: r for r in summary["resources"]}
    assert resources["voice_minutes"]["used"] == 2.0  # 120 seconds
    assert resources["voice_minutes"]["limit"] == 500  # free_trial plan
    assert resources["video_minutes"]["used"] == 15.0
    assert resources["ai_summaries"]["used"] == 1


def test_seat_quota_allows_up_to_the_limit_then_blocks(db_session):
    from app.numbering.identity.service import add_team_member

    account = Account(name="Seat Quota Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    service.change_plan(db_session, account.id, "free_trial", actor="test-actor")  # max_team_seats=5

    # Owner isn't created via this helper in this test, so seed a User row
    # directly to represent them (matches how other service-level tests in
    # this suite construct an account+owner without going through signup).
    from app.core.security import hash_password
    from app.numbering.identity.models import User, UserRole

    owner = User(account_id=account.id, email="seatowner@example.com", hashed_password=hash_password("x"), role=UserRole.OWNER)
    db_session.add(owner)
    db_session.commit()

    # 1 owner + 4 new members = 5, at the free_trial limit - should succeed.
    for i in range(4):
        add_team_member(
            db_session, account_id=account.id, email=f"seatmember{i}@example.com",
            password="supersecret123", role="member", actor=owner.id,
        )

    # A 6th seat exceeds the free_trial plan's max_team_seats=5.
    try:
        add_team_member(db_session, account_id=account.id, email="seatmemberover@example.com", password="supersecret123", role="member", actor=owner.id)
        assert False, "expected SeatQuotaExceededError"
    except service.SeatQuotaExceededError:
        pass


# --- Routes ---


def test_get_plans_requires_auth(client):
    response = client.get("/billing/plans")
    assert response.status_code == 401


def test_get_plans_returns_all_six_seeded_plans(client):
    """Pro and Scale added per the Zoiko Local Global Plans, Pricing &
    Commercial Launch Standard (2026-08-14) - a real 5-tier public
    architecture (Starter/Business/Pro/Scale/Enterprise) plus the
    pre-existing free_trial plan."""
    token = _signup_and_login(client, "billingplans1@example.com")
    response = client.get("/billing/plans", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    codes = {p["plan_code"] for p in response.json()}
    assert codes == {"free_trial", "starter", "business", "pro", "scale", "enterprise"}


def test_pro_and_scale_get_included_ai_receptionist_minutes(client):
    """Global Plans, Pricing & Commercial Launch Standard doc §5.3 - "Pro
    included allowance 50 minutes/account/month" / "Scale included
    allowance 150 minutes/account/month" - not multiplied by seat count,
    which this table already guarantees (one row per plan tier)."""
    token = _signup_and_login(client, "billingplans2@example.com")
    response = client.get("/billing/plans", headers={"Authorization": f"Bearer {token}"})
    minutes = {p["plan_code"]: p["included_ai_receptionist_minutes"] for p in response.json()}
    assert minutes["pro"] == 50
    assert minutes["scale"] == 150
    assert minutes["starter"] == 0
    assert minutes["business"] == 0


def test_plan_cache_round_trip_preserves_included_ai_receptionist_minutes(client, db_session):
    """Regression test: _serialize_plan/_deserialize_plan (the Redis cache
    layer behind list_plans) previously omitted included_ai_receptionist_
    minutes entirely - a cache MISS returned the right number (straight
    from the DB), but a cache HIT would silently drop it. Forces a
    serialize+deserialize round trip directly rather than depending on
    Redis actually being reachable in this test environment."""
    plans = service.list_plans(db_session)
    pro = next(p for p in plans if p.plan_code == "pro")
    round_tripped = service._deserialize_plan(service._serialize_plan(pro))
    assert round_tripped.included_ai_receptionist_minutes == 50


def test_get_active_price_catalog_entry_returns_the_real_annual_prices(db_session):
    """Global Plans, Pricing & Commercial Launch doc: "Annual billing is
    paid upfront... approximately 17% savings" - $129/$199/$299/$449,
    seeded by migration 4ec152435b05 alongside the existing monthly rows."""
    from app.billing.models import BillingPeriod

    expected = {"starter": 12900, "business": 19900, "pro": 29900, "scale": 44900}
    for plan_code, amount_cents in expected.items():
        entry = service.get_active_price_catalog_entry(db_session, plan_code, billing_period=BillingPeriod.ANNUAL)
        assert entry is not None, plan_code
        assert entry.amount_minor_units == amount_cents
        assert entry.is_placeholder is False


def test_change_plan_records_the_chosen_billing_period(client, db_session):
    from app.billing.models import BillingPeriod

    token = _signup_and_login(client, "annualplan1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "annual"}, headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["billing_period"] == "annual"

    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    sub = service.get_or_create_subscription(db_session, account_id)
    assert sub.billing_period == BillingPeriod.ANNUAL


def test_ai_receptionist_addon_grants_included_minutes_on_a_zero_allowance_plan(client, db_session):
    """Doc §5.3: "$29/workspace/month add-on; 100 AI-handled minutes
    included" - the only route Starter/Business have to any included AI
    Receptionist minutes at all (Pro/Scale get a baked-in plan allowance
    instead - see test_pro_and_scale_get_included_ai_receptionist_minutes).
    Included-minutes total is read via get_usage_summary's resource limit,
    same place the frontend reads it from - there's no standalone getter."""
    def _included_minutes():
        return next(
            r["limit"] for r in service.get_usage_summary(db_session, account_id)["resources"]
            if r["resource"] == "ai_receptionist_minutes"
        )

    token = _signup_and_login(client, "aiaddon1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    service.change_plan(db_session, account_id, "starter", actor="test-actor")

    assert _included_minutes() == 0

    response = client.put(
        "/billing/subscription/ai-receptionist-addon", json={"enabled": True}, headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["ai_receptionist_addon_enabled"] is True
    assert _included_minutes() == 100

    disabled = client.put(
        "/billing/subscription/ai-receptionist-addon", json={"enabled": False}, headers=headers,
    )
    assert disabled.json()["ai_receptionist_addon_enabled"] is False
    assert _included_minutes() == 0


def test_get_subscription_returns_default_free_trial(client):
    token = _signup_and_login(client, "billingsub1@example.com")
    response = client.get("/billing/subscription", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["plan_code"] == "free_trial"
    assert body["status"] == "trialing"
    # Synced to the mock ZoikoNex adapter on creation - see test_zoikonex_mock.py.
    assert body["zoikonex_ref"] is not None


def test_change_plan_route_requires_admin(client):
    owner_token = _signup_and_login(client, "billingplanowner@example.com", account_type="business")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "billingplanmember@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "billingplanmember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.put(
        "/billing/subscription/plan", json={"plan_code": "starter"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_change_plan_route_succeeds_for_owner(client):
    token = _signup_and_login(client, "billingplanowner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put("/billing/subscription/plan", json={"plan_code": "starter"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["plan_code"] == "starter"


def test_price_catalog_route_is_customer_facing_not_staff_only(client):
    """A real customer must be able to see what a plan costs before
    choosing it - confirmed missing (403'd for a real customer login) by
    a UI-gap sweep on 2026-08-14; this route used to require staff auth."""
    token = _signup_and_login(client, "pricecatalogcustomer@example.com")
    response = client.get("/billing/price-catalog/starter", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text


def test_price_catalog_route_requires_some_authentication(client):
    response = client.get("/billing/price-catalog/starter")
    assert response.status_code == 401


def test_change_plan_route_rejects_unknown_plan(client):
    token = _signup_and_login(client, "billingplanunknown@example.com")
    response = client.put(
        "/billing/subscription/plan", json={"plan_code": "not_real"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


def test_usage_summary_route_requires_auth(client):
    response = client.get("/billing/usage-summary")
    assert response.status_code == 401


# --- Cancellation ---


def test_cancel_subscription_sets_canceled_status_and_timestamp(db_session):
    account = Account(name="Cancel Sub Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    service.change_plan(db_session, account.id, "business", actor="test-actor")

    sub = service.cancel_subscription(db_session, account.id, actor="test-actor", reason="too expensive")
    assert sub.status == SubscriptionStatus.CANCELED
    assert sub.canceled_at is not None


def test_cancel_subscription_rejects_a_second_cancellation(db_session):
    account = Account(name="Double Cancel Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    service.cancel_subscription(db_session, account.id, actor="test-actor")

    try:
        service.cancel_subscription(db_session, account.id, actor="test-actor")
        assert False, "expected SubscriptionAlreadyCanceledError"
    except service.SubscriptionAlreadyCanceledError:
        pass


def test_assert_billing_not_suspended_blocks_a_canceled_subscription_immediately(db_session):
    """No grace period, unlike PAST_DUE - cancellation is voluntary, so
    there's nothing to wait out."""
    account = Account(name="Canceled Blocks Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    service.cancel_subscription(db_session, account.id, actor="test-actor")

    try:
        service.assert_billing_not_suspended(db_session, account.id)
        assert False, "expected BillingSuspendedError"
    except service.BillingSuspendedError:
        pass


def test_run_billing_cycle_skips_a_canceled_subscription_instead_of_billing_it(db_session, monkeypatch):
    from app.numbering.identity.models import AccountBillingClassification, AccountBillingSource

    account = Account(
        name="No Bill After Cancel Co", account_type=AccountType.BUSINESS,
        billing_classification=AccountBillingClassification.COMMERCIAL_STANDALONE,
        billing_source=AccountBillingSource.DIRECT_ZOIKO_LOCAL,
    )
    db_session.add(account)
    db_session.flush()
    service.change_plan(db_session, account.id, "business", actor="test-actor")
    service.cancel_subscription(db_session, account.id, actor="test-actor")

    result = service.run_billing_cycle(db_session, account.id, actor="test-actor")
    assert result == {"billed": False, "reason": "subscription is canceled"}


def test_cancel_subscription_route_requires_admin(client):
    owner_token = _signup_and_login(client, "cancelsubowner@example.com", account_type="business")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "cancelsubmember@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "cancelsubmember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/billing/subscription/cancel", json={}, headers={"Authorization": f"Bearer {member_token}"}
    )
    assert response.status_code == 403


def test_cancel_subscription_route_succeeds_for_owner(client):
    token = _signup_and_login(client, "cancelsubowner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/billing/subscription/cancel", json={"reason": "no longer needed"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "canceled"
    assert response.json()["canceled_at"] is not None


def test_cancel_subscription_route_rejects_a_second_cancellation(client):
    token = _signup_and_login(client, "cancelsubowner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/billing/subscription/cancel", json={}, headers=headers)

    response = client.post("/billing/subscription/cancel", json={}, headers=headers)
    assert response.status_code == 409


def test_canceled_account_is_blocked_from_outbound_calling(client, monkeypatch):
    from app.integrations.telecom import twilio as telecom

    _stub_buy_number(monkeypatch)
    monkeypatch.setattr(telecom, "place_call", lambda **kwargs: {"sid": "CA_fake", "status": "queued"})

    token = _signup_and_login(client, "canceledoutbound@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    purchase = _reserve_and_purchase(client, headers, "+15550019001")
    assert purchase.status_code == 200, purchase.text

    client.post("/billing/subscription/cancel", json={}, headers=headers)

    response = client.post(
        "/media/voice/outbound",
        json={"to": "+15550001234", "from_number": "+15550019001", "message": "test"},
        headers=headers,
    )
    assert response.status_code == 402


def test_usage_summary_route_returns_zeroed_resources_for_a_fresh_account(client):
    token = _signup_and_login(client, "billingusagefresh@example.com")
    response = client.get("/billing/usage-summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["plan_code"] == "free_trial"
    resources = {r["resource"]: r for r in body["resources"]}
    assert resources["voice_minutes"]["used"] == 0
    assert resources["voice_minutes"]["limit"] == 500


def test_number_purchase_blocked_once_plan_number_quota_is_reached(client, monkeypatch):
    """free_trial's max_numbers=1 (Global Plans, Pricing & Commercial
    Launch Standard doc §7: "Maximum one trial number") - the 2nd purchase
    attempt must be rejected with 402, without ever calling the real
    Twilio buy_number."""
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "quotanumbers1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = _reserve_and_purchase(client, headers, "+15550011111")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"

    over_limit = _reserve_and_purchase(client, headers, "+15550022222")
    assert over_limit.status_code == 402, over_limit.text
    assert "plan allows up to" in over_limit.json()["detail"]


def test_number_purchase_succeeds_after_upgrading_plan(client, monkeypatch):
    """Exhausts the free_trial number quota, confirms the next purchase is
    blocked, upgrades to enterprise (comfortably higher on every plan's
    limits), then confirms the same purchase now succeeds."""
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "quotanumbers2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = _reserve_and_purchase(client, headers, "+15550033331")
    assert response.status_code == 200, response.text

    blocked = _reserve_and_purchase(client, headers, "+15550044444")
    assert blocked.status_code == 402, blocked.text

    upgrade = client.put("/billing/subscription/plan", json={"plan_code": "enterprise"}, headers=headers)
    assert upgrade.status_code == 200

    now_ok = _reserve_and_purchase(client, headers, "+15550044444")
    assert now_ok.status_code == 200, now_ok.text


def test_team_member_add_blocked_once_seat_quota_is_reached(client):
    """free_trial's max_team_seats=5. Owner counts as seat 1, so 4 members
    can be added before the route returns 402 on the 5th."""
    owner_token = _signup_and_login(client, "quotaseats1@example.com", account_type="business")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    for i in range(4):
        response = client.post(
            "/team/members",
            json={"email": f"quotaseatsmember{i}@example.com", "password": "supersecret123", "role": "member"},
            headers=owner_headers,
        )
        assert response.status_code == 201, response.text

    over_limit = client.post(
        "/team/members",
        json={"email": "quotaseatsmemberover@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    assert over_limit.status_code == 402, over_limit.text
    assert "plan allows up to" in over_limit.json()["detail"]
