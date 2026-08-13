import hashlib
import hmac
import json
import logging
import time

import pytest

from app.compliance.models import ComplianceRule


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Compliance Test Co",
            "account_type": "individual",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_list_rules_for_a_country(client, db_session):
    db_session.add(
        ComplianceRule(
            country="US",
            requirement_type="kyc_individual",
            required_documents=["government_id"],
        )
    )
    db_session.commit()

    response = client.get("/compliance/rules?country=US")
    assert response.status_code == 200
    assert any(r["requirement_type"] == "kyc_individual" for r in response.json())


def test_list_rules_for_country_with_no_rules_returns_empty(client):
    response = client.get("/compliance/rules?country=ZZ")
    assert response.status_code == 200
    assert response.json() == []


def test_open_compliance_case_requires_auth(client):
    response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "US", "requirement_type": "kyc_individual"},
    )
    assert response.status_code == 401


def test_open_and_list_compliance_case(client):
    token = _signup_and_login(client, "compliance1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "us", "requirement_type": "kyc_individual"},
        headers=headers,
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["jurisdiction"] == "US"  # normalized to uppercase
    assert body["status"] == "pending"

    list_response = client.get("/compliance/cases/me", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_opening_a_case_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    token = _signup_and_login(client, "compliance2@example.com")
    case_response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "GB", "requirement_type": "kyc_individual"},
        headers={"Authorization": f"Bearer {token}"},
    )
    case_id = case_response.json()["id"]

    events = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.action == "compliance.case_opened",
            AuditEvent.target == f"compliance_case:{case_id}",
        )
        .all()
    )
    assert len(events) == 1


def _create_and_login_staff(db_session, client, email: str) -> str:
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _open_case(client, headers, jurisdiction="US") -> str:
    response = client.post(
        "/compliance/cases",
        json={"jurisdiction": jurisdiction, "requirement_type": "kyc_individual"},
        headers=headers,
    )
    return response.json()["id"]


def _add_member(client, admin_headers, email: str) -> str:
    response = client.post(
        "/team/members",
        json={"email": email, "password": "supersecret123", "role": "member"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_plain_member_cannot_open_a_compliance_case(client):
    """Opening a KYC case is an account-wide legal decision - Owner/Admin
    only, same reasoning as AI-processing consent."""
    owner_token = _signup_and_login(client, "membercompowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _add_member(client, owner_headers, "membercompmember@example.com")

    member_token = client.post(
        "/auth/login", json={"email": "membercompmember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "US", "requirement_type": "kyc_individual"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_support_staff_cannot_approve_a_case(client, db_session):
    """Segregation of duties: SUPPORT is read-only, only COMPLIANCE_OFFICER
    and SUPER_ADMIN can approve/reject KYC cases."""
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    customer_token = _signup_and_login(client, "supportapprove@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_service.create_staff(
        db_session, email="staffsupport1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffsupport1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 403


def test_support_staff_can_still_list_all_cases(client, db_session):
    """Read-only ops actions (list/search) stay open to every staff tier."""
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    customer_token = _signup_and_login(client, "supportlist@example.com")
    _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_service.create_staff(
        db_session, email="staffsupport2@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffsupport2@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get("/compliance/cases", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200


def test_compliance_officer_can_approve_a_case(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    customer_token = _signup_and_login(client, "officerapprove@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_service.create_staff(
        db_session,
        email="staffofficer1@zoikolocal.com",
        password="staffpass123",
        role=PlatformStaffRole.COMPLIANCE_OFFICER,
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffofficer1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


@pytest.mark.live
def test_submit_document_adds_it_to_the_case(client):
    token = _signup_and_login(client, "docsubmit1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(
        f"/compliance/cases/{case_id}/documents",
        data={"document_type": "government_id"},
        files={"file": ("id.pdf", b"%PDF-1.4 fake id document", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    documents = response.json()["documents"]
    assert len(documents) == 1
    assert documents[0]["document_type"] == "government_id"
    assert documents[0]["filename"] == "id.pdf"
    assert documents[0]["content_type"] == "application/pdf"
    assert documents[0]["storage_key"].startswith(f"compliance-documents/{case_id}/")


def test_submit_document_rejects_an_unsupported_content_type(client):
    token = _signup_and_login(client, "docsubmitbadtype@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(
        f"/compliance/cases/{case_id}/documents",
        data={"document_type": "government_id"},
        files={"file": ("script.exe", b"not a real document", "application/x-msdownload")},
        headers=headers,
    )
    assert response.status_code == 422


def test_submit_document_on_someone_elses_case_is_forbidden(client):
    token_a = _signup_and_login(client, "docowner@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {token_a}"})

    token_b = _signup_and_login(client, "docintruder@example.com")
    response = client.post(
        f"/compliance/cases/{case_id}/documents",
        data={"document_type": "government_id"},
        files={"file": ("id.pdf", b"fake", "application/pdf")},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 403


def test_submit_document_on_missing_case_is_404(client):
    token = _signup_and_login(client, "docsubmit2@example.com")
    response = client.post(
        "/compliance/cases/does-not-exist/documents",
        data={"document_type": "government_id"},
        files={"file": ("id.pdf", b"fake", "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.live
def test_document_download_url_returns_a_real_presigned_link(client):
    token = _signup_and_login(client, "docdownload1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)
    client.post(
        f"/compliance/cases/{case_id}/documents",
        data={"document_type": "government_id"},
        files={"file": ("id.pdf", b"%PDF-1.4 fake id document", "application/pdf")},
        headers=headers,
    )

    response = client.get(f"/compliance/cases/{case_id}/documents/0/download-url", headers=headers)
    assert response.status_code == 200, response.text
    assert "X-Amz-Signature" in response.json()["url"]


def test_document_download_url_rejects_a_different_account(client):
    token_a = _signup_and_login(client, "docdownloadowner@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {token_a}"})
    client.post(
        f"/compliance/cases/{case_id}/documents",
        data={"document_type": "government_id"},
        files={"file": ("id.pdf", b"fake", "application/pdf")},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    token_b = _signup_and_login(client, "docdownloadintruder@example.com")
    response = client.get(
        f"/compliance/cases/{case_id}/documents/0/download-url",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 403


def test_document_download_url_404s_for_an_out_of_range_index(client):
    token = _signup_and_login(client, "docdownloadoor@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.get(f"/compliance/cases/{case_id}/documents/0/download-url", headers=headers)
    assert response.status_code == 404


def test_customer_owner_cannot_approve_their_own_case(client):
    """The gap flagged earlier: approving KYC must be a staff-only action,
    not something a customer can do to their own case."""
    token = _signup_and_login(client, "approve1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(f"/compliance/cases/{case_id}/approve", headers=headers)
    assert response.status_code == 401  # customer token rejected outright - wrong scope


def test_staff_can_approve_a_case(client, db_session):
    customer_token = _signup_and_login(client, "approve2@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_token = _create_and_login_staff(db_session, client, "staffapprove1@zoikolocal.com")
    response = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_staff_can_reject_a_case_with_a_reason(client, db_session):
    customer_token = _signup_and_login(client, "reject1@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_token = _create_and_login_staff(db_session, client, "staffreject1@zoikolocal.com")
    response = client.post(
        f"/compliance/cases/{case_id}/reject",
        json={"reason": "Document was blurry"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_approve_requires_authentication(client):
    token = _signup_and_login(client, "approve3@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {token}"})

    response = client.post(f"/compliance/cases/{case_id}/approve")
    assert response.status_code == 401


def test_approving_a_case_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    customer_token = _signup_and_login(client, "approve4@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_token = _create_and_login_staff(db_session, client, "staffapprove2@zoikolocal.com")
    client.post(f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"})

    events = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.action == "compliance.case_approved",
            AuditEvent.target == f"compliance_case:{case_id}",
        )
        .all()
    )
    assert len(events) == 1


def test_customer_cannot_list_all_cases(client):
    token = _signup_and_login(client, "listall1@example.com")
    response = client.get("/compliance/cases", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_staff_can_list_all_cases_with_account_context(client, db_session):
    customer_token = _signup_and_login(client, "listall2@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_token = _create_and_login_staff(db_session, client, "staffcases1@zoikolocal.com")
    response = client.get("/compliance/cases", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200

    match = next(c for c in response.json() if c["id"] == case_id)
    assert match["account_owner_email"] == "listall2@example.com"
    assert match["account_name"] == "Compliance Test Co"


def test_approving_a_case_notifies_the_account_owner(client, db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    customer_token = _signup_and_login(client, "notifyapprove@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_token = _create_and_login_staff(db_session, client, "staffnotify1@zoikolocal.com")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        client.post(f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"})

    assert any(
        "notifyapprove@example.com" in record.message and "approved" in record.message
        for record in caplog.records
    )


def test_rejecting_a_case_notifies_the_account_owner_with_reason(client, db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    customer_token = _signup_and_login(client, "notifyreject@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {customer_token}"})

    staff_token = _create_and_login_staff(db_session, client, "staffnotify2@zoikolocal.com")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        client.post(
            f"/compliance/cases/{case_id}/reject",
            json={"reason": "Document was blurry"},
            headers={"Authorization": f"Bearer {staff_token}"},
        )

    assert any(
        "notifyreject@example.com" in record.message and "Document was blurry" in record.message
        for record in caplog.records
    )


def _stripe_identity_webhook_body_and_signature(
    secret: str, session_id: str, status: str, last_error: dict | None = None
) -> tuple[bytes, str]:
    body = json.dumps(
        {
            "id": "evt_test",
            "object": "event",
            "type": f"identity.verification_session.{status}",
            "data": {
                "object": {
                    "id": session_id,
                    "object": "identity.verification_session",
                    "status": status,
                    "last_error": last_error,
                }
            },
        }
    ).encode()
    # Stripe's construct_event enforces a tolerance against the real wall
    # clock (default 300s) - a fixed/fake timestamp fails verification
    # once enough real time has passed, so this must use the current time.
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{body.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return body, f"t={timestamp},v1={signature}"


def test_start_kyc_requires_auth(client):
    response = client.post("/compliance/cases/does-not-exist/kyc/start")
    assert response.status_code == 401


def test_start_kyc_on_someone_elses_case_is_forbidden(client):
    token_a = _signup_and_login(client, "kycowner@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {token_a}"})

    token_b = _signup_and_login(client, "kycintruder@example.com")
    response = client.post(
        f"/compliance/cases/{case_id}/kyc/start", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert response.status_code == 403


def test_start_kyc_fails_cleanly_when_stripe_is_not_configured(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_secret_key", "")
    token = _signup_and_login(client, "kycnoconfig@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(f"/compliance/cases/{case_id}/kyc/start", headers=headers)
    assert response.status_code == 502


def test_start_kyc_returns_a_clean_502_on_a_genuine_stripe_failure(client, monkeypatch):
    """Chaos test: Stripe IS configured, but the API call itself fails (a
    real outage/timeout), not a missing secret key - the "not configured"
    test above only covers the latter."""
    from app.integrations.kyc.stripe_identity import KYCError

    monkeypatch.setattr("app.core.config.settings.stripe_secret_key", "sk_test_fake")

    def _raise(reference_id):
        raise KYCError("Stripe Identity request failed: connection timed out")

    monkeypatch.setattr("app.compliance.service.stripe_identity.create_verification_session", _raise)

    token = _signup_and_login(client, "kycstripedown@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(f"/compliance/cases/{case_id}/kyc/start", headers=headers)
    assert response.status_code == 502


def test_start_kyc_success_stores_inquiry_id_and_returns_verification_url(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(
        "app.compliance.service.stripe_identity.create_verification_session",
        lambda reference_id: {"id": "vs_test123", "url": "https://verify.stripe.com/start/test_abc"},
    )

    token = _signup_and_login(client, "kycsuccess@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(f"/compliance/cases/{case_id}/kyc/start", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["inquiry_id"] == "vs_test123"
    assert body["verification_url"] == "https://verify.stripe.com/start/test_abc"


def test_start_kyc_is_blocked_once_the_case_is_approved(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_secret_key", "sk_test_fake")

    token = _signup_and_login(client, "kycretryapproved@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    staff_token = _create_and_login_staff(db_session, client, "staffkycretry1@zoikolocal.com")
    client.post(f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"})

    response = client.post(f"/compliance/cases/{case_id}/kyc/start", headers=headers)
    assert response.status_code == 409


def test_start_kyc_retry_resets_a_rejected_case_back_to_pending(client, db_session, monkeypatch):
    """The self-service gap: a rejected customer must be able to retry
    without staff intervention, and the stale "rejected" verdict must not
    keep showing while a fresh attempt is in progress."""
    monkeypatch.setattr("app.core.config.settings.stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(
        "app.compliance.service.stripe_identity.create_verification_session",
        lambda reference_id: {"id": "vs_retry123", "url": "https://verify.stripe.com/start/test_retry"},
    )

    token = _signup_and_login(client, "kycretryafterreject@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    staff_token = _create_and_login_staff(db_session, client, "staffkycretry2@zoikolocal.com")
    client.post(
        f"/compliance/cases/{case_id}/reject",
        json={"reason": "Document was blurry"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )

    response = client.post(f"/compliance/cases/{case_id}/kyc/start", headers=headers)
    assert response.status_code == 200
    assert response.json()["inquiry_id"] == "vs_retry123"

    case_response = client.get("/compliance/cases/me", headers=headers)
    body = case_response.json()[0]
    assert body["status"] == "pending"
    assert body["kyc_inquiry_id"] == "vs_retry123"


def test_stripe_identity_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_identity_webhook_secret", "whsec_test")
    body, _ = _stripe_identity_webhook_body_and_signature("whsec_test", "vs_x", "verified")
    response = client.post(
        "/compliance/webhooks/stripe-identity", content=body, headers={"Stripe-Signature": "t=123,v1=not-real"}
    )
    assert response.status_code == 403


def test_stripe_identity_webhook_approves_the_matching_case(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_identity_webhook_secret", "whsec_test")

    token = _signup_and_login(client, "kycwebhookapprove@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    from app.compliance.models import ComplianceCase

    case = db_session.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()
    case.kyc_inquiry_id = "vs_approve_test"
    db_session.commit()

    body, signature = _stripe_identity_webhook_body_and_signature("whsec_test", "vs_approve_test", "verified")
    response = client.post(
        "/compliance/webhooks/stripe-identity", content=body, headers={"Stripe-Signature": signature}
    )
    assert response.status_code == 204

    case_response = client.get("/compliance/cases/me", headers=headers)
    assert case_response.json()[0]["status"] == "approved"


def test_stripe_identity_webhook_rejects_the_matching_case_on_cancel(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_identity_webhook_secret", "whsec_test")

    token = _signup_and_login(client, "kycwebhookreject@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    from app.compliance.models import ComplianceCase

    case = db_session.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()
    case.kyc_inquiry_id = "vs_decline_test"
    db_session.commit()

    body, signature = _stripe_identity_webhook_body_and_signature("whsec_test", "vs_decline_test", "canceled")
    response = client.post(
        "/compliance/webhooks/stripe-identity", content=body, headers={"Stripe-Signature": signature}
    )
    assert response.status_code == 204

    case_response = client.get("/compliance/cases/me", headers=headers)
    assert case_response.json()[0]["status"] == "rejected"


def test_stripe_identity_webhook_requires_input_with_no_error_is_a_noop(client, db_session, monkeypatch):
    """requires_input with no last_error is the session's initial state
    right after creation (confirmed live against the real Stripe API) -
    it must never be treated as a rejection."""
    monkeypatch.setattr("app.core.config.settings.stripe_identity_webhook_secret", "whsec_test")

    token = _signup_and_login(client, "kycwebhookpending@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    from app.compliance.models import ComplianceCase

    case = db_session.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()
    case.kyc_inquiry_id = "vs_pending_test"
    db_session.commit()

    body, signature = _stripe_identity_webhook_body_and_signature("whsec_test", "vs_pending_test", "requires_input")
    response = client.post(
        "/compliance/webhooks/stripe-identity", content=body, headers={"Stripe-Signature": signature}
    )
    assert response.status_code == 204

    case_response = client.get("/compliance/cases/me", headers=headers)
    assert case_response.json()[0]["status"] == "pending"


def test_stripe_identity_webhook_requires_input_with_last_error_rejects_the_case(client, db_session, monkeypatch):
    """Confirmed live against a real submission: Stripe testmode auto-marks
    document submissions unverified, and that failure surfaces as
    requires_input + a last_error, not as its own terminal status. Without
    checking last_error, a genuinely failed verification would silently
    stay pending forever."""
    monkeypatch.setattr("app.core.config.settings.stripe_identity_webhook_secret", "whsec_test")

    token = _signup_and_login(client, "kycwebhookrealfail@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    from app.compliance.models import ComplianceCase

    case = db_session.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()
    case.kyc_inquiry_id = "vs_realfail_test"
    db_session.commit()

    body, signature = _stripe_identity_webhook_body_and_signature(
        "whsec_test",
        "vs_realfail_test",
        "requires_input",
        last_error={"code": "document_unverified_other", "reason": "The provided document was not verified."},
    )
    response = client.post(
        "/compliance/webhooks/stripe-identity", content=body, headers={"Stripe-Signature": signature}
    )
    assert response.status_code == 204

    case_response = client.get("/compliance/cases/me", headers=headers)
    assert case_response.json()[0]["status"] == "rejected"


def test_stripe_identity_webhook_with_unknown_session_id_is_a_noop(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.stripe_identity_webhook_secret", "whsec_test")
    body, signature = _stripe_identity_webhook_body_and_signature("whsec_test", "vs_unknown", "verified")
    response = client.post(
        "/compliance/webhooks/stripe-identity", content=body, headers={"Stripe-Signature": signature}
    )
    assert response.status_code == 204


def test_staff_can_filter_cases_by_status(client, db_session):
    customer_token = _signup_and_login(client, "listall3@example.com")
    headers = {"Authorization": f"Bearer {customer_token}"}
    pending_case_id = _open_case(client, headers, jurisdiction="US")
    approved_case_id = _open_case(client, headers, jurisdiction="GB")

    staff_token = _create_and_login_staff(db_session, client, "staffcases2@zoikolocal.com")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}
    client.post(f"/compliance/cases/{approved_case_id}/approve", headers=staff_headers)

    response = client.get("/compliance/cases?status=pending", headers=staff_headers)
    ids = [c["id"] for c in response.json()]
    assert pending_case_id in ids
    assert approved_case_id not in ids
