from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Call Flow Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def _make_active_number(client, db_session, token, e164: str) -> PhoneNumber:
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    return number


SIMPLE_MENU_NODES = [
    {
        "id": "menu1",
        "type": "menu",
        "prompt": "Press 1 for sales, 2 for support.",
        "options": {"1": "fwd_sales", "2": "fwd_support"},
        "invalid_node_id": "menu1",
    },
    {"id": "fwd_sales", "type": "forward", "destinations": ["+15551110000"], "on_no_answer_node_id": "vm1"},
    {"id": "fwd_support", "type": "forward", "destinations": ["+15552220000"]},
    {"id": "vm1", "type": "voicemail"},
]


def _create_and_publish_flow(client, headers, name="Main Line", nodes=None, entry_node_id="menu1"):
    flow = client.post("/call-flows", json={"name": name}, headers=headers).json()
    client.put(
        f"/call-flows/{flow['id']}/draft",
        json={"entry_node_id": entry_node_id, "nodes": nodes if nodes is not None else SIMPLE_MENU_NODES},
        headers=headers,
    )
    publish = client.post(f"/call-flows/{flow['id']}/publish", headers=headers)
    assert publish.status_code == 200, publish.text
    assert publish.json()["published"] is True, publish.json()
    return flow["id"]


def test_create_flow_has_an_empty_draft(client, db_session):
    token = _signup_and_login(client, "flow-create1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/call-flows", json={"name": "Main Line"}, headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Main Line"
    assert body["has_draft"] is True
    assert body["live_version"] is None

    detail = client.get(f"/call-flows/{body['id']}", headers=headers).json()
    assert detail["draft"]["nodes"] == []
    assert detail["live"] is None


def test_publish_fails_with_dangling_reference(client, db_session):
    token = _signup_and_login(client, "flow-invalid1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow = client.post("/call-flows", json={"name": "Broken"}, headers=headers).json()

    nodes = [{"id": "menu1", "type": "menu", "prompt": "Press 1", "options": {"1": "does-not-exist"}}]
    client.put(f"/call-flows/{flow['id']}/draft", json={"entry_node_id": "menu1", "nodes": nodes}, headers=headers)

    response = client.post(f"/call-flows/{flow['id']}/publish", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["published"] is False
    assert any("unknown node" in e for e in body["errors"])


def test_publish_fails_with_no_reachable_destination(client, db_session):
    token = _signup_and_login(client, "flow-invalid2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow = client.post("/call-flows", json={"name": "Loop"}, headers=headers).json()

    # A menu whose only option points back to itself never reaches a
    # forward/voicemail/receptionist/hangup node.
    nodes = [{"id": "menu1", "type": "menu", "prompt": "Press 1", "options": {"1": "menu1"}}]
    client.put(f"/call-flows/{flow['id']}/draft", json={"entry_node_id": "menu1", "nodes": nodes}, headers=headers)

    response = client.post(f"/call-flows/{flow['id']}/publish", headers=headers)
    body = response.json()
    assert body["published"] is False
    assert any("no reachable destination" in e for e in body["errors"])


def test_publish_creates_live_version_and_a_fresh_draft(client, db_session):
    token = _signup_and_login(client, "flow-publish1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow_id = _create_and_publish_flow(client, headers)

    detail = client.get(f"/call-flows/{flow_id}", headers=headers).json()
    assert detail["live"]["version"] == 1
    assert detail["live"]["status"] == "published"
    assert detail["draft"]["version"] == 2
    assert detail["draft"]["status"] == "draft"
    # The new draft starts as a copy of what was just published.
    assert detail["draft"]["nodes"] == SIMPLE_MENU_NODES


def test_rollback_restores_a_prior_version_as_a_new_version(client, db_session):
    token = _signup_and_login(client, "flow-rollback1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow_id = _create_and_publish_flow(client, headers)

    # Edit the draft and publish a second, different live version.
    changed_nodes = [{"id": "vm_only", "type": "voicemail"}]
    client.put(f"/call-flows/{flow_id}/draft", json={"entry_node_id": "vm_only", "nodes": changed_nodes}, headers=headers)
    client.post(f"/call-flows/{flow_id}/publish", headers=headers)

    detail = client.get(f"/call-flows/{flow_id}", headers=headers).json()
    assert detail["live"]["version"] == 2

    rollback = client.post(f"/call-flows/{flow_id}/rollback", json={"version": 1}, headers=headers)
    assert rollback.status_code == 200
    rolled_back = rollback.json()
    assert rolled_back["version"] == 3
    assert rolled_back["status"] == "published"
    assert rolled_back["rolled_back_from_version"] == 1
    assert rolled_back["nodes"] == SIMPLE_MENU_NODES


def test_assign_and_unassign_call_flow_to_a_number(client, db_session):
    token = _signup_and_login(client, "flow-assign1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_active_number(client, db_session, token, "+15550009000")
    flow_id = _create_and_publish_flow(client, headers)

    response = client.post(f"/call-flows/{flow_id}/assign", json={"phone_number_id": number.id}, headers=headers)
    assert response.status_code == 200
    assert response.json()["call_flow_id"] == flow_id

    response = client.post(f"/call-flows/unassign/{number.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["call_flow_id"] is None


def test_cannot_access_another_accounts_call_flow(client, db_session):
    token_a = _signup_and_login(client, "flow-iso1a@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    flow_id = _create_and_publish_flow(client, headers_a)

    token_b = _signup_and_login(client, "flow-iso1b@example.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    response = client.get(f"/call-flows/{flow_id}", headers=headers_b)
    assert response.status_code == 404


def test_incoming_call_with_a_live_flow_returns_the_menu_gather(client, db_session):
    token = _signup_and_login(client, "flow-incoming1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_active_number(client, db_session, token, "+15550009111")
    flow_id = _create_and_publish_flow(client, headers)
    client.post(f"/call-flows/{flow_id}/assign", json={"phone_number_id": number.id}, headers=headers)

    url = "http://testserver/media/voice/incoming"
    params = {"To": "+15550009111", "From": "+15559990001", "CallSid": "CAflow1", "CallStatus": "ringing"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/incoming", data=params, headers={"X-Twilio-Signature": signature})

    assert response.status_code == 200
    assert "<Gather" in response.text
    assert "Press 1 for sales" in response.text
    assert "flow-menu-input" in response.text
    assert "node_id=menu1" in response.text


def test_menu_digit_routes_to_the_matching_forward_node(client, db_session):
    token = _signup_and_login(client, "flow-digit1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow_id = _create_and_publish_flow(client, headers)
    live_version_id = client.get(f"/call-flows/{flow_id}", headers=headers).json()["live"]["id"]

    url = f"http://testserver/media/voice/flow-menu-input?flow_version_id={live_version_id}&node_id=menu1"
    params = {"CallSid": "CAflow2", "Digits": "2"}
    signature = _twilio_signature(url, params)
    response = client.post(
        f"/media/voice/flow-menu-input?flow_version_id={live_version_id}&node_id=menu1",
        data=params, headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert "+15552220000" in response.text
    assert "<Number>" in response.text


def test_invalid_menu_digit_repeats_the_same_menu(client, db_session):
    token = _signup_and_login(client, "flow-digit2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow_id = _create_and_publish_flow(client, headers)
    live_version_id = client.get(f"/call-flows/{flow_id}", headers=headers).json()["live"]["id"]

    url = f"http://testserver/media/voice/flow-menu-input?flow_version_id={live_version_id}&node_id=menu1"
    params = {"CallSid": "CAflow3", "Digits": "9"}
    signature = _twilio_signature(url, params)
    response = client.post(
        f"/media/voice/flow-menu-input?flow_version_id={live_version_id}&node_id=menu1",
        data=params, headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert "<Gather" in response.text
    assert "Press 1 for sales" in response.text


def test_forward_node_failover_routes_to_its_configured_node(client, db_session):
    token = _signup_and_login(client, "flow-failover1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow_id = _create_and_publish_flow(client, headers)
    live_version_id = client.get(f"/call-flows/{flow_id}", headers=headers).json()["live"]["id"]

    url = f"http://testserver/media/voice/flow-forward-fallback?flow_version_id={live_version_id}&node_id=fwd_sales"
    params = {"CallSid": "CAflow4", "DialCallStatus": "no-answer"}
    signature = _twilio_signature(url, params)
    response = client.post(
        f"/media/voice/flow-forward-fallback?flow_version_id={live_version_id}&node_id=fwd_sales",
        data=params, headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert "<Record" in response.text


def test_forward_node_without_failover_target_defaults_to_voicemail(client, db_session):
    token = _signup_and_login(client, "flow-failover2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    flow_id = _create_and_publish_flow(client, headers)
    live_version_id = client.get(f"/call-flows/{flow_id}", headers=headers).json()["live"]["id"]

    # fwd_support has no on_no_answer_node_id configured.
    url = f"http://testserver/media/voice/flow-forward-fallback?flow_version_id={live_version_id}&node_id=fwd_support"
    params = {"CallSid": "CAflow5", "DialCallStatus": "busy"}
    signature = _twilio_signature(url, params)
    response = client.post(
        f"/media/voice/flow-forward-fallback?flow_version_id={live_version_id}&node_id=fwd_support",
        data=params, headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert "<Record" in response.text
