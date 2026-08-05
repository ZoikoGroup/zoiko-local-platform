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
from app.observability.service import trace_provider_call


class StorageError(Exception):
    """Raised instead of letting a boto3/botocore exception escape this module."""


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
    try:
        with trace_provider_call("s3", "delete_object"):
            _client().delete_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e


def download_object(key: str) -> bytes:
    """Fetches an object's raw bytes directly from the bucket - used to hand
    a stored recording (e.g. a video call's egress output) to another
    provider (Groq transcription) rather than serving it to a browser, so a
    presigned URL isn't the right shape here."""
    try:
        with trace_provider_call("s3", "download_object"):
            response = _client().get_object(Bucket=settings.s3_bucket, Key=key)
            return response["Body"].read()
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """Recordings live in a private bucket (no public-read policy) — the
    permanent bucket URL 404s/403s in a browser. Callers must generate a
    fresh signed URL each time a recording is served rather than storing
    one, since it expires."""
    try:
        return _client().generate_presigned_url(
            "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=expires_in
        )
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e
