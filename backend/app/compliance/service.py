from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.compliance.models import ComplianceCase, ComplianceRule


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
