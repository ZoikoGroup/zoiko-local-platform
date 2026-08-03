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


def delete_object(key: str) -> None:
    """Deletes one object from the configured bucket — used to actually
    remove a recording's file once it's past its retention window, not just
    unlink it in our own database."""
    try:
        _client().delete_object(Bucket=settings.s3_bucket, Key=key)
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
