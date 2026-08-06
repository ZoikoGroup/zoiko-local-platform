from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.billing.service import BillingSuspendedError
from app.contacts import service as contacts_service
from app.core.database import get_db
from app.core.deps import get_api_key_account_id
from app.intelligence.models import ConversationSummary
from app.media import service as media_service
from app.media.models import CallRecord, Voicemail
from app.numbering.numbers.models import PhoneNumber
from app.public_api.schemas import (
    CreateContactRequest,
    PlaceCallRequest,
    PlaceCallResponse,
    PublicCallResponse,
    PublicContactResponse,
    PublicNumberResponse,
    PublicSummaryResponse,
    PublicVoicemailResponse,
)
from app.risk import service as risk_service
from app.integrations.telecom.twilio import TelecomError

# Deliberately small and curated, per the Architecture doc's Phase 2
# posture: "Internal APIs must be designed cleanly from Phase 1, but
# public contracts should wait until domain behavior is stable." A
# subset of what already exists internally, not a mirror of every
# route. Number purchasing and account/team management stay behind the
# customer-session-only internal API - the two write actions here
# (placing a call, saving a contact) are the two that make sense to
# automate from an external system (a script, a CRM-side trigger, etc.)
# without also handing out account-management power through an API key.
router = APIRouter(prefix="/public/v1", tags=["public-api"])

_LIST_LIMIT = 200


@router.get("/numbers", response_model=list[PublicNumberResponse])
def list_numbers(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)):
    return (
        db.query(PhoneNumber)
        .filter(PhoneNumber.account_id == account_id)
        .order_by(PhoneNumber.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.get("/calls", response_model=list[PublicCallResponse])
def list_calls(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)):
    return (
        db.query(CallRecord)
        .filter(CallRecord.account_id == account_id)
        .order_by(CallRecord.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.get("/voicemails", response_model=list[PublicVoicemailResponse])
def list_voicemails(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)):
    return (
        db.query(Voicemail)
        .filter(Voicemail.account_id == account_id)
        .order_by(Voicemail.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.get("/summaries", response_model=list[PublicSummaryResponse])
def list_summaries(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)):
    return (
        db.query(ConversationSummary)
        .filter(ConversationSummary.account_id == account_id)
        .order_by(ConversationSummary.created_at.desc())
        .limit(_LIST_LIMIT)
        .all()
    )


@router.post("/calls", response_model=PlaceCallResponse, status_code=status.HTTP_201_CREATED)
def place_call(
    payload: PlaceCallRequest,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    try:
        result = media_service.place_outbound_call_for_account(
            db, account_id=account_id, to=payload.to, from_number=payload.from_number, message=payload.message,
        )
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except BillingSuspendedError as e:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)) from e
    except risk_service.DestinationBlockedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except risk_service.VelocityLimitExceededError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return result


@router.get("/contacts", response_model=list[PublicContactResponse])
def list_contacts(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)):
    return contacts_service.list_contacts(db, account_id)


@router.post("/contacts", response_model=PublicContactResponse, status_code=status.HTTP_201_CREATED)
def create_contact(
    payload: CreateContactRequest,
    account_id: str = Depends(get_api_key_account_id),
    db: Session = Depends(get_db),
):
    return contacts_service.create_contact(
        db, account_id=account_id, user_id=None, name=payload.name, phone_number=payload.phone_number,
        email=payload.email, notes=payload.notes,
    )
