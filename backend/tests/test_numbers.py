import logging

from app.compliance.models import ComplianceRule


def _signup_and_login(client, email: str, account_type: str = "individual") -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Numbers Test Co",
            "account_type": account_type,
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    token = response.json()["access_token"]
    # Baseline for every test account here - the emergency-calling
    # disclosure gate is tested explicitly in its own tests below.
    client.post(
        "/compliance/consent",
        json={"consent_type": "emergency_calling_acknowledged"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _reserve(client, headers, e164: str, country: str = "US"):
    response = client.post("/numbers/reserve", json={"e164": e164, "country": country}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _stub_buy_number(monkeypatch):
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164: {"sid": "PN_fake_sid", "phone_number": e164, "capabilities": {}},
    )


def test_purchase_succeeds_when_no_compliance_rule_is_active_for_the_country(client, monkeypatch):
    """No active kyc_individual/kyc_business rule for 'US' in this test's
    isolated transaction, so the compliance gate must be a no-op — purchasing
    a number in a country with no rules configured must not be blocked."""
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "buyer1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550001111")

    response = client.post("/numbers/purchase", json={"e164": "+15550001111"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_purchase_is_blocked_without_an_approved_compliance_case(client, db_session, monkeypatch):
    """Once a country has an active KYC rule, purchase must be refused until
    the account has an approved compliance case for it — the docs' "Compliance
    Pending" lifecycle state, enforced at the point of purchase."""
    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "buyer2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+442079460001", country="GB")

    response = client.post("/numbers/purchase", json={"e164": "+442079460001"}, headers=headers)
    assert response.status_code == 403
    assert "compliance case" in response.json()["detail"]


def test_purchase_succeeds_once_compliance_case_is_approved(client, db_session, monkeypatch):
    from app.staff import service as staff_service

    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "buyer3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+442079460002", country="GB")

    case_response = client.post(
        "/compliance/cases", json={"jurisdiction": "GB", "requirement_type": "kyc_individual"}, headers=headers
    )
    case_id = case_response.json()["id"]

    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="staffbuyer3@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffbuyer3@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    approve = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert approve.status_code == 200

    response = client.post("/numbers/purchase", json={"e164": "+442079460002"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_suspending_a_number_notifies_the_account_owner(client, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "notifysuspend@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    # AU (not one of seed.py's seeded countries) - the shared dev DB has a
    # standing kyc_individual rule for US/GB/CA/NG/ZA/GH/KE/MX, which would
    # otherwise block purchase with 403 regardless of this test's own txn.
    _reserve(client, headers, "+15550006666", country="AU")
    client.post("/numbers/purchase", json={"e164": "+15550006666"}, headers=headers)

    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        response = client.post("/numbers/+15550006666/suspend", headers=headers)
    assert response.status_code == 200

    assert any(
        "notifysuspend@example.com" in record.message and "+15550006666" in record.message
        for record in caplog.records
    )


def test_suspending_a_number_with_a_reason_includes_it_in_the_notification(client, monkeypatch, caplog):
    """Architecture doc §10 Business controls: 'manual override reasons' -
    an Admin suspending a number must be able to record why."""
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "notifysuspendreason@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550006677", country="AU")
    client.post("/numbers/purchase", json={"e164": "+15550006677"}, headers=headers)

    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        response = client.post(
            "/numbers/+15550006677/suspend", json={"reason": "Suspicious call volume"}, headers=headers
        )
    assert response.status_code == 200

    assert any("Suspicious call volume" in record.message for record in caplog.records)


def _add_member(client, admin_headers, email: str) -> str:
    response = client.post(
        "/team/members",
        json={"email": email, "password": "supersecret123", "role": "member"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_member_only_sees_numbers_assigned_to_them(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner1@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550002222")
    client.post("/numbers/purchase", json={"e164": "+15550002222"}, headers=owner_headers)

    member_id = _add_member(client, owner_headers, "assignmember1@example.com")
    member_token = client.post(
        "/auth/login", json={"email": "assignmember1@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    # unassigned - member sees nothing yet
    assert client.get("/numbers", headers=member_headers).json() == []

    assign = client.put(
        "/numbers/+15550002222/assign", json={"user_id": member_id}, headers=owner_headers
    )
    assert assign.status_code == 200, assign.text
    assert assign.json()["assigned_user_id"] == member_id

    listed = client.get("/numbers", headers=member_headers).json()
    assert [n["e164"] for n in listed] == ["+15550002222"]

    # owner still sees it regardless of assignment
    owner_listed = client.get("/numbers", headers=owner_headers).json()
    assert [n["e164"] for n in owner_listed] == ["+15550002222"]


def test_member_cannot_manage_a_number_not_assigned_to_them(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner2@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550003333")
    client.post("/numbers/purchase", json={"e164": "+15550003333"}, headers=owner_headers)

    _add_member(client, owner_headers, "assignmember2@example.com")
    member_token = client.post(
        "/auth/login", json={"email": "assignmember2@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    response = client.post("/numbers/+15550003333/suspend", headers=member_headers)
    assert response.status_code == 409
    assert "not assigned to you" in response.json()["detail"]


def test_member_can_manage_a_number_once_assigned_to_them(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner3@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550004444")
    client.post("/numbers/purchase", json={"e164": "+15550004444"}, headers=owner_headers)

    member_id = _add_member(client, owner_headers, "assignmember3@example.com")
    client.put("/numbers/+15550004444/assign", json={"user_id": member_id}, headers=owner_headers)

    member_token = client.post(
        "/auth/login", json={"email": "assignmember3@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    response = client.post("/numbers/+15550004444/suspend", headers=member_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"


def test_member_cannot_assign_numbers(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner4@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550005555")
    client.post("/numbers/purchase", json={"e164": "+15550005555"}, headers=owner_headers)

    member_id = _add_member(client, owner_headers, "assignmember4@example.com")
    member_token = client.post(
        "/auth/login", json={"email": "assignmember4@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.put(
        "/numbers/+15550005555/assign",
        json={"user_id": member_id},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_business_account_is_gated_on_kyc_business_not_kyc_individual(client, db_session, monkeypatch):
    """A business account purchasing in a country that only has a
    kyc_individual rule must not be gated by it — the requirement type has
    to match the account's own type."""
    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="FR", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "buyer4@example.com", account_type="business")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+33140000001", country="FR")

    response = client.post("/numbers/purchase", json={"e164": "+33140000001"}, headers=headers)
    assert response.status_code == 200, response.text


def test_sync_webhook_requires_public_base_url_configured(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.numbering.numbers.service.settings.public_base_url", "")
    token = _signup_and_login(client, "syncwebhook1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550007777")
    client.post("/numbers/purchase", json={"e164": "+15550007777"}, headers=headers)

    response = client.post("/numbers/+15550007777/sync-webhook", headers=headers)
    assert response.status_code == 409
    assert "PUBLIC_BASE_URL" in response.json()["detail"]


def test_sync_webhook_pushes_the_current_base_url_to_twilio(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.numbering.numbers.service.settings.public_base_url", "https://example.ngrok-free.dev")
    calls = []
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.set_voice_webhook",
        lambda sid, base_url: calls.append((sid, base_url)),
    )
    token = _signup_and_login(client, "syncwebhook2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550008888")
    client.post("/numbers/purchase", json={"e164": "+15550008888"}, headers=headers)

    response = client.post("/numbers/+15550008888/sync-webhook", headers=headers)
    assert response.status_code == 200, response.text
    assert calls == [("PN_fake_sid", "https://example.ngrok-free.dev")]


def test_sync_webhook_rejects_a_number_that_was_never_purchased(client):
    token = _signup_and_login(client, "syncwebhook3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550009999")

    response = client.post("/numbers/+15550009999/sync-webhook", headers=headers)
    assert response.status_code == 409


def test_cancel_releases_the_number_on_twilio(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    released = []
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.release_number", lambda sid: released.append(sid)
    )
    token = _signup_and_login(client, "cancelrelease1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550001212")
    client.post("/numbers/purchase", json={"e164": "+15550001212"}, headers=headers)

    response = client.post("/numbers/+15550001212/cancel", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert released == ["PN_fake_sid"]


def test_cancel_leaves_number_active_when_twilio_release_fails(client, monkeypatch):
    from app.integrations.telecom.twilio import TelecomError

    _stub_buy_number(monkeypatch)

    def _fail(sid):
        raise TelecomError("boom")

    monkeypatch.setattr("app.numbering.numbers.service.telecom.release_number", _fail)
    token = _signup_and_login(client, "cancelrelease2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550001313")
    client.post("/numbers/purchase", json={"e164": "+15550001313"}, headers=headers)

    response = client.post("/numbers/+15550001313/cancel", headers=headers)
    assert response.status_code == 502

    still_active = next(n for n in client.get("/numbers", headers=headers).json() if n["e164"] == "+15550001313")
    assert still_active["status"] == "active"


def test_purchase_blocked_by_compliance_persists_compliance_pending_status(client, db_session, monkeypatch):
    """The docs' "Compliance Pending" lifecycle state must be a real,
    visible, persisted state - not just a one-off error response - so the
    customer/admin can see this specific number is blocked on KYC/KYB
    rather than it looking like an abandoned reservation."""
    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "compliancepending1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+442079460011", country="GB")

    response = client.post("/numbers/purchase", json={"e164": "+442079460011"}, headers=headers)
    assert response.status_code == 403

    numbers = client.get("/numbers", headers=headers).json()
    number = next(n for n in numbers if n["e164"] == "+442079460011")
    assert number["status"] == "compliance_pending"


def test_purchase_retries_successfully_from_compliance_pending_after_approval(client, db_session, monkeypatch):
    """A number stuck in Compliance Pending must be retryable via the same
    purchase endpoint once the case is approved - the customer's only
    self-service path forward, without having to reserve the number again."""
    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "compliancepending2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+442079460012", country="GB")

    blocked = client.post("/numbers/purchase", json={"e164": "+442079460012"}, headers=headers)
    assert blocked.status_code == 403

    case_response = client.post(
        "/compliance/cases", json={"jurisdiction": "GB", "requirement_type": "kyc_individual"}, headers=headers
    )
    case_id = case_response.json()["id"]

    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="staffcompliancepending2@zoikolocal.com", password="staffpass123",
        role=PlatformStaffRole.SUPER_ADMIN,
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffcompliancepending2@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    client.post(f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"})

    retry = client.post("/numbers/purchase", json={"e164": "+442079460012"}, headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "active"


def test_cancelled_number_is_quarantined_from_reservation(client, monkeypatch):
    """Docs' "Quarantine period before reuse, default 90 days" - a just-
    cancelled number must not be immediately re-reservable, by the same
    account or anyone else."""
    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.numbering.numbers.service.telecom.release_number", lambda sid: None)
    token = _signup_and_login(client, "quarantine1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550002222")
    client.post("/numbers/purchase", json={"e164": "+15550002222"}, headers=headers)
    cancel = client.post("/numbers/+15550002222/cancel", headers=headers)
    assert cancel.status_code == 200, cancel.text

    response = client.post(
        "/numbers/reserve", json={"e164": "+15550002222", "country": "US"}, headers=headers
    )
    assert response.status_code == 409
    assert "quarantine" in response.json()["detail"].lower()


def test_list_numbers_shows_an_expired_reservation_honestly(client, db_session):
    """Found while manually checking a real account's My Numbers page: a
    reservation past its RESERVATION_TTL_MINUTES window stayed labeled
    "reserved" forever - purchase_number already rejected it ("Reservation
    expired"), but the customer had no way to see that from the list
    itself. Read-path-only fix (PhoneNumberResponse's model_validator) -
    the underlying row must stay untouched (status="reserved" in the DB)
    so the existing re-reserve/expiry-on-purchase logic keeps working."""
    from datetime import datetime, timedelta, timezone

    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

    token = _signup_and_login(client, "expiredreservation1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550009999")

    number = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == "+15550009999").first()
    number.reserved_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    response = client.get("/numbers", headers=headers)
    assert response.status_code == 200
    listed = next(n for n in response.json() if n["e164"] == "+15550009999")
    assert listed["status"] == "expired"

    db_session.refresh(number)
    assert number.status == PhoneNumberStatus.RESERVED  # untouched in the DB


def test_cancelled_number_can_be_reserved_after_quarantine_period(client, db_session, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.numbering.numbers.models import PhoneNumber

    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.numbering.numbers.service.telecom.release_number", lambda sid: None)
    token = _signup_and_login(client, "quarantine2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550002233")
    client.post("/numbers/purchase", json={"e164": "+15550002233"}, headers=headers)
    cancel = client.post("/numbers/+15550002233/cancel", headers=headers)
    assert cancel.status_code == 200, cancel.text

    number = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == "+15550002233").first()
    number.cancelled_at = datetime.now(timezone.utc) - timedelta(days=91)
    db_session.commit()

    response = client.post(
        "/numbers/reserve", json={"e164": "+15550002233", "country": "US"}, headers=headers
    )
    assert response.status_code == 201, response.text


def test_purchase_is_blocked_without_the_emergency_calling_disclosure_acknowledged(client, monkeypatch):
    """Roadmap doctrine: "Zoiko Local is not an emergency-service operator" -
    every account must acknowledge that 911/999 calling may not work
    reliably before buying any number, in every country, no exceptions."""
    _stub_buy_number(monkeypatch)

    client.post(
        "/auth/signup",
        json={
            "account_name": "No Disclosure Co", "account_type": "individual",
            "email": "nodisclosure@example.com", "password": "supersecret123",
        },
    )
    token = client.post(
        "/auth/login", json={"email": "nodisclosure@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    _reserve(client, headers, "+15550009911")
    response = client.post("/numbers/purchase", json={"e164": "+15550009911"}, headers=headers)
    assert response.status_code == 403
    assert "emergency" in response.json()["detail"].lower()


def test_purchase_succeeds_once_emergency_calling_disclosure_is_acknowledged(client, monkeypatch):
    _stub_buy_number(monkeypatch)

    client.post(
        "/auth/signup",
        json={
            "account_name": "Disclosure Co", "account_type": "individual",
            "email": "disclosureok@example.com", "password": "supersecret123",
        },
    )
    token = client.post(
        "/auth/login", json={"email": "disclosureok@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    _reserve(client, headers, "+15550009922")
    blocked = client.post("/numbers/purchase", json={"e164": "+15550009922"}, headers=headers)
    assert blocked.status_code == 403

    client.post(
        "/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers
    )
    response = client.post("/numbers/purchase", json={"e164": "+15550009922"}, headers=headers)
    assert response.status_code == 200


# --- Curated country list ---


def test_list_supported_countries_returns_the_curated_list(client):
    token = _signup_and_login(client, "countrieslist1@example.com")
    response = client.get("/numbers/countries", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    codes = {c["code"] for c in response.json()}
    assert "US" in codes
    assert "GB" in codes
    # Not an arbitrary country Twilio may have coverage for but this
    # platform hasn't curated yet.
    assert "ZZ" not in codes


def test_search_rejects_an_uncurated_country(client):
    token = _signup_and_login(client, "countriessearch1@example.com")
    response = client.get(
        "/numbers/search", params={"country": "ZZ"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


def test_search_rejects_a_non_numeric_area_code_with_a_clean_message(client, monkeypatch):
    """Confirmed live: a city name (or any non-numeric text) typed into the
    area code field used to reach Twilio raw and come back as their own
    400 verbatim ("chicao is not a valid integer: 'AreaCode'", vendor field
    name and docs link included). Caught before Twilio is ever called -
    stubbing search_available_numbers to fail the test loudly if it is."""
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Twilio should never be called for an invalid area code")

    monkeypatch.setattr("app.numbering.numbers.service.telecom.search_available_numbers", _fail_if_called)

    token = _signup_and_login(client, "areacodeinvalid1@example.com")
    response = client.get(
        "/numbers/search", params={"country": "US", "area_code": "chicao"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert "chicao" in response.json()["detail"]
    assert "AreaCode" not in response.json()["detail"]  # no raw vendor field name leaking through


def test_reserve_rejects_an_uncurated_country(client):
    token = _signup_and_login(client, "countriesreserve1@example.com")
    response = client.post(
        "/numbers/reserve", json={"e164": "+9990001111", "country": "ZZ"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


# --- Renewal flow ---


def test_purchase_sets_a_next_renewal_date_about_30_days_out(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "renewalpurchase1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550011001")

    response = client.post("/numbers/purchase", json={"e164": "+15550011001"}, headers=headers)
    assert response.status_code == 200
    next_renewal_at = datetime.fromisoformat(response.json()["next_renewal_at"].replace("Z", "+00:00"))
    expected = datetime.now(timezone.utc) + timedelta(days=30)
    assert abs((next_renewal_at - expected).total_seconds()) < 60


def _create_staff_and_login(client, db_session, email: str) -> str:
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN)
    return client.post("/staff/login", json={"email": email, "password": "staffpass123"}).json()["access_token"]


def test_staff_can_list_and_mark_a_due_renewal(client, db_session, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.numbering.numbers.models import PhoneNumber

    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "renewalstaff1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550011002")
    client.post("/numbers/purchase", json={"e164": "+15550011002"}, headers=headers)

    number = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == "+15550011002").first()
    number.next_renewal_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    staff_token = _create_staff_and_login(client, db_session, "staffrenewal1@zoikolocal.com")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    due = client.get("/staff/numbers/due-for-renewal", headers=staff_headers)
    assert due.status_code == 200
    assert any(n["e164"] == "+15550011002" for n in due.json())

    response = client.post(f"/staff/numbers/{number.id}/mark-renewed", headers=staff_headers)
    assert response.status_code == 200
    new_next_renewal_at = datetime.fromisoformat(response.json()["next_renewal_at"].replace("Z", "+00:00"))
    assert new_next_renewal_at > datetime.now(timezone.utc) + timedelta(days=29)

    due_after = client.get("/staff/numbers/due-for-renewal", headers=staff_headers)
    assert not any(n["e164"] == "+15550011002" for n in due_after.json())


def test_staff_cannot_mark_renewed_a_number_not_yet_due(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "renewalstaff2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550011003")
    purchased = client.post("/numbers/purchase", json={"e164": "+15550011003"}, headers=headers).json()

    staff_token = _create_staff_and_login(client, db_session, "staffrenewal2@zoikolocal.com")
    response = client.post(
        f"/staff/numbers/{purchased['id']}/mark-renewed", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 409


# --- Real Stripe Checkout for number purchase (test mode) ---


def _stripe_webhook_body_and_signature(secret: str, event_type: str, session_object: dict) -> tuple[bytes, str]:
    import hashlib
    import hmac
    import json
    import time

    body = json.dumps(
        {"id": "evt_test", "object": "event", "type": event_type, "data": {"object": session_object}}
    ).encode()
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{body.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return body, f"t={timestamp},v1={signature}"


def test_create_checkout_session_requires_auth(client):
    response = client.post("/numbers/+15550013001/checkout-session")
    assert response.status_code == 401


def test_create_checkout_session_fails_when_number_not_reserved_by_account(client):
    token = _signup_and_login(client, "checkoutnoreserve@example.com")
    response = client.post(
        "/numbers/+15550013002/checkout-session", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 409


def test_create_checkout_session_returns_stripe_hosted_url(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_payments_secret_key", "rk_test_fake")
    monkeypatch.setattr(
        "app.numbering.numbers.service.stripe_checkout.create_checkout_session",
        lambda **kwargs: {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay/cs_test_123"},
    )

    token = _signup_and_login(client, "checkoutsuccess@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550013003")

    response = client.post("/numbers/+15550013003/checkout-session", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "cs_test_123"
    assert body["url"] == "https://checkout.stripe.com/c/pay/cs_test_123"


def test_non_commercial_account_cannot_create_a_checkout_session(client, db_session, monkeypatch):
    """Commercial Billing Operating Standard doc COM-03: non-commercial
    billing_classification accounts (DEMO/SANDBOX/etc.) must never create
    a live charge, even if everything else about the request is valid."""
    from app.numbering.identity.models import Account, AccountBillingClassification

    monkeypatch.setattr("app.core.config.settings.stripe_payments_secret_key", "rk_test_fake")
    token = _signup_and_login(client, "checkoutdemo@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _reserve(client, headers, "+15550013005")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    account.billing_classification = AccountBillingClassification.DEMO
    db_session.commit()

    response = client.post("/numbers/+15550013005/checkout-session", headers=headers)
    assert response.status_code == 403


def test_create_checkout_session_returns_502_when_stripe_call_fails(client, monkeypatch):
    from app.integrations.billing.stripe_checkout import PaymentError

    monkeypatch.setattr("app.core.config.settings.stripe_payments_secret_key", "rk_test_fake")

    def _raise(**kwargs):
        raise PaymentError("Stripe create checkout session failed: connection timed out")

    monkeypatch.setattr("app.numbering.numbers.service.stripe_checkout.create_checkout_session", _raise)

    token = _signup_and_login(client, "checkoutstripedown@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550013004")

    response = client.post("/numbers/+15550013004/checkout-session", headers=headers)
    assert response.status_code == 502


def test_stripe_payment_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")
    body, _ = _stripe_webhook_body_and_signature(
        "whsec_test", "checkout.session.completed", {"id": "cs_test_bad_sig", "metadata": {}}
    )
    response = client.post(
        "/numbers/payments/webhook", content=body, headers={"Stripe-Signature": "t=123,v1=not-real"}
    )
    assert response.status_code == 403


def test_stripe_payment_webhook_completes_the_purchase(client, db_session, monkeypatch):
    """End-to-end: reserve -> real (mocked-at-the-SDK-boundary) Stripe
    webhook fires checkout.session.completed -> the number reaches ACTIVE
    via the existing purchase_number flow, with no direct call to
    /numbers/purchase anywhere in this test."""
    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")

    token = _signup_and_login(client, "checkoutwebhook@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _reserve(client, headers, "+15550013005")

    body, signature = _stripe_webhook_body_and_signature(
        "whsec_test", "checkout.session.completed",
        {"id": "cs_test_complete", "metadata": {"e164": "+15550013005", "account_id": account_id}},
    )
    response = client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert response.status_code == 204

    numbers = client.get("/numbers", headers=headers).json()
    purchased = next(n for n in numbers if n["e164"] == "+15550013005")
    assert purchased["status"] == "active"


def test_stripe_payment_webhook_is_idempotent_against_retried_delivery(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")

    token = _signup_and_login(client, "checkoutwebhookretry@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _reserve(client, headers, "+15550013006")

    body, signature = _stripe_webhook_body_and_signature(
        "whsec_test", "checkout.session.completed",
        {"id": "cs_test_retry", "metadata": {"e164": "+15550013006", "account_id": account_id}},
    )
    first = client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert first.status_code == 204
    # Stripe redelivers the same event (at-least-once delivery) - must not
    # error even though the number is no longer purchasable.
    second = client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert second.status_code == 204


def test_stripe_payment_webhook_refunds_a_genuine_fulfillment_failure(client, db_session, monkeypatch):
    """The exact scenario hit live during manual testing: payment succeeds,
    Twilio then can't provision the number (e.g. trial account number
    limit) - the customer paid for nothing, so the payment must be
    refunded automatically."""
    from app.integrations.telecom.twilio import TelecomError

    def _raise_buy(e164):
        raise TelecomError("Trial account has reached the maximum number of phone numbers allowed.")

    monkeypatch.setattr("app.numbering.numbers.service.telecom.buy_number", _raise_buy)
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")

    refund_calls = []
    monkeypatch.setattr(
        "app.numbering.numbers.service.stripe_checkout.refund_payment",
        lambda payment_intent_id: refund_calls.append(payment_intent_id) or {"id": "re_test", "status": "succeeded"},
    )

    token = _signup_and_login(client, "checkoutrefund1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _reserve(client, headers, "+15550013007")

    body, signature = _stripe_webhook_body_and_signature(
        "whsec_test", "checkout.session.completed",
        {
            "id": "cs_test_refund", "payment_intent": "pi_test_refund_me",
            "metadata": {"e164": "+15550013007", "account_id": account_id},
        },
    )
    response = client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert response.status_code == 204
    assert refund_calls == ["pi_test_refund_me"]

    numbers = client.get("/numbers", headers=headers).json()
    number = next(n for n in numbers if n["e164"] == "+15550013007")
    assert number["status"] == "reserved"  # released back, not stranded


def test_stripe_payment_webhook_does_not_refund_a_compliance_pending_outcome(client, db_session, monkeypatch):
    """Compliance-pending is not a failure - the customer still gets the
    number once their case is approved, so the payment must be kept."""
    from app.compliance.models import ComplianceRule

    db_session.add(ComplianceRule(country="US", requirement_type="kyc_individual", is_active=True))
    db_session.commit()

    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")
    refund_calls = []
    monkeypatch.setattr(
        "app.numbering.numbers.service.stripe_checkout.refund_payment",
        lambda payment_intent_id: refund_calls.append(payment_intent_id),
    )

    token = _signup_and_login(client, "checkoutrefund2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _reserve(client, headers, "+15550013008")

    body, signature = _stripe_webhook_body_and_signature(
        "whsec_test", "checkout.session.completed",
        {
            "id": "cs_test_compliance", "payment_intent": "pi_test_compliance",
            "metadata": {"e164": "+15550013008", "account_id": account_id},
        },
    )
    response = client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert response.status_code == 204
    assert refund_calls == []

    numbers = client.get("/numbers", headers=headers).json()
    number = next(n for n in numbers if n["e164"] == "+15550013008")
    assert number["status"] == "compliance_pending"


def test_stripe_payment_webhook_does_not_refund_an_idempotent_replay(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")
    refund_calls = []
    monkeypatch.setattr(
        "app.numbering.numbers.service.stripe_checkout.refund_payment",
        lambda payment_intent_id: refund_calls.append(payment_intent_id),
    )

    token = _signup_and_login(client, "checkoutrefund3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _reserve(client, headers, "+15550013009")

    body, signature = _stripe_webhook_body_and_signature(
        "whsec_test", "checkout.session.completed",
        {
            "id": "cs_test_noreplay", "payment_intent": "pi_test_noreplay",
            "metadata": {"e164": "+15550013009", "account_id": account_id},
        },
    )
    client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert refund_calls == []


def test_stripe_payment_webhook_ignores_unrelated_event_types(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_test")
    body, signature = _stripe_webhook_body_and_signature(
        "whsec_test", "payment_intent.created", {"id": "pi_test_unrelated"}
    )
    response = client.post("/numbers/payments/webhook", content=body, headers={"Stripe-Signature": signature})
    assert response.status_code == 204
