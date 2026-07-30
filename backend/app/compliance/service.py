import uuid

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.compliance.models import ComplianceCase, ComplianceCaseStatus, ComplianceRule


def get_active_rules(db: Session, country: str) -> list[ComplianceRule]:
    return (
        db.query(ComplianceRule)
        .filter(ComplianceRule.country == country.upper(), ComplianceRule.is_active.is_(True))
        .all()
    )


def is_requirement_active(db: Session, country: str, requirement_type: str) -> bool:
    return (
        db.query(ComplianceRule)
        .filter(
            ComplianceRule.country == country.upper(),
            ComplianceRule.requirement_type == requirement_type,
            ComplianceRule.is_active.is_(True),
        )
        .first()
        is not None
    )


def open_compliance_case(
    db: Session,
    *,
    account_id: str,
    jurisdiction: str,
    requirement_type: str,
    actor: str,
    number_id: str | None = None,
) -> ComplianceCase:
    case = ComplianceCase(
        account_id=account_id,
        number_id=number_id,
        jurisdiction=jurisdiction.upper(),
        requirement_type=requirement_type,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    log_event(
        db,
        actor=actor,
        action="compliance.case_opened",
        target=f"compliance_case:{case.id}",
        after={"case_id": case.id, "jurisdiction": case.jurisdiction, "requirement_type": requirement_type},
    )
    return case


def list_cases_for_account(db: Session, account_id: str) -> list[ComplianceCase]:
    return (
        db.query(ComplianceCase)
        .filter(ComplianceCase.account_id == account_id)
        .order_by(ComplianceCase.created_at.desc())
        .all()
    )


def get_case(db: Session, case_id: str) -> ComplianceCase | None:
    try:
        uuid.UUID(case_id)
    except ValueError:
        return None  # not a valid UUID at all - can't possibly match a row
    return db.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()


def submit_document(
    db: Session, case: ComplianceCase, *, document_type: str, reference: str, actor: str
) -> ComplianceCase:
    """Tracks that a document was submitted for this case. This does NOT
    store the actual file - no cloud storage is wired up yet (needs its
    own provider credentials, same situation as Twilio). This records
    the metadata: what type of document, and a reference to where the
    real file would live once storage exists.
    """
    new_doc = {"document_type": document_type, "reference": reference}
    case.documents = [*case.documents, new_doc]  # reassign, not .append() - JSON columns need a new object to detect the change
    db.commit()
    db.refresh(case)

    log_event(
        db,
        actor=actor,
        action="compliance.document_submitted",
        target=f"compliance_case:{case.id}",
        after={"document_type": document_type},
    )
    return case


def approve_case(db: Session, case: ComplianceCase, *, actor: str) -> ComplianceCase:
    before_status = case.status
    case.status = ComplianceCaseStatus.APPROVED
    db.commit()
    db.refresh(case)

    log_event(
        db,
        actor=actor,
        action="compliance.case_approved",
        target=f"compliance_case:{case.id}",
        before={"status": before_status},
        after={"status": case.status},
    )
    return case


def reject_case(db: Session, case: ComplianceCase, *, actor: str, reason: str | None = None) -> ComplianceCase:
    before_status = case.status
    case.status = ComplianceCaseStatus.REJECTED
    db.commit()
    db.refresh(case)

    log_event(
        db,
        actor=actor,
        action="compliance.case_rejected",
        target=f"compliance_case:{case.id}",
        reason=reason,
        before={"status": before_status},
        after={"status": case.status},
    )
    return case
