from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_api_key_account_id
from app.intelligence.models import ConversationSummary
from app.media.models import CallRecord, Voicemail
from app.numbering.numbers.models import PhoneNumber
from app.public_api.schemas import (
    PublicCallResponse,
    PublicNumberResponse,
    PublicSummaryResponse,
    PublicVoicemailResponse,
)

# Deliberately small and read-only, per the Architecture doc's Phase 2
# posture: "Internal APIs must be designed cleanly from Phase 1, but
# public contracts should wait until domain behavior is stable." This is
# a curated subset of what already exists internally, not a mirror of
# every route - state-changing actions (placing calls, buying numbers)
# stay behind the customer-session-only internal API for now.
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
