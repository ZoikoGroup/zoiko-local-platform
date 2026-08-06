import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.webhooks.models import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint

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
    event (matches send_sms_notification's SMS-is-best-effort posture)."""
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
    body = json.dumps(body_dict).encode()

    for endpoint in endpoints:
        signature = _sign(endpoint.secret, body)
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id, event_type=event_type, payload=body_dict, status=WebhookDeliveryStatus.FAILED,
        )
        try:
            response = httpx.post(
                endpoint.url,
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

        db.add(delivery)
        db.commit()
