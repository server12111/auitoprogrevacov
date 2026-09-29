"""Шифрование чувствительных данных (пароли 2FA, строки сессий) для хранения в БД."""
from cryptography.fernet import Fernet, InvalidToken

from app import config

_fernet = Fernet(config.ENCRYPTION_KEY.encode())


def encrypt(plain: str | None) -> str | None:
    if plain is None:
        return None
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return None
