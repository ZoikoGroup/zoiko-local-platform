"""
Provider Gateway for Stripe Payments (billing category) — real, live-tested
against Stripe's test-mode sandbox (no real money moves). Only file allowed
to import the `stripe` SDK for payment collection - a separate concern from
app.integrations.kyc.stripe_identity (same vendor, different Stripe account
scope/key, different product: Checkout/payments here, Identity verification
there). Per docs/pci-scope-assessment.md's locked recommendation, this only
ever creates a Stripe-hosted Checkout Session - card data is entered on
Stripe's own page and never touches this backend, keeping PCI scope minimal.

Distinct from app.integrations.billing.zoikonex: that module is an
explicitly-disclosed MOCK standing in for a ZoikoNex connection that doesn't
exist yet. This module is a real integration against a real (test-mode)
Stripe account - the first real payment collection anywhere in this
codebase. It does not replace ZoikoNex as the eventual money-truth system;
it's what makes number-purchase actually cost something today, in the
absence of that connection.
"""

import stripe

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover
from app.observability.service import trace_provider_call

_breaker = CircuitBreaker("stripe_payments")


def circuit_state() -> str:
    return _breaker.state.value


class PaymentError(Exception):
    """Raised instead of letting a stripe SDK exception escape this module."""


def health_check() -> dict:
    """Real reachability check. Deliberately NOT stripe.Account.retrieve
    (the pattern app.integrations.kyc.stripe_identity's health_check uses)
    - that call needs account-read permissions this module's restricted
    key doesn't have and shouldn't need (least-privilege: "One-time
    payments" scope only). Listing Checkout Sessions is the cheapest call
    actually within that scope."""
    if not settings.stripe_payments_secret_key:
        return {"configured": False, "ok": False, "detail": None}
    try:
        stripe.checkout.Session.list(api_key=settings.stripe_payments_secret_key, limit=1)
        return {"configured": True, "ok": True, "detail": None}
    except stripe.error.StripeError as e:
        return {"configured": True, "ok": False, "detail": str(e)}


def create_checkout_session(
    *, e164: str, amount_cents: int, currency: str, success_url: str, cancel_url: str, metadata: dict
) -> dict:
    """Creates a Stripe-hosted Checkout Session for a one-time number-
    purchase payment. Returns {id, url} - url is the Stripe-hosted page the
    customer is redirected to; id is stored nowhere by this module (the
    caller correlates completion back to our own record via `metadata`,
    e.g. e164 + account_id), since Stripe's own webhook event already
    carries the full session object back."""
    if not settings.stripe_payments_secret_key:
        raise PaymentError("Stripe payments secret key is not configured")

    def _primary() -> dict:
        try:
            with trace_provider_call("stripe_payments", "create_checkout_session"):
                session = stripe.checkout.Session.create(
                    api_key=settings.stripe_payments_secret_key,
                    mode="payment",
                    line_items=[
                        {
                            "price_data": {
                                "currency": currency,
                                "unit_amount": amount_cents,
                                "product_data": {"name": f"Zoiko Local number: {e164}"},
                            },
                            "quantity": 1,
                        }
                    ],
                    success_url=success_url,
                    cancel_url=cancel_url,
                    metadata=metadata,
                )
        except stripe.error.StripeError as e:
            raise PaymentError(f"Stripe create checkout session failed: {e}") from e
        return {"id": session.id, "url": session.url}

    return with_failover(_breaker, _primary, None, PaymentError)


def refund_payment(payment_intent_id: str) -> dict:
    """Issues a full refund against a completed Checkout Session's
    PaymentIntent - called when a payment succeeds but number fulfillment
    then fails for a genuine reason (quota/billing/disclosure/telecom),
    see app.numbering.numbers.service.complete_number_purchase_from_checkout.
    Confirmed the restricted "One-time payments" key scope includes refund
    creation (verified against the real test account, not assumed)."""
    if not settings.stripe_payments_secret_key:
        raise PaymentError("Stripe payments secret key is not configured")

    def _primary() -> dict:
        try:
            with trace_provider_call("stripe_payments", "refund_payment"):
                refund = stripe.Refund.create(
                    api_key=settings.stripe_payments_secret_key, payment_intent=payment_intent_id,
                )
        except stripe.error.StripeError as e:
            raise PaymentError(f"Stripe refund failed: {e}") from e
        # amount/currency: the real refunded figure per Stripe's own
        # response - callers need this instead of assuming a flat constant
        # now that checkout amounts vary by NumberRate (country/number_type
        # and inclusion-threshold surcharges), see
        # app.numbering.numbers.service._refund_and_notify_failed_purchase.
        return {"id": refund.id, "status": refund.status, "amount": refund.amount, "currency": refund.currency}

    return with_failover(_breaker, _primary, None, PaymentError)


def construct_webhook_event(payload: bytes, signature_header: str) -> stripe.Event:
    """Verifies the Stripe-Signature header and returns the parsed event -
    the official SDK handles the HMAC check, same pattern as
    app.integrations.kyc.stripe_identity.construct_webhook_event."""
    if not settings.stripe_payments_webhook_secret:
        raise PaymentError("Stripe payments webhook secret is not configured")

    try:
        return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_payments_webhook_secret)
    except (stripe.error.SignatureVerificationError, ValueError) as e:
        raise PaymentError(f"Invalid Stripe webhook signature: {e}") from e
