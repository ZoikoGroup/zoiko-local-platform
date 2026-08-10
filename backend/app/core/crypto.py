from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EncryptionNotConfiguredError(Exception):
    """Raised when TOKEN_ENCRYPTION_KEY isn't set but a real secret (e.g. an
    OAuth token) needed to be encrypted or decrypted."""


class DecryptionError(Exception):
    """Raised when stored ciphertext doesn't decrypt under the configured
    key - a wrong/rotated key, or corrupted data."""


def _fernet() -> Fernet:
    if not settings.token_encryption_key:
        raise EncryptionNotConfiguredError("TOKEN_ENCRYPTION_KEY is not configured")
    return Fernet(settings.token_encryption_key.encode())


def encrypt_secret(plaintext: str) -> str:
    """Encrypts a value (e.g. an OAuth access/refresh token) before it's
    stored in the database - CrmConnection's HubSpot tokens are the first
    real use of this. Never store plaintext long-lived credentials."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise DecryptionError("Stored value could not be decrypted with the configured key") from e
