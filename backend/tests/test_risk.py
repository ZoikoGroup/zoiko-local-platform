from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Risk Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _create_staff_and_login(client, db_session, email: str, role):
    from app.staff import service as staff_service

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _active_number(db_session, account_id: str, e164: str) -> PhoneNumber:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    return number


def test_customer_cannot_manage_blocked_destinations(client):
    token = _signup_and_login(client, "riskcustomer@example.com")
    response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_support_staff_cannot_add_blocked_destination(client, db_session):
    from app.staff.models import PlatformStaffRole

    staff_token = _create_staff_and_login(client, db_session, "risksupport1@zoikolocal.com", PlatformStaffRole.SUPPORT)
    response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403


def test_super_admin_can_add_and_support_staff_can_list(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskadmin1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    create_response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201

    support_token = _create_staff_and_login(
        client, db_session, "risksupport2@zoikolocal.com", PlatformStaffRole.SUPPORT
    )
    list_response = client.get(
        "/risk/blocked-destinations", headers={"Authorization": f"Bearer {support_token}"}
    )
    assert list_response.status_code == 200
    assert any(r["prefix"] == "+1900" for r in list_response.json())


def test_outbound_call_to_blocked_destination_is_rejected(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskadmin2@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    token = _signup_and_login(client, "riskblocked@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550009999")

    response = client.post(
        "/media/voice/outbound",
        json={"to": "+19005551234", "from": "+15550009999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "blocked destination" in response.json()["detail"].lower()


def test_outbound_call_velocity_limit_is_enforced(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAvelocity", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )

    token = _signup_and_login(client, "riskvelocity@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550008888")
    headers = {"Authorization": f"Bearer {token}"}

    from app.risk.service import MAX_OUTBOUND_CALLS_PER_WINDOW

    for _ in range(MAX_OUTBOUND_CALLS_PER_WINDOW):
        response = client.post(
            "/media/voice/outbound",
            json={"to": "+15551230000", "from": "+15550008888"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    over_limit_response = client.post(
        "/media/voice/outbound",
        json={"to": "+15551230000", "from": "+15550008888"},
        headers=headers,
    )
    assert over_limit_response.status_code == 429
