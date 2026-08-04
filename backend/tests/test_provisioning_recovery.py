from datetime import datetime, timedelta, timezone

from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Recovery Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _create_and_login_staff(db_session, client, email: str, role=PlatformStaffRole.SUPPORT) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _seed_stuck_number(
    db_session, account_id: str, *, e164: str, status=PhoneNumberStatus.PROVISIONING, minutes_ago: int = 10
) -> PhoneNumber:
    number = PhoneNumber(
        e164=e164, country="US", status=status, account_id=account_id,
        provisioning_started_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    db_session.add(number)
    db_session.commit()
    return number


def test_stuck_provisioning_requires_staff_auth(client):
    assert client.get("/staff/numbers/stuck-provisioning").status_code == 401


def test_list_stuck_provisioning_shows_a_genuinely_stale_number(client, db_session):
    _, account_id = _signup_and_login(client, "recoveryowner1@example.com")
    _seed_stuck_number(db_session, account_id, e164="+15550021111")

    staff_token = _create_and_login_staff(db_session, client, "recoverystaff1@zoikolocal.com")
    response = client.get(
        "/staff/numbers/stuck-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    entries = response.json()
    assert any(e["e164"] == "+15550021111" for e in entries)
    entry = next(e for e in entries if e["e164"] == "+15550021111")
    assert entry["account_name"] == "Recovery Test Co"
    assert entry["status"] == "provisioning"


def test_list_stuck_provisioning_excludes_a_recent_in_flight_number(client, db_session):
    """A number that entered PROVISIONING moments ago is probably a real
    concurrent request, not a crash - must not show up as "stuck" yet."""
    _, account_id = _signup_and_login(client, "recoveryowner2@example.com")
    _seed_stuck_number(db_session, account_id, e164="+15550022222", minutes_ago=0)

    staff_token = _create_and_login_staff(db_session, client, "recoverystaff2@zoikolocal.com")
    response = client.get(
        "/staff/numbers/stuck-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    assert not any(e["e164"] == "+15550022222" for e in response.json())


def test_retry_provisioning_activates_the_number_on_success(client, db_session, monkeypatch):
    _, account_id = _signup_and_login(client, "recoveryowner3@example.com")
    number = _seed_stuck_number(db_session, account_id, e164="+15550023333")

    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164: {"sid": "PN_recovered_sid", "phone_number": e164, "capabilities": {}},
    )

    staff_token = _create_and_login_staff(db_session, client, "recoverystaff3@zoikolocal.com")
    response = client.post(
        f"/staff/numbers/{number.id}/retry-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_retry_provisioning_reverts_to_reserved_on_provider_failure(client, db_session, monkeypatch):
    _, account_id = _signup_and_login(client, "recoveryowner4@example.com")
    number = _seed_stuck_number(db_session, account_id, e164="+15550024444")

    from app.integrations.telecom.twilio import TelecomError

    def _fail(e164):
        raise TelecomError("provider still down")

    monkeypatch.setattr("app.numbering.numbers.service.telecom.buy_number", _fail)

    staff_token = _create_and_login_staff(db_session, client, "recoverystaff4@zoikolocal.com")
    response = client.post(
        f"/staff/numbers/{number.id}/retry-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 502

    # Number must not be stranded - it's back to a normal, purchasable state.
    follow_up = client.get(
        "/staff/numbers/stuck-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    ).json()
    assert not any(e["e164"] == "+15550024444" for e in follow_up)


def test_retry_provisioning_rejects_a_number_that_is_not_stuck(client, db_session):
    _, account_id = _signup_and_login(client, "recoveryowner5@example.com")
    number = PhoneNumber(
        e164="+15550025555", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
    )
    db_session.add(number)
    db_session.commit()

    staff_token = _create_and_login_staff(db_session, client, "recoverystaff5@zoikolocal.com")
    response = client.post(
        f"/staff/numbers/{number.id}/retry-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 409


def test_retry_provisioning_requires_support_or_super_admin_role(client, db_session):
    _, account_id = _signup_and_login(client, "recoveryowner6@example.com")
    number = _seed_stuck_number(db_session, account_id, e164="+15550026666")

    compliance_token = _create_and_login_staff(
        db_session, client, "recoverycompliance6@zoikolocal.com", role=PlatformStaffRole.COMPLIANCE_OFFICER
    )
    response = client.post(
        f"/staff/numbers/{number.id}/retry-provisioning", headers={"Authorization": f"Bearer {compliance_token}"}
    )
    assert response.status_code == 403


def test_release_stuck_provisioning_reverts_to_reserved_without_calling_the_provider(client, db_session, monkeypatch):
    _, account_id = _signup_and_login(client, "recoveryowner7@example.com")
    number = _seed_stuck_number(db_session, account_id, e164="+15550027777")

    def _unexpected_call(e164):
        raise AssertionError("release must not call the provider at all")

    monkeypatch.setattr("app.numbering.numbers.service.telecom.buy_number", _unexpected_call)

    staff_token = _create_and_login_staff(db_session, client, "recoverystaff7@zoikolocal.com")
    response = client.post(
        f"/staff/numbers/{number.id}/release-provisioning", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "reserved"
