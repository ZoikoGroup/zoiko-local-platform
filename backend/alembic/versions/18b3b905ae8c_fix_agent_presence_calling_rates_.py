"""fix agent_presence/calling_rates/status_subscriptions constraint drift

Revision ID: 18b3b905ae8c
Revises: afbc03ad6710
Create Date: 2026-08-13 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18b3b905ae8c'
down_revision: Union[str, None] = 'afbc03ad6710'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Found running the full chain against a genuinely fresh database (this
# project's local docker-compose Postgres) - `alembic check` failed with
# real drift that several earlier migrations' own comments had already
# flagged and deliberately left unfixed ("pre-existing, unrelated drift...
# not touched here" - see 6f9bce3448b8/155a3edc4305/ce2bebedfe43).
#
# agent_presence.user_id and calling_rates/status_subscriptions' unique
# columns all declare `unique=True, index=True` in their models - SQLAlchemy
# compiles that to ONE unique index, not a separate named UniqueConstraint
# AND a separate plain index. The migrations that created these tables
# added both (redundant, and for calling_rates/status_subscriptions the
# separate index was left non-unique) - collapsing each pair to the single
# unique index the models actually expect.
#
# Every operation below is guarded with an inspector check rather than
# called unconditionally: confirmed live that the long-lived Neon dev
# database and a genuinely fresh chain replay disagree on which of these
# already exist - Neon's calling_rates, for instance, already has the
# correct final shape (no redundant constraint, created_at already NOT
# NULL) via a different real history, while a fresh replay does not. An
# unconditional drop_constraint failed outright on Neon with "constraint
# ... does not exist" - guarding makes this correct on both.


def _has_constraint(inspector, table: str, name: str) -> bool:
    return any(c["name"] == name for c in inspector.get_unique_constraints(table))


def _has_index(inspector, table: str, name: str) -> bool:
    return any(i["name"] == name for i in inspector.get_indexes(table))


def _index_is_unique(inspector, table: str, name: str) -> bool:
    return any(i["name"] == name and i["unique"] for i in inspector.get_indexes(table))


def _column_is_nullable(inspector, table: str, name: str) -> bool:
    return any(c["name"] == name and c["nullable"] for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_constraint(inspector, 'agent_presence', 'uq_agent_presence_user'):
        op.drop_constraint('uq_agent_presence_user', 'agent_presence', type_='unique')

    if _has_constraint(inspector, 'calling_rates', 'calling_rates_country_key'):
        op.drop_constraint('calling_rates_country_key', 'calling_rates', type_='unique')
    if _has_index(inspector, 'calling_rates', 'ix_calling_rates_country') and not _index_is_unique(
        inspector, 'calling_rates', 'ix_calling_rates_country'
    ):
        op.drop_index('ix_calling_rates_country', table_name='calling_rates')
        op.create_index('ix_calling_rates_country', 'calling_rates', ['country'], unique=True)
    if _column_is_nullable(inspector, 'calling_rates', 'created_at'):
        op.alter_column('calling_rates', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=False)

    if _has_constraint(inspector, 'status_subscriptions', 'status_subscriptions_account_id_key'):
        op.drop_constraint('status_subscriptions_account_id_key', 'status_subscriptions', type_='unique')
    if _has_index(inspector, 'status_subscriptions', 'ix_status_subscriptions_account_id') and not _index_is_unique(
        inspector, 'status_subscriptions', 'ix_status_subscriptions_account_id'
    ):
        op.drop_index('ix_status_subscriptions_account_id', table_name='status_subscriptions')
        op.create_index('ix_status_subscriptions_account_id', 'status_subscriptions', ['account_id'], unique=True)


def downgrade() -> None:
    # Best-effort reversal to the redundant-but-harmless original shape -
    # not guarded, since downgrade is only ever run right after this
    # migration's own upgrade in practice (same posture as every other
    # migration in this codebase; none guard downgrade()).
    op.drop_index('ix_status_subscriptions_account_id', table_name='status_subscriptions')
    op.create_index('ix_status_subscriptions_account_id', 'status_subscriptions', ['account_id'], unique=False)
    op.create_unique_constraint('status_subscriptions_account_id_key', 'status_subscriptions', ['account_id'])

    op.alter_column('calling_rates', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.drop_index('ix_calling_rates_country', table_name='calling_rates')
    op.create_index('ix_calling_rates_country', 'calling_rates', ['country'], unique=False)
    op.create_unique_constraint('calling_rates_country_key', 'calling_rates', ['country'])

    op.create_unique_constraint('uq_agent_presence_user', 'agent_presence', ['user_id'])
