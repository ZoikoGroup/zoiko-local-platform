"""Seed a demo account for local development.

Run with: python -m app.seed
"""

from app.core.database import Base, SessionLocal, engine
from app.numbering.identity import service


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        try:
            user = service.create_account_with_owner(
                db,
                account_name="Demo Account",
                account_type="individual",
                email="demo@zoikolocal.test",
                password="demo12345",
            )
            print(f"Seeded demo user: {user.email} (account_id={user.account_id})")
        except ValueError as e:
            print(f"Skipped seeding: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
