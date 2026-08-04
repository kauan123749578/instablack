"""TOTP (Authenticator) — normaliza chave e gera código de 6 dígitos."""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import pyotp

_BASE32_RE = re.compile(r"^[A-Z2-7]+=*$")
_SIX_DIGIT_RE = re.compile(r"^\d{6}$")


class TotpError(ValueError):
    """Chave TOTP inválida."""


def _pad_base32(secret: str) -> str:
    """Base32 precisa de comprimento múltiplo de 8 (padding '=')."""
    rem = len(secret) % 8
    if rem:
        secret = secret + ("=" * (8 - rem))
    return secret


def normalize_totp_secret(raw: str | None) -> str:
    """Aceita Base32 ou URI otpauth://...; devolve secret Base32 limpo."""
    text = (raw or "").strip()
    if not text:
        raise TotpError("Chave TOTP vazia.")

    # Usuário colou o código de 6 dígitos em vez da chave secreta
    compact = re.sub(r"\s+", "", text)
    if _SIX_DIGIT_RE.match(compact):
        raise TotpError(
            "Isso é o código de 6 dígitos, não a chave. "
            "No Authenticator: editar conta > mostrar chave / secret key (Base32)."
        )

    if text.lower().startswith("otpauth://"):
        parsed = urlparse(text)
        qs = parse_qs(parsed.query)
        secret = (qs.get("secret") or [None])[0]
        if not secret:
            raise TotpError("URI otpauth sem parâmetro secret.")
        text = unquote(secret).strip()

    text = text.upper().replace(" ", "").replace("-", "").replace("\n", "").replace("\r", "")
    text = re.sub(r"[^A-Z2-7=]", "", text)
    if not text:
        raise TotpError(
            "Chave inválida. Cole a chave secreta Base32 (letras A–Z e 2–7), "
            "não o código que muda a cada 30s."
        )
    if len(text.rstrip("=")) < 8:
        raise TotpError(
            "Chave curta demais. Cole a chave secreta completa do Authenticator "
            "(geralmente 16+ caracteres), não o código de 6 dígitos."
        )

    text = _pad_base32(text.rstrip("="))
    if not _BASE32_RE.match(text):
        raise TotpError("Chave TOTP inválida (use Base32 ou otpauth://).")

    try:
        code = pyotp.TOTP(text).now()
        if not code or len(str(code)) < 6:
            raise TotpError("Não foi possível gerar código com esta chave.")
    except Exception as exc:
        if isinstance(exc, TotpError):
            raise
        raise TotpError(f"Chave TOTP inválida: {exc}") from exc
    return text


def current_totp_code(secret: str) -> tuple[str, int]:
    """Retorna (código 6 dígitos, segundos restantes no período)."""
    totp = pyotp.TOTP(secret)
    code = totp.now()
    remaining = int(totp.interval - (time.time() % totp.interval))
    if remaining <= 0:
        remaining = int(totp.interval)
    return code, remaining
