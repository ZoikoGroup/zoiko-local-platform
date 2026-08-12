"""add zoikonex id-mapping columns to plans and subscriptions

Revision ID: 0fd38b72ef14
Revises: e1f3a9c7b2d4
Create Date: 2026-08-11 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0fd38b72ef14'
down_revision: Union[str, None] = 'e1f3a9c7b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZoikoNex's product-catalogue-commercial is the real price authority -
    # a Plan isn't sellable there until registered as a Product+Offer+
    # PriceRule (app.integrations.billing.zoikonex.register_plan_in_catalog).
    # NULL until that one-time registration runs for this plan_code.
    op.add_column('plans', sa.Column('zoikonex_product_id', sa.String(length=100), nullable=True))
    op.add_column('plans', sa.Column('zoikonex_offer_id', sa.String(length=100), nullable=True))
    op.add_column('plans', sa.Column('zoikonex_price_rule_id', sa.String(length=100), nullable=True))

    # ZoikoNex's own Party -> Customer -> Account chain (customer-account
    # service), created once per Zoiko Local account the first time its
    # subscription syncs. zoikonex_pii_token is a placeholder UUID standing
    # in for a real vaulted PII token - see Subscription's docstring.
    op.add_column('subscriptions', sa.Column('zoikonex_party_id', sa.String(length=100), nullable=True))
    op.add_column('subscriptions', sa.Column('zoikonex_customer_id', sa.String(length=100), nullable=True))
    op.add_column('subscriptions', sa.Column('zoikonex_account_id', sa.String(length=100), nullable=True))
    op.add_column('subscriptions', sa.Column('zoikonex_pii_token', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('subscriptions', 'zoikonex_pii_token')
    op.drop_column('subscriptions', 'zoikonex_account_id')
    op.drop_column('subscriptions', 'zoikonex_customer_id')
    op.drop_column('subscriptions', 'zoikonex_party_id')
    op.drop_column('plans', 'zoikonex_price_rule_id')
    op.drop_column('plans', 'zoikonex_offer_id')
    op.drop_column('plans', 'zoikonex_product_id')
