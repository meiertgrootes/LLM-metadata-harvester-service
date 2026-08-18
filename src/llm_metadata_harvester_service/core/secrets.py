from cryptography.fernet import Fernet, InvalidToken

from llm_metadata_harvester_service.core.config import TASK_SECRET_KEY


def encrypt_task_secret(secret: str) -> str:
    if not TASK_SECRET_KEY:
        raise RuntimeError("TASK_SECRET_KEY is required to submit jobs")
    return Fernet(TASK_SECRET_KEY.encode()).encrypt(secret.encode()).decode()


def decrypt_task_secret(secret: str) -> str:
    if not TASK_SECRET_KEY:
        raise RuntimeError("TASK_SECRET_KEY is required to execute jobs")
    try:
        return Fernet(TASK_SECRET_KEY.encode()).decrypt(secret.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError("Task secret could not be decrypted") from exc
