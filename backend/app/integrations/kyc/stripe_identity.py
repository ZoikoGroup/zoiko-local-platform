"""
Provider Gateway for Stripe Identity (kyc category). Only file allowed to
import the `stripe` SDK directly - real KYC verification for
compliance_cases, replacing the pure-manual-review workflow with a real
provider decision (staff can still override).
"""

import stripe

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover
from app.observability.service import trace_provider_call

_breaker = CircuitBreaker("kyc")


def circuit_state() -> str:
    return _breaker.state.value


class KYCError(Exception):
    """Raised instead of letting a stripe SDK exception escape this module."""


# Imported after KYCError is defined - _secondary_stub imports it back from
# this module, which would otherwise be a circular import.
from app.integrations.kyc import _secondary_stub as secondary  # noqa: E402


def health_check() -> dict:
    """Real reachability check - retrieves the account resource, the
    cheapest authenticated call Stripe offers."""
    if not settings.stripe_secret_key:
        return {"configured": False, "ok": False, "detail": None}
    try:
        stripe.Account.retrieve(api_key=settings.stripe_secret_key)
        return {"configured": True, "ok": True, "detail": None}
    except stripe.error.StripeError as e:
        return {"configured": True, "ok": False, "detail": str(e)}


def create_verification_session(reference_id: str) -> dict:
    """Starts a verification for one compliance case. reference_id is our
    own compliance_case.id, stored in metadata so the webhook can
    correlate without an extra lookup table. Returns {id, url} - url is
    the short-lived Stripe-hosted link the customer opens to complete
    document capture."""
    if not settings.stripe_secret_key:
        raise KYCError("Stripe secret key is not configured")

    def _primary() -> dict:
        try:
            with trace_provider_call("stripe_identity", "create_verification_session"):
                session = stripe.identity.VerificationSession.create(
                    api_key=settings.stripe_secret_key,
                    type="document",
                    metadata={"case_id": reference_id},
                )
        except stripe.error.StripeError as e:
            raise KYCError(f"Stripe create verification session failed: {e}") from e

        return {"id": session.id, "url": session.url}

    secondary_fn = (
        (lambda: secondary.create_verification_session(reference_id)) if settings.kyc_failover_enabled else None
    )
    return with_failover(_breaker, _primary, secondary_fn, KYCError)


def construct_webhook_event(payload: bytes, signature_header: str) -> stripe.Event:
    """Verifies the Stripe-Signature header and returns the parsed event -
    the official SDK handles the HMAC check, so we don't hand-roll it."""
    if not settings.stripe_identity_webhook_secret:
        raise KYCError("Stripe Identity webhook secret is not configured")

    try:
        return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_identity_webhook_secret)
    except (stripe.error.SignatureVerificationError, ValueError) as e:
        raise KYCError(f"Invalid Stripe webhook signature: {e}") from e
