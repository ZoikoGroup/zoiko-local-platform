from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.consent import service
from app.consent.models import ConsentType
from app.core.database import get_db
from app.core.deps import get_current_user
from app.numbering.identity.models import User

router = APIRouter(prefix="/compliance/consent", tags=["consent"])


class ConsentRequest(BaseModel):
    consent_type: ConsentType


@router.post("")
def grant_consent(
    payload: ConsentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    record = service.grant_consent(db, current_user.account_id, payload.consent_type)
    return {"consent_type": record.consent_type.value, "granted_at": record.granted_at}


@router.delete("/{consent_type}")
def revoke_consent(
    consent_type: ConsentType,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        record = service.revoke_consent(db, current_user.account_id, consent_type)
    except service.ConsentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"consent_type": record.consent_type.value, "revoked_at": record.revoked_at}


@router.get("")
def list_consent(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    records = service.get_consent_status(db, current_user.account_id)
    return [
        {"consent_type": r.consent_type.value, "granted_at": r.granted_at, "revoked_at": r.revoked_at}
        for r in records
    ]
