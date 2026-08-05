from app.media.models import CallDirection, CallRecord, ReceptionistCall, ReceptionistUrgency, Voicemail
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Contacts Test Co", "account_type": "business",
            "email": email, "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def test_create_contact_requires_auth(client):
    response = client.post("/contacts", json={"name": "Jordan", "phone_number": "+15550001111"})
    assert response.status_code == 401


def test_create_and_list_contact(client):
    token, _ = _signup_and_login(client, "contacts1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create = client.post(
        "/contacts", headers=headers,
        json={"name": "Jordan Lee", "phone_number": "+15550001111", "email": "jordan@example.com", "notes": "VIP"},
    )
    assert create.status_code == 201
    body = create.json()
    assert body["name"] == "Jordan Lee"
    assert body["phone_number"] == "+15550001111"
    assert body["email"] == "jordan@example.com"
    assert body["notes"] == "VIP"

    listed = client.get("/contacts", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


def test_create_duplicate_phone_number_conflicts(client):
    token, _ = _signup_and_login(client, "contacts2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/contacts", headers=headers, json={"name": "Jordan", "phone_number": "+15550002222"})
    dupe = client.post("/contacts", headers=headers, json={"name": "Someone Else", "phone_number": "+15550002222"})
    assert dupe.status_code == 409


def test_get_update_and_delete_contact(client):
    token, _ = _signup_and_login(client, "contacts3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/contacts", headers=headers, json={"name": "Jordan", "phone_number": "+15550003333"},
    ).json()

    fetched = client.get(f"/contacts/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Jordan"

    updated = client.put(
        f"/contacts/{created['id']}", headers=headers,
        json={"name": "Jordan Lee", "phone_number": "+15550003333", "notes": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Jordan Lee"
    assert updated.json()["notes"] == "Updated"

    deleted = client.delete(f"/contacts/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/contacts/{created['id']}", headers=headers).status_code == 404


def test_update_contact_to_a_phone_number_already_used_conflicts(client):
    token, _ = _signup_and_login(client, "contacts4@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/contacts", headers=headers, json={"name": "A", "phone_number": "+15550004444"})
    b = client.post("/contacts", headers=headers, json={"name": "B", "phone_number": "+15550005555"}).json()

    conflict = client.put(
        f"/contacts/{b['id']}", headers=headers, json={"name": "B", "phone_number": "+15550004444"},
    )
    assert conflict.status_code == 409


def test_contact_from_another_account_is_not_found(client):
    owner_token, _ = _signup_and_login(client, "contacts5owner@example.com")
    intruder_token, _ = _signup_and_login(client, "contacts5intruder@example.com")

    owner_contact = client.post(
        "/contacts", headers={"Authorization": f"Bearer {owner_token}"},
        json={"name": "Owner's Contact", "phone_number": "+15550006666"},
    ).json()

    intruder_headers = {"Authorization": f"Bearer {intruder_token}"}
    assert client.get(f"/contacts/{owner_contact['id']}", headers=intruder_headers).status_code == 404
    assert client.put(
        f"/contacts/{owner_contact['id']}", headers=intruder_headers,
        json={"name": "Hijacked", "phone_number": "+15550006666"},
    ).status_code == 404
    assert client.delete(f"/contacts/{owner_contact['id']}", headers=intruder_headers).status_code == 404


def test_contact_history_aggregates_calls_voicemails_and_receptionist_calls(client, db_session):
    token, account_id = _signup_and_login(client, "contacts6@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    phone = "+15550007777"

    contact = client.post("/contacts", headers=headers, json={"name": "Riley", "phone_number": phone}).json()

    number = PhoneNumber(e164="+15559990000", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.flush()

    db_session.add(CallRecord(
        account_id=account_id, direction=CallDirection.INBOUND, from_number=phone, to_number=number.e164,
        status="completed", duration=42,
    ))
    db_session.add(Voicemail(
        phone_number_id=number.id, account_id=account_id, from_number=phone,
        recording_url="https://example.com/vm.mp3", duration=15,
    ))
    db_session.add(ReceptionistCall(
        account_id=account_id, phone_number_id=number.id, call_sid="CAtest123", caller_number=phone,
        raw_transcript="Hello, calling about an order.", summary="Riley called about an order.",
        urgency=ReceptionistUrgency.LOW,
    ))
    db_session.commit()

    # unrelated contact/number - must not show up in Riley's history
    other_phone = "+15550008888"
    db_session.add(CallRecord(
        account_id=account_id, direction=CallDirection.OUTBOUND, from_number=number.e164, to_number=other_phone,
        status="completed", duration=5,
    ))
    db_session.commit()

    history = client.get(f"/contacts/{contact['id']}/history", headers=headers)
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 3
    assert {e["type"] for e in entries} == {"call", "voicemail", "receptionist_call"}

    receptionist_entry = next(e for e in entries if e["type"] == "receptionist_call")
    assert receptionist_entry["summary"] == "Riley called about an order."
    assert receptionist_entry["status"] == "low"

    call_entry = next(e for e in entries if e["type"] == "call")
    assert call_entry["duration"] == 42

    voicemail_entry = next(e for e in entries if e["type"] == "voicemail")
    assert voicemail_entry["recording_url"] == "https://example.com/vm.mp3"


def test_contact_history_requires_auth(client):
    response = client.get("/contacts/some-id/history")
    assert response.status_code == 401
