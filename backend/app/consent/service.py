from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.consent.models import GLOBAL_JURISDICTION, ConsentRecord, ConsentType


class ConsentNotFoundError(Exception):
    """Raised when revoking consent that was never granted."""


def _find(db: Session, account_id: str, consent_type: ConsentType, jurisdiction: str) -> ConsentRecord | None:
    return (
        db.query(ConsentRecord)
        .filter(
            ConsentRecord.account_id == account_id,
            ConsentRecord.consent_type == consent_type,
            ConsentRecord.jurisdiction == jurisdiction,
        )
        .first()
    )


def grant_consent(
    db: Session, account_id: str, consent_type: ConsentType, jurisdiction: str = GLOBAL_JURISDICTION
) -> ConsentRecord:
    jurisdiction = jurisdiction.upper()
    record = _find(db, account_id, consent_type, jurisdiction)
    now = datetime.now(timezone.utc)
    if record is None:
        record = ConsentRecord(
            account_id=account_id, consent_type=consent_type, jurisdiction=jurisdiction, granted_at=now
        )
        db.add(record)
    else:
        record.granted_at = now
        record.revoked_at = None
    db.commit()
    db.refresh(record)
    log_event(
        db, actor_id=account_id, action="consent.granted",
        target_type="consent_record", target_id=record.id,
        metadata={"consent_type": consent_type.value, "jurisdiction": jurisdiction},
    )
    return record


def revoke_consent(
    db: Session, account_id: str, consent_type: ConsentType, jurisdiction: str = GLOBAL_JURISDICTION
) -> ConsentRecord:
    jurisdiction = jurisdiction.upper()
    record = _find(db, account_id, consent_type, jurisdiction)
    if record is None:
        raise ConsentNotFoundError(
            f"No {consent_type.value} consent has been granted for this account in {jurisdiction}"
        )

    record.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    log_event(
        db, actor_id=account_id, action="consent.revoked",
        target_type="consent_record", target_id=record.id,
        metadata={"consent_type": consent_type.value, "jurisdiction": jurisdiction},
    )
    return record


def _is_granted(record: ConsentRecord | None) -> bool:
    return record is not None and record.granted_at is not None and record.revoked_at is None


def has_active_consent(
    db: Session, account_id: str, consent_type: ConsentType, jurisdiction: str = GLOBAL_JURISDICTION
) -> bool:
    """A GLOBAL_JURISDICTION grant is a superset that covers every specific
    jurisdiction too - so an account that only ever used the single "grant
    consent" button (which grants GLOBAL) keeps working exactly as before.
    A narrower per-country grant does NOT get GLOBAL rights automatically;
    it only satisfies checks for that exact jurisdiction.
    """
    jurisdiction = jurisdiction.upper()
    if _is_granted(_find(db, account_id, consent_type, jurisdiction)):
        return True
    if jurisdiction != GLOBAL_JURISDICTION and _is_granted(
        _find(db, account_id, consent_type, GLOBAL_JURISDICTION)
    ):
        return True
    return False


def get_consent_status(db: Session, account_id: str) -> list[ConsentRecord]:
    return db.query(ConsentRecord).filter(ConsentRecord.account_id == account_id).all()
