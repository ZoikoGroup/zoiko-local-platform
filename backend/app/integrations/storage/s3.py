"""
Provider Gateway for S3-compatible object storage (storage category). Per
CLAUDE.md's Provider Gateway rule, this is the ONLY file allowed to import
`boto3` directly — everything else calls the functions below instead.

Works with real AWS S3 (leave settings.s3_endpoint empty) or any
S3-compatible provider (Backblaze B2, Cloudflare R2, MinIO, ...).
"""

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover
from app.observability.service import trace_provider_call

_breaker = CircuitBreaker("storage")


def circuit_state() -> str:
    return _breaker.state.value


class StorageError(Exception):
    """Raised instead of letting a boto3/botocore exception escape this module."""


def _is_provider_failure(e: Exception) -> bool:
    """Passed as with_failover's is_breaker_failure - _breaker is a single
    process-wide instance shared by every storage operation on the
    platform, so what counts as a "failure" here matters beyond just this
    one request. Every StorageError raised in this module wraps the
    original botocore exception via `from e`, so e.__cause__ is that
    original exception.

    A ClientError carries the real HTTP status S3 returned via
    `.response["ResponseMetadata"]["HTTPStatusCode"]` (e.g. NoSuchKey,
    AccessDenied, InvalidBucketName - all 4xx). A 4xx means the bucket
    understood and rejected THIS specific request - an expected,
    per-request outcome that says nothing about whether the storage
    provider itself is healthy. A BotoCoreError (e.g. EndpointConnectionError,
    ConnectTimeoutError) has no `.response` at all - a connection/timeout-
    level failure with nothing HTTP to inspect, which does count as a real
    provider-health signal. Only a 5xx (or no status at all) should trip
    the shared breaker - same conservative default as twilio.py's
    _is_provider_failure."""
    cause = getattr(e, "__cause__", None)
    response = getattr(cause, "response", None)
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode") if response else None
    return status is None or status >= 500


def _client():
    if not (settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key):
        raise StorageError(
            "Object storage is not configured — set S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY"
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint or None,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
    )


def health_check() -> dict:
    """Real reachability check - head_bucket, the cheapest authenticated
    call that confirms both credentials and bucket access."""
    if not (settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key):
        return {"configured": False, "ok": False, "detail": None}
    try:
        _client().head_bucket(Bucket=settings.s3_bucket)
        return {"configured": True, "ok": True, "detail": None}
    except (BotoCoreError, ClientError) as e:
        return {"configured": True, "ok": False, "detail": str(e)}


def upload_object(key: str, data: bytes, content_type: str) -> None:
    """Uploads raw bytes to the configured (private) bucket — used for
    documents a customer submits directly (e.g. compliance verification
    docs), as opposed to recordings, which providers write via their own
    egress/callback flow rather than us pushing bytes ourselves."""
    try:
        with trace_provider_call("s3", "upload_object"):
            _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e


def delete_object(key: str) -> None:
    """Deletes one object from the configured bucket — used to actually
    remove a recording's file once it's past its retention window, not just
    unlink it in our own database."""
    def _primary() -> None:
        try:
            with trace_provider_call("s3", "delete_object"):
                _client().delete_object(Bucket=settings.s3_bucket, Key=key)
        except (BotoCoreError, ClientError) as e:
            raise StorageError(str(e)) from e

    # No secondary_fn: real gap fix - objects are never replicated to the
    # secondary bucket by upload_object (it only ever writes to the primary),
    # and S3's delete-on-nonexistent-key is not an error. Failing over here
    # would let this "succeed" against an empty secondary bucket while the
    # real file survives untouched on the (temporarily unreachable) primary
    # - a caller like retention/service.py would then wrongly mark the
    # recording as purged. Same posture as buy_number's deliberate
    # secondary_fn=None in twilio.py for an operation the secondary can't
    # actually fulfill correctly.
    with_failover(_breaker, _primary, None, StorageError, _is_provider_failure)


def download_object(key: str) -> bytes:
    """Fetches an object's raw bytes directly from the bucket - used to hand
    a stored recording (e.g. a video call's egress output) to another
    provider (Groq transcription) rather than serving it to a browser, so a
    presigned URL isn't the right shape here."""
    def _primary() -> bytes:
        try:
            with trace_provider_call("s3", "download_object"):
                response = _client().get_object(Bucket=settings.s3_bucket, Key=key)
                return response["Body"].read()
        except (BotoCoreError, ClientError) as e:
            raise StorageError(str(e)) from e

    # No secondary_fn: objects are never replicated to the secondary bucket
    # by upload_object - falling over here would silently return whatever
    # (nothing, or stale data) happens to exist under this key in the
    # secondary bucket instead of a clear error that the primary is down.
    # Same rationale as delete_object's own secondary_fn=None above.
    return with_failover(_breaker, _primary, None, StorageError, _is_provider_failure)


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Recordings live in a private bucket (no public-read policy) — the
    permanent bucket URL 404s/403s in a browser. Callers must generate a
    fresh signed URL each time a recording is served rather than storing
    one, since it expires."""
    def _primary() -> str:
        try:
            return _client().generate_presigned_url(
                "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=expires_in
            )
        except (BotoCoreError, ClientError) as e:
            raise StorageError(str(e)) from e

    # No secondary_fn: objects are never replicated to the secondary bucket
    # by upload_object - a presigned URL for the secondary bucket would
    # point a browser/client at a key that was never written there. Same
    # rationale as delete_object's own secondary_fn=None above.
    return with_failover(_breaker, _primary, None, StorageError, _is_provider_failure)
