from app.media.models import Voicemail
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str, account_name: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": account_name,
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _make_voicemail(db_session, account_id: str, e164: str) -> Voicemail:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()

    voicemail = Voicemail(
        phone_number_id=number.id,
        account_id=account_id,
        from_number="+15559990000",
        recording_url="https://example.com/fake.wav",
    )
    db_session.add(voicemail)
    db_session.commit()
    return voicemail


def test_summarize_voicemail_rejects_other_account(client, db_session):
    _, owner_account_id = _signup_and_login(client, "intelowner@example.com", "Intel Owner Co")
    voicemail = _make_voicemail(db_session, owner_account_id, "+15550004444")

    intruder_token, _ = _signup_and_login(client, "intelintruder@example.com", "Intel Intruder Co")

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_summarize_voicemail_rejects_without_consent(client, db_session):
    token, account_id = _signup_and_login(client, "intelnoconsent@example.com", "Intel No Consent Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550006666")

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()


def test_summarize_voicemail_success_path(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "intelsuccess@example.com", "Intel Success Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550005555")

    consent_response = client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert consent_response.status_code == 200

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr(
        "app.intelligence.service.transcribe_audio",
        lambda audio_bytes: "Hi, this is a test voicemail, please call me back.",
    )

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["transcript"] == "Hi, this is a test voicemail, please call me back."
    assert body["summary"]  # real Groq call — non-empty summary text
    assert body["model_version"]
    assert body["disclaimer"]

    list_response = client.get("/intelligence/summaries", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    revoke_response = client.delete(
        "/compliance/consent/ai_processing", headers={"Authorization": f"Bearer {token}"}
    )
    assert revoke_response.status_code == 200
