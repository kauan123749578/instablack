"""TOTP (Authenticator) — normaliza chave e gera código de 6 dígitos."""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import pyotp

_BASE32_RE = re.compile(r"^[A-Z2-7]+=*$")


class TotpError(ValueError):
    """Chave TOTP inválida."""


def normalize_totp_secret(raw: str | None) -> str:
    """Aceita Base32 ou URI otpauth://...; devolve secret Base32 limpo."""
    text = (raw or "").strip().replace(" ", "").replace("-", "")
    if not text:
        raise TotpError("Chave TOTP vazia.")

    if text.lower().startswith("otpauth://"):
        parsed = urlparse(text)
        qs = parse_qs(parsed.query)
        secret = (qs.get("secret") or [None])[0]
        if not secret:
            raise TotpError("URI otpauth sem parâmetro secret.")
        text = unquote(secret).strip().replace(" ", "").replace("-", "")

    text = text.upper()
    # Remove espaços comuns em keys copiadas
    text = re.sub(r"[^A-Z2-7=]", "", text)
    if len(text) < 16:
        raise TotpError("Chave TOTP curta demais (mín. ~16 caracteres Base32).")
    if not _BASE32_RE.match(text):
        raise TotpError("Chave TOTP inválida (use Base32 ou otpauth://).")

    # Valida que pyotp consegue gerar
    try:
        code = pyotp.TOTP(text).now()
        if not code or len(code) < 6:
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
