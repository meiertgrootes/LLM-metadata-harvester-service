from cryptography.fernet import Fernet, InvalidToken

from llm_metadata_harvester_service.core.config import WEBHOOK_SECRET_KEY


def encrypt_webhook_secret(secret: str) -> str:
    if not WEBHOOK_SECRET_KEY:
        raise RuntimeError("WEBHOOK_SECRET_KEY is required for signed webhooks")
    return Fernet(WEBHOOK_SECRET_KEY.encode()).encrypt(secret.encode()).decode()


def decrypt_webhook_secret(secret: str) -> str:
    if not WEBHOOK_SECRET_KEY:
        raise RuntimeError("WEBHOOK_SECRET_KEY is required for signed webhooks")
    try:
        return Fernet(WEBHOOK_SECRET_KEY.encode()).decrypt(secret.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Webhook secret could not be decrypted") from exc
