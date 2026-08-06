"""Secondary object storage provider (any second S3-compatible bucket -
Backblaze B2, a different-region Cloudflare R2 bucket, ...) behind
storage_failover_enabled. Real boto3 calls against a real bucket, not a
mock - but NOT tested against a live account, since no real secondary
bucket exists yet. Wire STORAGE_SECONDARY_* credentials in .env and flip
STORAGE_FAILOVER_ENABLED=true to activate. Callers in s3.py never change,
since it dispatches to this module by function name only.

Reuses boto3 exactly like the primary (s3.py) - S3-compatible storage is
the one Provider Gateway category where primary and secondary genuinely
share an SDK and request shape, only the endpoint/credentials/bucket differ.
"""

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.integrations.storage.s3 import StorageError


def _client():
    if not (
        settings.storage_secondary_bucket
        and settings.storage_secondary_access_key_id
        and settings.storage_secondary_secret_access_key
    ):
        raise StorageError(
            "Secondary object storage provider is not configured - set STORAGE_SECONDARY_BUCKET, "
            "STORAGE_SECONDARY_ACCESS_KEY_ID and STORAGE_SECONDARY_SECRET_ACCESS_KEY"
        )
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_secondary_endpoint or None,
        aws_access_key_id=settings.storage_secondary_access_key_id,
        aws_secret_access_key=settings.storage_secondary_secret_access_key,
        region_name=settings.storage_secondary_region,
    )


def delete_object(key: str) -> None:
    try:
        _client().delete_object(Bucket=settings.storage_secondary_bucket, Key=key)
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e


def download_object(key: str) -> bytes:
    try:
        response = _client().get_object(Bucket=settings.storage_secondary_bucket, Key=key)
        return response["Body"].read()
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    try:
        return _client().generate_presigned_url(
            "get_object", Params={"Bucket": settings.storage_secondary_bucket, "Key": key}, ExpiresIn=expires_in
        )
    except (BotoCoreError, ClientError) as e:
        raise StorageError(str(e)) from e
