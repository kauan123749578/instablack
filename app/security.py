"""Hash de senhas + cifra sim\u00e9trica leve para senhas de contas IG."""
from __future__ import annotations

import base64
import hashlib
import hmac

import bcrypt

from app.config import settings

# bcrypt aceita no m\u00e1ximo 72 bytes; truncamos por seguran\u00e7a.
_BCRYPT_MAX_BYTES = 72


def _to_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    hashed = bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt())
    return hashed.decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------
# Cifra sim\u00e9trica simples (XOR + HMAC) para senhas de contas Instagram.
# Suficiente para evitar leitura crua no banco; n\u00e3o substitui um KMS.
# ------------------------------------------------------------------
def _derive_key(salt: bytes = b"ig-cred-v1") -> bytes:
    return hashlib.pbkdf2_hmac("sha256", settings.secret_key.encode("utf-8"), salt, 100_000)


def encrypt_secret(plain: str) -> str:
    if plain is None:
        return ""
    key = _derive_key()
    data = plain.encode("utf-8")
    stream = hashlib.shake_256(key + b"|stream").digest(len(data))
    cipher = bytes(b ^ s for b, s in zip(data, stream))
    mac = hmac.new(key, cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac + cipher).decode("ascii")


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except Exception:
        return None
    if len(raw) < 32:
        return None
    mac, cipher = raw[:32], raw[32:]
    key = _derive_key()
    expected_mac = hmac.new(key, cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        return None
    stream = hashlib.shake_256(key + b"|stream").digest(len(cipher))
    return bytes(b ^ s for b, s in zip(cipher, stream)).decode("utf-8", errors="replace")
