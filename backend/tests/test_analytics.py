from datetime import datetime, timedelta, timezone

from app.intelligence.models import ConversationSummary, SummarySourceType
from app.media.models import CallDirection, CallRecord, VideoParticipantSession, VideoSession, VideoSessionStatus
from app.messaging.models import Conversation, Message, MessagingChannel, MessageDirection, MessageStatus
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={"account_name": "Analytics Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    account_id = signup.json()["account_id"]
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    return token, account_id


def test_overview_aggregates_calls_video_and_messages(client, db_session):
    token, account_id = _signup_and_login(client, "analytics1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    number = PhoneNumber(e164="+15550030001", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.flush()

    db_session.add_all(
        [
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND, from_number="+15550099999",
                to_number=number.e164, status="completed", duration=60,
            ),
            CallRecord(
                account_id=account_id, direction=CallDirection.OUTBOUND, from_number=number.e164,
                to_number="+15550099998", status="completed", duration=120,
            ),
        ]
    )

    session = VideoSession(
        account_id=account_id, host_user_id=me["id"], room_name="zl-analyticstest1", status=VideoSessionStatus.ENDED,
    )
    db_session.add(session)
    db_session.flush()

    base = datetime.now(timezone.utc)
    db_session.add(
        VideoParticipantSession(
            video_session_id=session.id, participant_identity="a", joined_at=base, left_at=base + timedelta(minutes=10),
        )
    )

    conversation = Conversation(
        account_id=account_id, phone_number_id=number.id, customer_number="+15550099997",
        channel=MessagingChannel.SMS,
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id, direction=MessageDirection.OUTBOUND, body="hi",
                status=MessageStatus.SENT,
            ),
            Message(
                conversation_id=conversation.id, direction=MessageDirection.INBOUND, body="hey back",
                status=MessageStatus.RECEIVED,
            ),
        ]
    )

    db_session.add(
        ConversationSummary(
            account_id=account_id, source_type=SummarySourceType.CALL, source_id=account_id,
            transcript="a transcript", summary="a summary", model_version="groq/test",
        )
    )
    db_session.commit()

    response = client.get("/analytics/overview", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total_calls"] == 2
    assert data["total_call_minutes"] == 3.0
    assert data["total_video_minutes"] == 10.0
    assert data["total_messages"] == 2
    assert data["active_numbers"] == 1
    assert data["ai_summaries"] == 1

    today = datetime.now(timezone.utc).date().isoformat()
    todays_point = next(p for p in data["daily"] if p["date"] == today)
    assert todays_point["calls"] == 2
    assert todays_point["messages"] == 2


def test_overview_excludes_data_outside_the_range(client, db_session):
    token, account_id = _signup_and_login(client, "analytics2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    old = datetime.now(timezone.utc) - timedelta(days=45)
    db_session.add(
        CallRecord(
            account_id=account_id, direction=CallDirection.INBOUND, from_number="+15550099996",
            to_number="+15550099995", status="completed", duration=60, created_at=old,
        )
    )
    db_session.commit()

    response = client.get("/analytics/overview?days=30", headers=headers)
    assert response.status_code == 200
    assert response.json()["total_calls"] == 0


def test_overview_requires_admin(client):
    owner_token, _ = _signup_and_login(client, "analyticsowner3@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "analyticsmember3@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "analyticsmember3@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.get("/analytics/overview", headers={"Authorization": f"Bearer {member_token}"})
    assert response.status_code == 403


def test_export_csv_returns_header_and_daily_rows(client, db_session):
    token, account_id = _signup_and_login(client, "analytics4@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    db_session.add(
        CallRecord(
            account_id=account_id, direction=CallDirection.INBOUND, from_number="+15550099994",
            to_number="+15550099993", status="completed", duration=30,
        )
    )
    db_session.commit()

    response = client.get("/analytics/export.csv?days=7", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    body = response.text.splitlines()
    assert body[0] == "date,calls,call_minutes,video_minutes,messages"
    assert any(line.startswith(datetime.now(timezone.utc).date().isoformat()) for line in body[1:])
