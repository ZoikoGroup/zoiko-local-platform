import hashlib
import hmac
import json
import logging
import secrets
import threading
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.database import SessionLocal
from app.notifications.service import notify_webhook_endpoint_added
from app.webhooks.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint

logger = logging.getLogger("zoiko.webhooks")

_DELIVERY_TIMEOUT_SECONDS = 5.0
_MAX_ENDPOINTS_PER_ACCOUNT = 10


class WebhookEndpointAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the given endpoint."""


class WebhookEndpointLimitExceededError(Exception):
    """Raised when an account already has the max number of registered endpoints."""


class InvalidWebhookUrlError(Exception):
    """Raised for a URL that isn't a plausible public HTTPS receiver."""


def _assert_valid_url(url: str) -> None:
    if not url.startswith("https://"):
        raise InvalidWebhookUrlError("Webhook URL must start with https://")
    if len(url) > 2048:
        raise InvalidWebhookUrlError("Webhook URL is too long")


def create_endpoint(db: Session, *, account_id: str, url: str, description: str | None, actor: str) -> tuple[WebhookEndpoint, str]:
    """Returns (endpoint, secret) - the secret is generated here and
    returned to the caller exactly once (see routes.py); it is never
    re-exposed by any read endpoint afterward, same posture as an API key."""
    _assert_valid_url(url)

    existing_count = db.query(WebhookEndpoint).filter(WebhookEndpoint.account_id == account_id).count()
    if existing_count >= _MAX_ENDPOINTS_PER_ACCOUNT:
        raise WebhookEndpointLimitExceededError(f"Accounts may register up to {_MAX_ENDPOINTS_PER_ACCOUNT} webhook endpoints")

    secret = secrets.token_hex(32)
    endpoint = WebhookEndpoint(account_id=account_id, url=url, description=description, secret=secret)
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)

    log_event(
        db, actor=actor, action="webhook_endpoint.created", target=f"webhook_endpoint:{endpoint.id}",
        after={"url": url, "description": description},
    )

    from app.numbering.identity.models import User, UserRole

    actor_user = db.query(User).filter(User.id == actor).first()
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_webhook_endpoint_added(
            db, account_id=account_id, account_email=owner.email, url=url,
            actor_display_name=actor_user.email if actor_user else "your account",
        )

    return endpoint, secret


def list_endpoints(db: Session, account_id: str) -> list[WebhookEndpoint]:
    return (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.account_id == account_id)
        .order_by(WebhookEndpoint.created_at.desc())
        .all()
    )


def delete_endpoint(db: Session, *, account_id: str, endpoint_id: str, actor: str) -> None:
    endpoint = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == endpoint_id).first()
    if endpoint is None or endpoint.account_id != account_id:
        raise WebhookEndpointAuthorizationError(f"{endpoint_id} is not a webhook endpoint on your account")

    db.delete(endpoint)
    db.commit()
    log_event(db, actor=actor, action="webhook_endpoint.deleted", target=f"webhook_endpoint:{endpoint_id}")


def list_deliveries(db: Session, *, account_id: str, endpoint_id: str | None = None, limit: int = 100) -> list[WebhookDelivery]:
    query = db.query(WebhookDelivery).join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id).filter(
        WebhookEndpoint.account_id == account_id
    )
    if endpoint_id:
        query = query.filter(WebhookDelivery.endpoint_id == endpoint_id)
    return query.order_by(WebhookDelivery.created_at.desc()).limit(limit).all()


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def dispatch_webhook_event(db: Session, *, account_id: str, event_type: str, payload: dict) -> None:
    """The webhook counterpart to notifications' send_notification - called
    from the exact same dispatch point (see app.notifications.service.
    send_notification) so every event that's already notification-worthy
    also reaches any webhook endpoints the account has registered, with no
    additional call-site changes anywhere else in the codebase. Best-effort:
    a delivery failure here must never fail the request that triggered the
    event (matches send_sms_notification's SMS-is-best-effort posture).

    The actual HTTP delivery runs on a background thread (_deliver), not
    inline here - send_notification is called from ~40 call sites all over
    the app, none of which have a FastAPI BackgroundTasks handle this deep
    in business logic, so a background thread is the only way to keep this
    off the request path without threading that object through every one
    of them. Confirmed live as a real bug before this fix: a slow/
    unreachable customer endpoint (an account can register up to
    _MAX_ENDPOINTS_PER_ACCOUNT of them) made every notification-triggering
    action anywhere in the app - placing a call, sending a message, a
    compliance case changing status, etc. - block the original request for
    up to _DELIVERY_TIMEOUT_SECONDS per endpoint, exactly the class of bug
    app.integrations.eventbus.kafka.publish's own docstring already
    documents fixing for Kafka event publishing - webhook delivery just
    never got the same treatment."""
    endpoints = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.account_id == account_id, WebhookEndpoint.is_active.is_(True))
        .all()
    )
    if not endpoints:
        return

    body_dict = {
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    # Detached plain values, not the ORM objects themselves - endpoint is
    # bound to this request's Session, which this request will close once
    # it returns; the background thread below runs independently of that
    # lifecycle and needs its own Session anyway (SQLAlchemy Sessions
    # aren't thread-safe to share).
    endpoint_data = [(e.id, e.url, e.secret) for e in endpoints]
    _spawn_delivery(endpoint_data, event_type, body_dict)


def _spawn_delivery(endpoint_data: list[tuple[str, str, str]], event_type: str, body_dict: dict) -> None:
    """The only seam between dispatch_webhook_event and the real delivery
    work - exists so tests can monkeypatch this one function to run
    _deliver_to_endpoints inline, on the test's own db_session, instead of
    a real background thread with its own independent connection. That
    swap matters because db_session wraps each test in a single uncommitted
    transaction rolled back at teardown (see conftest.py) - a genuinely
    separate connection can never see a WebhookEndpoint row the test just
    created, and WebhookDelivery.endpoint_id is a real foreign key, so it
    would hit an actual constraint violation trying to insert against it.
    Production is unaffected: this default IS the real async behavior."""
    threading.Thread(
        target=_deliver_to_endpoints, args=(endpoint_data, event_type, body_dict), daemon=True,
    ).start()


def _deliver_to_endpoints(
    endpoint_data: list[tuple[str, str, str]], event_type: str, body_dict: dict, *, db: Session | None = None,
) -> None:
    """Runs off the request thread by default - opens its own DB session
    (see dispatch_webhook_event's docstring) so a slow customer endpoint's
    delivery attempt(s) never hold up, or share a Session with, the request
    that triggered them. The db override exists only for _spawn_delivery's
    test seam above - real callers never pass it."""
    body = json.dumps(body_dict).encode()
    owns_session = db is None
    if db is None:
        db = SessionLocal()
    try:
        for endpoint_id, url, secret in endpoint_data:
            signature = _sign(secret, body)
            delivery = WebhookDelivery(
                endpoint_id=endpoint_id, event_type=event_type, payload=body_dict, status=WebhookDeliveryStatus.FAILED,
            )
            try:
                response = httpx.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Zoiko-Event": event_type,
                        "X-Zoiko-Signature": f"sha256={signature}",
                    },
                    timeout=_DELIVERY_TIMEOUT_SECONDS,
                )
                delivery.response_status_code = response.status_code
                if response.is_success:
                    delivery.status = WebhookDeliveryStatus.DELIVERED
                else:
                    delivery.error = f"Endpoint returned HTTP {response.status_code}"
            except httpx.HTTPError as e:
                delivery.error = str(e)
                logger.warning("Webhook delivery to endpoint %s failed: %s", endpoint_id, e)

            db.add(delivery)
            db.commit()
    finally:
        if owns_session:
            db.close()
