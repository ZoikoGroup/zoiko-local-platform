from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Queue Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (creating/managing
    # a queue) for a TRIALING account, and this file's tests are about
    # queue mechanics, not trial-gating, so upgrade to a real paid plan
    # here rather than adding this to every individual test. Pro, not
    # starter: the full-lifecycle test wires a queue into a published Call
    # Flow, which needs the routing.advanced entitlement (Pro+ only).
    client.put(
        "/billing/subscription/plan", json={"plan_code": "pro", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _me(client, token) -> dict:
    return client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()


def _make_active_number(db_session, account_id: str, e164: str) -> PhoneNumber:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    return number


def test_create_queue_and_manage_membership(client, db_session):
    token = _signup_and_login(client, "queue-crud1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = _me(client, token)

    created = client.post("/queues", json={"name": "Sales", "max_wait_seconds": 60, "wrap_up_seconds": 15}, headers=headers)
    assert created.status_code == 201
    queue = created.json()
    assert queue["name"] == "Sales"
    assert queue["members"] == []

    added = client.post(f"/queues/{queue['id']}/members", json={"user_id": me["id"]}, headers=headers)
    assert added.status_code == 200
    assert [m["user_id"] for m in added.json()["members"]] == [me["id"]]

    removed = client.delete(f"/queues/{queue['id']}/members/{me['id']}", headers=headers)
    assert removed.json()["members"] == []


def test_cannot_access_another_accounts_queue(client, db_session):
    token_a = _signup_and_login(client, "queue-iso1a@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    queue = client.post("/queues", json={"name": "Support"}, headers=headers_a).json()

    token_b = _signup_and_login(client, "queue-iso1b@example.com")
    headers_b = {"Authorization": f"Bearer {token_b}"}
    response = client.get(f"/queues/{queue['id']}", headers=headers_b)
    assert response.status_code == 404


def test_presence_defaults_offline_and_rejects_manual_wrap_up(client, db_session):
    token = _signup_and_login(client, "presence1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    presence = client.get("/queues/presence/me", headers=headers).json()
    assert presence["status"] == "offline"
    assert presence["effectively_available"] is False

    updated = client.put("/queues/presence/me", json={"status": "available"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["status"] == "available"
    assert updated.json()["effectively_available"] is True

    rejected = client.put("/queues/presence/me", json={"status": "wrap_up"}, headers=headers)
    assert rejected.status_code == 400


def test_pull_next_requires_membership_phone_and_a_waiting_caller(client, db_session):
    token = _signup_and_login(client, "pull1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = _me(client, token)
    queue = client.post("/queues", json={"name": "Sales"}, headers=headers).json()

    not_member = client.post(f"/queues/{queue['id']}/pull-next", headers=headers)
    assert not_member.status_code == 403

    client.post(f"/queues/{queue['id']}/members", json={"user_id": me["id"]}, headers=headers)
    no_phone = client.post(f"/queues/{queue['id']}/pull-next", headers=headers)
    assert no_phone.status_code == 400

    client.put("/auth/me/phone", json={"phone_number": "+15551230000"}, headers=headers)
    nobody_waiting = client.post(f"/queues/{queue['id']}/pull-next", headers=headers)
    assert nobody_waiting.status_code == 409


def test_full_queue_lifecycle_enqueue_pull_answer_wrapup(client, db_session, monkeypatch):
    token = _signup_and_login(client, "lifecycle1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = _me(client, token)
    account_id = me["account_id"]
    number = _make_active_number(db_session, account_id, "+15550551111")

    queue = client.post("/queues", json={"name": "Support", "max_wait_seconds": 60, "wrap_up_seconds": 20}, headers=headers).json()
    client.post(f"/queues/{queue['id']}/members", json={"user_id": me["id"]}, headers=headers)
    client.put("/auth/me/phone", json={"phone_number": "+15559990000"}, headers=headers)

    flow = client.post("/call-flows", json={"name": "Support Line"}, headers=headers).json()
    nodes = [{"id": "q1", "type": "queue", "queue_id": queue["id"], "overflow_node_id": "vm1"}, {"id": "vm1", "type": "voicemail"}]
    client.put(f"/call-flows/{flow['id']}/draft", json={"entry_node_id": "q1", "nodes": nodes}, headers=headers)
    publish = client.post(f"/call-flows/{flow['id']}/publish", headers=headers)
    assert publish.json()["published"] is True, publish.json()
    client.post(f"/call-flows/{flow['id']}/assign", json={"phone_number_id": number.id}, headers=headers)

    # 1. Caller dials in - should be enqueued, with the voicemail overflow spliced in as fallthrough.
    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {"To": "+15550551111", "From": "+15551110000", "CallSid": "CAqueue1", "CallStatus": "ringing"}
    sig = _twilio_signature(incoming_url, incoming_params)
    incoming = client.post("/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": sig})
    assert incoming.status_code == 200
    assert "<Enqueue" in incoming.text
    assert f"zoiko-queue-{queue['id']}" in incoming.text
    assert "<Record" in incoming.text  # the overflow, spliced in as fallthrough

    # 2. Twilio's waitUrl fires almost immediately - this is what actually creates the log row.
    wait_url = f"http://testserver/media/voice/queue/wait?queue_id={queue['id']}&to_number=%2B15550551111"
    wait_params = {"CallSid": "CAqueue1", "From": "+15551110000", "QueueTime": "0"}
    wait_sig = _twilio_signature(wait_url, wait_params)
    wait_resp = client.post(
        f"/media/voice/queue/wait?queue_id={queue['id']}&to_number=%2B15550551111",
        data=wait_params, headers={"X-Twilio-Signature": wait_sig},
    )
    assert wait_resp.status_code == 200
    assert "<Say>" in wait_resp.text and "<Pause" in wait_resp.text

    status_after_wait = client.get(f"/queues/{queue['id']}/status", headers=headers).json()
    assert status_after_wait["waiting_count"] == 1
    assert status_after_wait["in_progress_count"] == 0

    # 3. Agent pulls the next caller - place_call is stubbed, no real Twilio call placed.
    monkeypatch.setattr(
        "app.queues.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAagentcall1", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    pulled = client.post(f"/queues/{queue['id']}/pull-next", headers=headers)
    assert pulled.status_code == 200
    assert pulled.json()["caller_number"] == "+15551110000"

    # 4. Agent's phone answers - Twilio hits agent-connect, which bridges into the real queue
    #    and marks our own log row as answered.
    connect = client.post(
        f"/media/voice/queue/agent-connect?queue_id={queue['id']}&agent_user_id={me['id']}"
        f"&queue_call_log_id={pulled.json()['queue_call_log_id']}"
    )
    assert connect.status_code == 200
    assert f"<Dial><Queue>zoiko-queue-{queue['id']}</Queue></Dial>" in connect.text

    status_after_answer = client.get(f"/queues/{queue['id']}/status", headers=headers).json()
    assert status_after_answer["waiting_count"] == 0
    assert status_after_answer["in_progress_count"] == 1

    # 5. The agent-facing call ends - agent enters wrap-up.
    ended_url = f"http://testserver/media/voice/queue/agent-call-ended?agent_user_id={me['id']}&queue_id={queue['id']}"
    ended_params = {"CallSid": "CAagentcall1", "CallStatus": "completed"}
    ended_sig = _twilio_signature(ended_url, ended_params)
    ended = client.post(
        f"/media/voice/queue/agent-call-ended?agent_user_id={me['id']}&queue_id={queue['id']}",
        data=ended_params, headers={"X-Twilio-Signature": ended_sig},
    )
    assert ended.status_code == 204
    presence = client.get("/queues/presence/me", headers=headers).json()
    assert presence["status"] == "wrap_up"
    assert presence["effectively_available"] is False

    # 6. The queue's own action callback finalizes the log as answered.
    left_url = "http://testserver/media/voice/queue/left"
    left_params = {"CallSid": "CAqueue1", "QueueResult": "bridged"}
    left_sig = _twilio_signature(left_url, left_params)
    left = client.post("/media/voice/queue/left", data=left_params, headers={"X-Twilio-Signature": left_sig})
    assert left.status_code == 204

    final_status = client.get(f"/queues/{queue['id']}/status", headers=headers).json()
    assert final_status["waiting_count"] == 0
    assert final_status["in_progress_count"] == 0


def test_wait_webhook_leaves_queue_once_max_wait_exceeded(client, db_session):
    token = _signup_and_login(client, "maxwait1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    queue = client.post("/queues", json={"name": "Overflow Test", "max_wait_seconds": 30}, headers=headers).json()

    wait_url = f"http://testserver/media/voice/queue/wait?queue_id={queue['id']}"
    params = {"CallSid": "CAoverflow1", "From": "+15551119999", "QueueTime": "31"}
    sig = _twilio_signature(wait_url, params)
    response = client.post(
        f"/media/voice/queue/wait?queue_id={queue['id']}", data=params, headers={"X-Twilio-Signature": sig}
    )
    assert response.status_code == 200
    assert "<Leave" in response.text
