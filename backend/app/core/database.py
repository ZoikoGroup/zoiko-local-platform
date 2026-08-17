from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

# pool_pre_ping avoids hard failures at import time if Postgres isn't up yet.
# pool_recycle discards a pooled connection once it's older than this many
# seconds instead of waiting to hit "server closed the connection
# unexpectedly" mid-query - Neon's pooler enforces its own max connection
# lifetime well under SQLAlchemy's default (unbounded), which is what a long
# test run was hitting.
#
# connect_args' connect_timeout bounds how long a brand-new TCP connection
# attempt (the one case pool_recycle/pool_pre_ping can't help with - there's
# no existing connection yet to recycle or ping) is allowed to hang before
# psycopg2 gives up and raises OperationalError. Without it, psycopg2 falls
# back to the OS's own TCP connect timeout, which on a connection that's
# gone silently unreachable (no RST, just no response) can be minutes, not
# seconds - confirmed live: a full backend pytest run against this app's
# real Neon database hit this exact case repeatedly, hanging for 5-15+
# minutes on a single fresh test session's first connection instead of
# failing fast into the request-level 503 database_unavailable_handler
# already exists to return for exactly this scenario (see app/main.py).
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=180,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    connect_args={"connect_timeout": 10},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
