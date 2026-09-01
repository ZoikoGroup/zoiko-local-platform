"""add guardrail_rules table

Revision ID: 0251c73f5e21
Revises: 455d7ada581c
Create Date: 2026-09-01 10:10:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0251c73f5e21'
down_revision: Union[str, None] = '455d7ada581c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AI Receptionist disallowed-commitment patterns (app.intelligence.
    # guardrails.check_for_disallowed_commitments), moved off hardcoded
    # Python regex constants (_PRICING_PATTERNS/_LEGAL_PATTERNS/
    # _MEDICAL_PATTERNS) onto a data table per the project rule "Compliance
    # rules are stored as data (a table), never hardcoded if-statements."
    # Global/platform-wide, not scoped to an account - same scope as
    # compliance_rules.
    op.create_table(
        'guardrail_rules',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_guardrail_rules_category'), 'guardrail_rules', ['category'], unique=False)

    guardrail_rules_table = sa.table(
        'guardrail_rules',
        sa.column('id', sa.String),
        sa.column('category', sa.String),
        sa.column('pattern', sa.Text),
        sa.column('is_active', sa.Boolean),
    )
    # Exactly the patterns the hardcoded constants held, one row per
    # pattern - no coverage lost in the move from code to data.
    seed_patterns = [
        ('pricing_commitment', r"\$\s?\d"),
        ('pricing_commitment', r"\b\d+(\.\d+)?\s?(dollars|usd|percent|% ?(off|discount))\b"),
        ('pricing_commitment', r"\b(guarantee|guaranteed|promise[ds]?)\b[^.]{0,40}\b(price|cost|rate|discount|refund|quote)\b"),
        ('pricing_commitment', r"\b(price|cost|rate|quote) (is|will be|of) \b"),
        ('pricing_commitment', r"\b(free|no charge|complimentary)\b[^.]{0,40}\b(service|repair|installation|replacement)\b"),
        ('legal_advice', r"\blegal advice\b"),
        ('legal_advice', r"\b(legally (binding|obligated|required|entitled))\b"),
        ('legal_advice', r"\byou (will|can|should) (sue|win (your|the) case|be liable)\b"),
        ('legal_advice', r"\bwe (accept|admit) (liability|fault)\b"),
        ('medical_advice', r"\bmedical advice\b"),
        ('medical_advice', r"\byou (have|are experiencing) (a|an) [\w\s]{0,30}(condition|disease|infection|disorder)\b"),
        ('medical_advice', r"\b(diagnos(e|is|ed|ing)|prescri(be|bed|ption))\b"),
    ]
    op.bulk_insert(
        guardrail_rules_table,
        [
            {'id': str(uuid.uuid4()), 'category': category, 'pattern': pattern, 'is_active': True}
            for category, pattern in seed_patterns
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_guardrail_rules_category'), table_name='guardrail_rules')
    op.drop_table('guardrail_rules')
