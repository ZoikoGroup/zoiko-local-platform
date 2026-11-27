from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d8e7f6a5b4"
down_revision: Union[str, None] = "f6a2d9c4b7e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REDUNDANT_UNIQUE_CONSTRAINTS = (
    ("assistant_destinations", "uq_assistant_destination_key", "destination_key"),
    ("assistant_intents", "uq_assistant_intent_id", "intent_id"),
    ("assistant_knowledge_sources", "uq_assistant_knowledge_slug", "slug"),
    ("assistant_sessions", "uq_assistant_session_token_hash", "session_token_hash"),
    ("caller_identities", "caller_identities_phone_number_id_key", "phone_number_id"),
    ("career_jobs", "career_jobs_slug_key", "slug"),
    ("plan_change_checkout_sessions", "uq_plan_change_checkout_sessions_stripe_session_id", "stripe_session_id"),
    ("platform_assistant_content", "uq_platform_assistant_content_key", "content_key"),
    ("platform_assistant_sessions", "uq_platform_assistant_session_token_hash", "session_token_hash"),
)

_NOT_NULL_TABLES = (
    "ai_receptionist_addon_checkout_sessions",
    "country_approval_records",
    "erasure_requests",
)


def _inspector():
    return sa.inspect(op.get_bind())


def _has_constraint(inspector, table: str, name: str) -> bool:
    return any(c["name"] == name for c in inspector.get_unique_constraints(table))


def _has_index(inspector, table: str, name: str) -> bool:
    return any(i["name"] == name for i in inspector.get_indexes(table))


def _index_is_unique(inspector, table: str, name: str) -> bool:
    return any(i["name"] == name and i["unique"] for i in inspector.get_indexes(table))


def _column_is_nullable(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column and c["nullable"] for c in inspector.get_columns(table))


def _ensure_not_null(table: str, column: str = "created_at") -> None:
    inspector = _inspector()
    if not _column_is_nullable(inspector, table, column):
        return
    bind = op.get_bind()
    bind.execute(sa.text(f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL"))
    op.alter_column(
        table,
        column,
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )


def _allow_null(table: str, column: str = "created_at") -> None:
    inspector = _inspector()
    if _column_is_nullable(inspector, table, column):
        return
    op.alter_column(
        table,
        column,
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def _drop_redundant_unique_constraints() -> None:
    for table, constraint_name, column in _REDUNDANT_UNIQUE_CONSTRAINTS:
        inspector = _inspector()
        if not _has_constraint(inspector, table, constraint_name):
            continue
        index_name = f"ix_{table}_{column}"
        if _has_index(inspector, table, index_name) and _index_is_unique(inspector, table, index_name):
            op.drop_constraint(constraint_name, table, type_="unique")


def _restore_redundant_unique_constraints() -> None:
    for table, constraint_name, column in _REDUNDANT_UNIQUE_CONSTRAINTS:
        inspector = _inspector()
        if _has_constraint(inspector, table, constraint_name):
            continue
        index_name = f"ix_{table}_{column}"
        if _has_index(inspector, table, index_name) and _index_is_unique(inspector, table, index_name):
            op.create_unique_constraint(constraint_name, table, [column])


def _reconcile_calling_rates() -> None:
    inspector = _inspector()
    if _has_index(inspector, "calling_rates", "ix_calling_rates_country_destination"):
        op.drop_index("ix_calling_rates_country_destination", table_name="calling_rates")

    inspector = _inspector()
    if not _has_index(inspector, "calling_rates", "ix_calling_rates_country"):
        op.create_index("ix_calling_rates_country", "calling_rates", ["country"])
    if not _has_index(inspector, "calling_rates", "ix_calling_rates_destination_country"):
        op.create_index("ix_calling_rates_destination_country", "calling_rates", ["destination_country"])
    if not _has_constraint(inspector, "calling_rates", "uq_calling_rate_country_destination"):
        op.create_unique_constraint(
            "uq_calling_rate_country_destination",
            "calling_rates",
            ["country", "destination_country"],
        )


def _restore_calling_rates() -> None:
    inspector = _inspector()
    if _has_constraint(inspector, "calling_rates", "uq_calling_rate_country_destination"):
        op.drop_constraint("uq_calling_rate_country_destination", "calling_rates", type_="unique")
    if _has_index(inspector, "calling_rates", "ix_calling_rates_country"):
        op.drop_index("ix_calling_rates_country", table_name="calling_rates")
    if _has_index(inspector, "calling_rates", "ix_calling_rates_destination_country"):
        op.drop_index("ix_calling_rates_destination_country", table_name="calling_rates")
    if not _has_index(inspector, "calling_rates", "ix_calling_rates_country_destination"):
        op.create_index(
            "ix_calling_rates_country_destination",
            "calling_rates",
            ["country", "destination_country"],
            unique=True,
        )


def upgrade() -> None:
    for table in _NOT_NULL_TABLES:
        _ensure_not_null(table)
    _drop_redundant_unique_constraints()
    _reconcile_calling_rates()


def downgrade() -> None:
    _restore_calling_rates()
    _restore_redundant_unique_constraints()
    for table in _NOT_NULL_TABLES:
        _allow_null(table)
