"""add verify_url placeholder to auth.verify_email_address template

Revision ID: 3aba8bcd72b6
Revises: 2807e83dc1ba
Create Date: 2026-08-31 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3aba8bcd72b6'
down_revision: Union[str, None] = '2807e83dc1ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_BODY = (
    'Verify your email address\n\nHello {user_display_name}, confirm this email address for your Zoiko Local '
    'account. The link expires in {link_expiry_duration} and can be used once. Zoiko Local will never ask for '
    'your password, authentication code, or payment details by email.\n\nNext: Verify Email Address.'
)
_NEW_BODY = _OLD_BODY + '\n\n{verify_url}'


def upgrade() -> None:
    # Real gap: this template was seeded from the doc's literal copy text
    # with no functional link placeholder at all - unlike auth.password_
    # reset and 5 other keys, which the canonical-estate migration
    # (8f2a1c9d4e6b) already fixed by appending "{reset_url}" etc. to the
    # body. Without this, notify_email_verification_requested's verify_url
    # context value would render nowhere in the sent email - a customer
    # would have no way to actually click through and verify.
    op.execute(
        sa.text("UPDATE notification_templates SET body_template=:body WHERE key='auth.verify_email_address'")
        .bindparams(body=_NEW_BODY)
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE notification_templates SET body_template=:body WHERE key='auth.verify_email_address'")
        .bindparams(body=_OLD_BODY)
    )
