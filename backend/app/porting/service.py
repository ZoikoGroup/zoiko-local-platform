import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.events.service import (
    publish_porting_request_approved,
    publish_porting_request_canceled,
    publish_porting_request_completed,
    publish_porting_request_rejected,
    publish_porting_request_submitted,
)
from app.notifications.service import (
    notify_porting_request_approved,
    notify_porting_request_canceled,
    notify_porting_request_completed,
    notify_porting_request_rejected,
    notify_porting_request_submitted,
)
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus
from app.porting.models import PortingRequest, PortingRequestStatus


class PortingRequestAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the given porting request."""


class PortingRequestConflictError(Exception):
    """Raised for an invalid state transition, or a number that can't be
    ported in (already active on this platform, or already has a request
    in flight)."""


def _account_owner_email(db: Session, account_id: str) -> str | None:
    from app.numbering.identity.models import User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    return owner.email if owner else None


def submit_porting_request(
    db: Session,
    *,
    account_id: str,
    requested_by_user_id: str,
    phone_number: str,
    country: str,
    current_carrier: str,
    carrier_account_number: str,
    billing_name: str,
    billing_address: str,
    authorization_evidence_url: str | None = None,
    target_completion_date: date | None = None,
) -> PortingRequest:
    if db.query(PhoneNumber).filter(PhoneNumber.e164 == phone_number).first() is not None:
        raise PortingRequestConflictError(f"{phone_number} is already a number on this platform")

    existing_in_flight = (
        db.query(PortingRequest)
        .filter(
            PortingRequest.phone_number == phone_number,
            PortingRequest.status.in_([PortingRequestStatus.SUBMITTED, PortingRequestStatus.APPROVED]),
        )
        .first()
    )
    if existing_in_flight is not None:
        raise PortingRequestConflictError(f"A porting request for {phone_number} is already in progress")

    request = PortingRequest(
        account_id=account_id,
        requested_by_user_id=requested_by_user_id,
        phone_number=phone_number,
        country=country.upper(),
        current_carrier=current_carrier,
        carrier_account_number=carrier_account_number,
        billing_name=billing_name,
        billing_address=billing_address,
        authorization_evidence_url=authorization_evidence_url,
        target_completion_date=target_completion_date,
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    log_event(
        db, actor=requested_by_user_id, action="porting.request_submitted",
        target=f"porting_request:{request.id}", after={"phone_number": phone_number, "country": request.country},
    )
    publish_porting_request_submitted(account_id, request_id=request.id, phone_number=phone_number, country=request.country)

    owner_email = _account_owner_email(db, account_id)
    if owner_email:
        notify_porting_request_submitted(
            db, account_id=account_id, account_email=owner_email, phone_number=phone_number,
            port_reference=request.id,
        )

    return request


def list_my_porting_requests(db: Session, account_id: str) -> list[PortingRequest]:
    return (
        db.query(PortingRequest)
        .filter(PortingRequest.account_id == account_id)
        .order_by(PortingRequest.created_at.desc())
        .all()
    )


def list_all_porting_requests(db: Session, status: str | None = None) -> list[dict]:
    """Staff-only view across every account - same join-in-context shape as
    compliance's list_all_cases, so a reviewer has enough to act without a
    second lookup."""
    from app.numbering.identity.models import Account, User, UserRole

    query = (
        db.query(PortingRequest, Account.name, User.email)
        .join(Account, Account.id == PortingRequest.account_id)
        .join(User, (User.account_id == Account.id) & (User.role == UserRole.OWNER))
    )
    if status:
        query = query.filter(PortingRequest.status == PortingRequestStatus(status))

    rows = query.order_by(PortingRequest.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "account_id": r.account_id,
            "account_name": account_name,
            "account_owner_email": owner_email,
            "phone_number": r.phone_number,
            "country": r.country,
            "current_carrier": r.current_carrier,
            "carrier_account_number": r.carrier_account_number,
            "billing_name": r.billing_name,
            "billing_address": r.billing_address,
            "authorization_evidence_url": r.authorization_evidence_url,
            "target_completion_date": r.target_completion_date,
            "status": r.status,
            "rejection_reason": r.rejection_reason,
            "twilio_incoming_number_sid": r.twilio_incoming_number_sid,
            "created_number_id": r.created_number_id,
            "created_at": r.created_at,
        }
        for r, account_name, owner_email in rows
    ]


def get_porting_request(db: Session, request_id: str) -> PortingRequest | None:
    try:
        uuid.UUID(request_id)
    except ValueError:
        return None
    return db.query(PortingRequest).filter(PortingRequest.id == request_id).first()


def cancel_porting_request(db: Session, request: PortingRequest, *, account_id: str, actor: str) -> PortingRequest:
    if request.account_id != account_id:
        raise PortingRequestAuthorizationError(f"{request.id} is not a porting request owned by your account")
    if request.status not in (PortingRequestStatus.SUBMITTED, PortingRequestStatus.APPROVED):
        raise PortingRequestConflictError(f"Request is {request.status.value} and can no longer be canceled")

    before_status = request.status
    request.status = PortingRequestStatus.CANCELED
    db.commit()
    db.refresh(request)

    log_event(
        db, actor=actor, action="porting.request_canceled",
        target=f"porting_request:{request.id}", before={"status": before_status}, after={"status": request.status},
    )
    publish_porting_request_canceled(account_id, request_id=request.id)

    owner_email = _account_owner_email(db, account_id)
    if owner_email:
        from app.numbering.identity.models import User

        canceling_user = db.query(User).filter(User.id == actor).first()
        notify_porting_request_canceled(
            db, account_id=account_id, account_email=owner_email, port_reference=request.id,
            canceled_by=canceling_user.email if canceling_user else "your account",
        )

    return request


def approve_porting_request(db: Session, request: PortingRequest, *, actor: str) -> PortingRequest:
    """Marks the intake as vetted and ready for a staff member to actually
    perform the port with the losing carrier themselves (outside this
    system) - not a call to any carrier or Twilio API."""
    if request.status != PortingRequestStatus.SUBMITTED:
        raise PortingRequestConflictError(f"Request is {request.status.value}, not submitted")

    request.status = PortingRequestStatus.APPROVED
    db.commit()
    db.refresh(request)

    log_event(
        db, actor=actor, action="porting.request_approved",
        target=f"porting_request:{request.id}", after={"status": request.status},
    )
    publish_porting_request_approved(request.account_id, request_id=request.id)

    owner_email = _account_owner_email(db, request.account_id)
    if owner_email:
        notify_porting_request_approved(
            db, account_id=request.account_id, account_email=owner_email, phone_number=request.phone_number,
            port_reference=request.id,
        )

    return request


def reject_porting_request(
    db: Session, request: PortingRequest, *, actor: str, reason: str | None = None
) -> PortingRequest:
    if request.status in (PortingRequestStatus.COMPLETED, PortingRequestStatus.CANCELED):
        raise PortingRequestConflictError(f"Request is already {request.status.value}")

    request.status = PortingRequestStatus.REJECTED
    request.rejection_reason = reason
    db.commit()
    db.refresh(request)

    log_event(
        db, actor=actor, action="porting.request_rejected",
        target=f"porting_request:{request.id}", reason=reason, after={"status": request.status},
    )
    publish_porting_request_rejected(request.account_id, request_id=request.id, reason=reason)

    owner_email = _account_owner_email(db, request.account_id)
    if owner_email:
        notify_porting_request_rejected(
            db, account_id=request.account_id, account_email=owner_email,
            phone_number=request.phone_number, reason=reason, port_reference=request.id,
        )

    return request


def complete_porting_request(
    db: Session, request: PortingRequest, *, actor: str, twilio_incoming_number_sid: str
) -> PortingRequest:
    """Called by staff once they've actually finished the port with the
    losing carrier and the number is live on Twilio - this is what
    activates the number on the platform, the same payoff a real
    purchase_number() gives, just entered by a human instead of coming back
    from a buy_number() API call."""
    if request.status != PortingRequestStatus.APPROVED:
        raise PortingRequestConflictError(f"Request is {request.status.value}, not approved")
    if db.query(PhoneNumber).filter(PhoneNumber.e164 == request.phone_number).first() is not None:
        raise PortingRequestConflictError(f"{request.phone_number} is already a number on this platform")

    number = PhoneNumber(
        e164=request.phone_number,
        country=request.country,
        provider="twilio",
        provider_sid=twilio_incoming_number_sid,
        status=PhoneNumberStatus.ACTIVE,
        account_id=request.account_id,
    )
    db.add(number)

    request.status = PortingRequestStatus.COMPLETED
    request.twilio_incoming_number_sid = twilio_incoming_number_sid
    db.commit()
    db.refresh(request)
    db.refresh(number)

    request.created_number_id = number.id
    db.commit()
    db.refresh(request)

    log_event(
        db, actor=actor, action="porting.request_completed",
        target=f"porting_request:{request.id}",
        after={"status": request.status, "phone_number_id": number.id},
    )
    publish_porting_request_completed(request.account_id, request_id=request.id, phone_number_id=number.id)

    owner_email = _account_owner_email(db, request.account_id)
    if owner_email:
        notify_porting_request_completed(
            db, account_id=request.account_id, account_email=owner_email, phone_number=request.phone_number,
            port_reference=request.id,
        )

    return request
