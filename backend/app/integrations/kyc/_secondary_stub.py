"""Stand-in for a second KYC/identity-verification vendor (e.g. Persona,
Onfido) behind kyc_failover_enabled. No real second-vendor account exists
yet - raises a clearly labeled error instead of silently no-opping.
"""

from app.integrations.kyc.stripe_identity import KYCError

_NOT_CONFIGURED = (
    "secondary KYC provider not configured - set KYC_SECONDARY_* credentials "
    "once a second vendor account exists"
)


def create_verification_session(reference_id: str) -> dict:
    raise KYCError(_NOT_CONFIGURED)
