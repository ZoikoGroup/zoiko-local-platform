import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.contacts.models import Contact
from app.contacts.schemas import ContactHistoryEntry
from app.crm.service import sync_contact_to_crm
from app.media.models import CallRecord, ReceptionistCall, Voicemail
from app.numbering.identity.models import User
from app.numbering.numbers.service import assigned_number_ids


class ContactNotFoundError(Exception):
    """Raised when a contact_id doesn't exist or belongs to a different account."""


def list_contacts(db: Session, account_id: str) -> list[Contact]:
    return db.query(Contact).filter(Contact.account_id == account_id).order_by(Contact.name.asc()).all()


def get_contact(db: Session, account_id: str, contact_id: str) -> Contact:
    contact = db.query(Contact).filter(Contact.id == contact_id, Contact.account_id == account_id).first()
    if contact is None:
        raise ContactNotFoundError(f"{contact_id} is not a contact on your account")
    return contact


def create_contact(
    db: Session, *, account_id: str, user_id: str | None, name: str, phone_number: str,
    email: str | None, notes: str | None,
) -> Contact:
    """user_id is None when called from the public API (an API key has no
    logged-in user to attribute it to) - left off Contact.created_by_user_id
    too in that case, but the audit log always needs a real actor string,
    so it falls back to "public_api" (see log_event's actor column, which
    is free text, not a foreign key)."""
    contact = Contact(
        account_id=account_id, name=name, phone_number=phone_number, email=email, notes=notes,
        created_by_user_id=user_id,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    log_event(
        db, actor=user_id or "public_api", action="contacts.created", target=f"contact:{contact.id}",
        after={"name": name, "phone_number": phone_number},
    )
    sync_contact_to_crm(db, account_id=account_id, contact_id=contact.id, name=name, phone_number=phone_number)
    return contact


def update_contact(
    db: Session, *, account_id: str, user_id: str, contact_id: str, name: str, phone_number: str,
    email: str | None, notes: str | None,
) -> Contact:
    contact = get_contact(db, account_id, contact_id)
    before = {"name": contact.name, "phone_number": contact.phone_number}
    contact.name = name
    contact.phone_number = phone_number
    contact.email = email
    contact.notes = notes
    db.commit()
    db.refresh(contact)
    log_event(
        db, actor=user_id, action="contacts.updated", target=f"contact:{contact.id}",
        before=before, after={"name": name, "phone_number": phone_number},
    )
    sync_contact_to_crm(db, account_id=account_id, contact_id=contact.id, name=name, phone_number=phone_number)
    return contact


def delete_contact(db: Session, *, account_id: str, user_id: str, contact_id: str) -> None:
    contact = get_contact(db, account_id, contact_id)
    before = {"name": contact.name, "phone_number": contact.phone_number}
    db.delete(contact)
    db.commit()
    log_event(db, actor=user_id, action="contacts.deleted", target=f"contact:{contact_id}", before=before)


def get_contact_history(db: Session, user: User, contact: Contact) -> list[ContactHistoryEntry]:
    """Calls, voicemails, and receptionist calls matched by phone number, not
    a stored relationship - a contact saved after the fact still shows prior
    history with the same number. Respects the same Member/assigned-number
    restriction as the main Calls page (list_account_calls) - a Member only
    sees history on numbers assigned to them, not the whole account's.
    """
    phone = contact.phone_number
    calls_query = db.query(CallRecord).filter(
        CallRecord.account_id == contact.account_id,
        sa.or_(CallRecord.from_number == phone, CallRecord.to_number == phone),
    )
    voicemails_query = db.query(Voicemail).filter(
        Voicemail.account_id == contact.account_id, Voicemail.from_number == phone,
    )
    receptionist_calls_query = db.query(ReceptionistCall).filter(
        ReceptionistCall.account_id == contact.account_id, ReceptionistCall.caller_number == phone,
    )

    ids = assigned_number_ids(db, user)
    if ids is not None:
        calls_query = calls_query.filter(CallRecord.phone_number_id.in_(ids))
        voicemails_query = voicemails_query.filter(Voicemail.phone_number_id.in_(ids))
        receptionist_calls_query = receptionist_calls_query.filter(ReceptionistCall.phone_number_id.in_(ids))

    entries = [
        ContactHistoryEntry(
            type="call", id=c.id, direction=c.direction.value, status=c.status, duration=c.duration,
            recording_url=c.recording_url, created_at=c.created_at,
        )
        for c in calls_query.all()
    ] + [
        ContactHistoryEntry(
            type="voicemail", id=v.id, duration=v.duration, recording_url=v.recording_url,
            created_at=v.created_at,
        )
        for v in voicemails_query.all()
    ] + [
        ContactHistoryEntry(
            type="receptionist_call", id=r.id, summary=r.summary, status=r.urgency.value if r.urgency else None,
            created_at=r.created_at,
        )
        for r in receptionist_calls_query.all()
    ]

    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries
