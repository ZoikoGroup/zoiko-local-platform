from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

# pool_pre_ping avoids hard failures at import time if Postgres isn't up yet.
# pool_recycle discards a pooled connection once it's older than this many
# seconds instead of waiting to hit "server closed the connection
# unexpectedly" mid-query - Neon's pooler enforces its own max connection
# lifetime well under SQLAlchemy's default (unbounded), which is what a long
# test run was hitting.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=180)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
