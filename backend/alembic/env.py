import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.apikeys import models as apikeys_models  # noqa: F401
from app.audit import models as audit_models  # noqa: F401
from app.billing import models as billing_models  # noqa: F401
from app.compliance import models as compliance_models  # noqa: F401
from app.consent import models as consent_models  # noqa: F401
from app.contacts import models as contacts_models  # noqa: F401
from app.crm import models as crm_models  # noqa: F401
from app.core.config import settings
from app.core.database import Base
from app.events import models as events_models  # noqa: F401
from app.intelligence import models as intelligence_models  # noqa: F401
from app.media import models as media_models  # noqa: F401
from app.messaging import models as messaging_models  # noqa: F401
from app.numbering.identity import models as identity_models  # noqa: F401
from app.numbering.numbers import models as numbers_models  # noqa: F401
from app.notifications import models as notifications_models  # noqa: F401
from app.observability import models as observability_models  # noqa: F401
from app.ops import models as ops_models  # noqa: F401
from app.porting import models as porting_models  # noqa: F401
from app.queues import models as queues_models  # noqa: F401
from app.retention import models as retention_models  # noqa: F401
from app.risk import models as risk_models  # noqa: F401
from app.routing import models as routing_models  # noqa: F401
from app.staff import models as staff_models  # noqa: F401
from app.usage import models as usage_models  # noqa: F401
from app.webhooks import models as webhooks_models  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transaction_per_migration=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Real gap fix: without this, `alembic upgrade head` wraps the
        # ENTIRE chain (current revision -> head) in one transaction, not
        # one per migration - confirmed live, replaying this repo's full
        # migration chain from an empty database in one continuous run
        # fails with Postgres's "unsafe use of new value ... New enum
        # values must be committed before they can be used" the moment a
        # later migration uses an enum value an earlier migration in the
        # SAME run just added (e.g. c4a891fe6d27 using catalog_entry_
        # status_enum's ACTIVE value). This never surfaced against the
        # real Neon database because its schema was built incrementally
        # across many separate `alembic upgrade head` invocations over
        # many working sessions - each invocation's transaction committed
        # independently, so an enum value added in an earlier SESSION was
        # already durable by the time a later session used it. Any
        # genuinely fresh environment (new deploy, CI, disaster-recovery
        # restore) running the full chain in one shot would hit this
        # every time. transaction_per_migration=True commits after each
        # individual migration instead - standard Alembic practice for
        # exactly this class of Postgres limitation, and also closer to
        # what actually happened historically than one giant transaction.
        context.configure(
            connection=connection, target_metadata=target_metadata,
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
