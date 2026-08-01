from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.consent.models import ConsentRecord, ConsentType


class ConsentNotFoundError(Exception):
    """Raised when revoking consent that was never granted."""


def _find(db: Session, account_id: str, consent_type: ConsentType) -> ConsentRecord | None:
    return (
        db.query(ConsentRecord)
        .filter(ConsentRecord.account_id == account_id, ConsentRecord.consent_type == consent_type)
        .first()
    )


def grant_consent(db: Session, account_id: str, consent_type: ConsentType) -> ConsentRecord:
    record = _find(db, account_id, consent_type)
    now = datetime.now(timezone.utc)
    if record is None:
        record = ConsentRecord(account_id=account_id, consent_type=consent_type, granted_at=now)
        db.add(record)
    else:
        record.granted_at = now
        record.revoked_at = None
    db.commit()
    db.refresh(record)
    log_event(
        db, actor_id=account_id, action="consent.granted",
        target_type="consent_record", target_id=record.id, metadata={"consent_type": consent_type.value},
    )
    return record


def revoke_consent(db: Session, account_id: str, consent_type: ConsentType) -> ConsentRecord:
    record = _find(db, account_id, consent_type)
    if record is None:
        raise ConsentNotFoundError(f"No {consent_type.value} consent has been granted for this account")

    record.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    log_event(
        db, actor_id=account_id, action="consent.revoked",
        target_type="consent_record", target_id=record.id, metadata={"consent_type": consent_type.value},
    )
    return record


def has_active_consent(db: Session, account_id: str, consent_type: ConsentType) -> bool:
    record = _find(db, account_id, consent_type)
    return record is not None and record.granted_at is not None and record.revoked_at is None


def get_consent_status(db: Session, account_id: str) -> list[ConsentRecord]:
    return db.query(ConsentRecord).filter(ConsentRecord.account_id == account_id).all()
