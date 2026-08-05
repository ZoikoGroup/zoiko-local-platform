def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Contacts Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _add_viewer(client, owner_headers, email: str) -> str:
    add_response = client.post(
        "/team/members",
        json={"email": email, "password": "viewersecret123", "role": "viewer"},
        headers=owner_headers,
    )
    assert add_response.status_code == 201, add_response.text
    login_response = client.post("/auth/login", json={"email": email, "password": "viewersecret123"})
    return login_response.json()["access_token"]


def test_list_contacts_requires_auth(client):
    response = client.get("/contacts")
    assert response.status_code == 401


def test_create_and_list_a_contact(client):
    token, _ = _signup_and_login(client, "contactsowner1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post(
        "/contacts",
        json={"name": "Jordan Lee", "phone_number": "+15551234567", "email": "jordan@example.com", "notes": "VIP"},
        headers=headers,
    )
    assert create_response.status_code == 201, create_response.text
    body = create_response.json()
    assert body["name"] == "Jordan Lee"
    assert body["phone_number"] == "+15551234567"

    list_response = client.get("/contacts", headers=headers)
    assert list_response.status_code == 200
    names = [c["name"] for c in list_response.json()]
    assert "Jordan Lee" in names


def test_create_contact_rejects_a_blank_name(client):
    token, _ = _signup_and_login(client, "contactsowner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/contacts", json={"name": "", "phone_number": "+15551234567"}, headers=headers
    )
    assert response.status_code == 422


def test_update_a_contact(client):
    token, _ = _signup_and_login(client, "contactsowner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    contact_id = client.post(
        "/contacts", json={"name": "Old Name", "phone_number": "+15550000001"}, headers=headers
    ).json()["id"]

    update_response = client.put(
        f"/contacts/{contact_id}",
        json={"name": "New Name", "phone_number": "+15550000002", "notes": "updated"},
        headers=headers,
    )
    assert update_response.status_code == 200
    body = update_response.json()
    assert body["name"] == "New Name"
    assert body["phone_number"] == "+15550000002"
    assert body["notes"] == "updated"


def test_update_a_nonexistent_contact_returns_404(client):
    token, _ = _signup_and_login(client, "contactsowner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/contacts/00000000-0000-0000-0000-000000000000",
        json={"name": "Ghost", "phone_number": "+15550000000"},
        headers=headers,
    )
    assert response.status_code == 404


def test_delete_a_contact(client):
    token, _ = _signup_and_login(client, "contactsowner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    contact_id = client.post(
        "/contacts", json={"name": "To Delete", "phone_number": "+15550000003"}, headers=headers
    ).json()["id"]

    delete_response = client.delete(f"/contacts/{contact_id}", headers=headers)
    assert delete_response.status_code == 204

    list_response = client.get("/contacts", headers=headers)
    assert not any(c["id"] == contact_id for c in list_response.json())


def test_contacts_are_scoped_to_the_callers_own_account(client):
    token_a, _ = _signup_and_login(client, "contactsaccountA@example.com")
    token_b, _ = _signup_and_login(client, "contactsaccountB@example.com")

    client.post(
        "/contacts", json={"name": "Account A Contact", "phone_number": "+15550000004"},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    b_list = client.get("/contacts", headers={"Authorization": f"Bearer {token_b}"}).json()
    assert not any(c["name"] == "Account A Contact" for c in b_list)

    # Cross-account update/delete must also be rejected, not just hidden from listing.
    a_contact_id = client.get("/contacts", headers={"Authorization": f"Bearer {token_a}"}).json()[0]["id"]
    cross_update = client.put(
        f"/contacts/{a_contact_id}",
        json={"name": "Hijacked", "phone_number": "+15550000004"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert cross_update.status_code == 404


def test_viewer_can_list_contacts_but_not_create_them(client):
    owner_token, _ = _signup_and_login(client, "contactsviewerowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/contacts", json={"name": "Owner's Contact", "phone_number": "+15550000005"}, headers=owner_headers
    )
    viewer_token = _add_viewer(client, owner_headers, "contactsviewer1@example.com")
    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    list_response = client.get("/contacts", headers=viewer_headers)
    assert list_response.status_code == 200
    assert any(c["name"] == "Owner's Contact" for c in list_response.json())

    create_response = client.post(
        "/contacts", json={"name": "Blocked", "phone_number": "+15550000006"}, headers=viewer_headers
    )
    assert create_response.status_code == 403


def test_contact_history_matches_calls_by_phone_number(client, db_session):
    from app.media.models import CallDirection, CallRecord

    token, account_id = _signup_and_login(client, "contactshistory1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    contact_id = client.post(
        "/contacts", json={"name": "Has History", "phone_number": "+15559998888"}, headers=headers
    ).json()["id"]

    db_session.add(
        CallRecord(
            account_id=account_id, phone_number_id=None, direction=CallDirection.INBOUND,
            from_number="+15559998888", to_number="+15550001111", provider_call_sid="CAcontacthist1",
            status="completed", duration=42,
        )
    )
    db_session.commit()

    history_response = client.get(f"/contacts/{contact_id}/history", headers=headers)
    assert history_response.status_code == 200
    body = history_response.json()
    assert len(body["calls"]) == 1
    assert body["calls"][0]["from"] == "+15559998888"
    assert body["voicemails"] == []


def test_contact_history_is_empty_for_a_contact_with_no_activity(client):
    token, _ = _signup_and_login(client, "contactshistory2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    contact_id = client.post(
        "/contacts", json={"name": "No History", "phone_number": "+15559997777"}, headers=headers
    ).json()["id"]

    response = client.get(f"/contacts/{contact_id}/history", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"calls": [], "voicemails": []}


def test_creating_a_contact_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    token, _ = _signup_and_login(client, "contactsaudit1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    contact_id = client.post(
        "/contacts", json={"name": "Audited", "phone_number": "+15550000007"}, headers=headers
    ).json()["id"]

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "contacts.created", AuditEvent.target == f"contact:{contact_id}")
        .all()
    )
    assert len(events) == 1
