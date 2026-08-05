"""Stand-in for a second S3-compatible provider (e.g. a Backblaze B2 or R2
bucket in a different region/account) behind storage_failover_enabled. No
real second-vendor bucket exists yet - every function raises a clearly
labeled error rather than silently no-opping. Swap these bodies for a real
boto3 client pointed at the secondary bucket once one exists - callers
never change, since s3.py dispatches to this module by function name only.
"""

from app.integrations.storage.s3 import StorageError

_NOT_CONFIGURED = (
    "secondary object storage provider not configured - set "
    "STORAGE_SECONDARY_* credentials once a second bucket/vendor exists"
)


def delete_object(key: str) -> None:
    raise StorageError(_NOT_CONFIGURED)


def download_object(key: str) -> bytes:
    raise StorageError(_NOT_CONFIGURED)


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    raise StorageError(_NOT_CONFIGURED)
