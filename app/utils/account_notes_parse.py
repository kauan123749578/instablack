"""Parse lote de contas IG: `user | senha |` + URL 2FA (browserscan / otpauth)."""
from __future__ import annotations

import re

from app.utils.totp import TotpError, normalize_totp_secret

_URL_RE = re.compile(r"https?://[^\s]+", re.I)
_USER_RE = re.compile(r"^[A-Za-z0-9._]{1,64}$")
_MAX_IMPORT = 200


def extract_totp_from_text(raw: str | None) -> str | None:
    """Tenta achar uma chave TOTP em URL (#fragment), otpauth:// ou Base32 puro."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return normalize_totp_secret(text)
    except TotpError:
        return None


def _parse_cred_line(line: str) -> tuple[str, str, str | None] | None:
    """`1 user | senha |` ou `user | senha | https://...#SECRET`."""
    line = line.strip()
    if not line:
        return None
    line = re.sub(r"^\d+[\).\s:-]+", "", line).strip()
    url_m = _URL_RE.search(line)
    totp_hint = url_m.group(0) if url_m else None
    cred_part = line[: url_m.start()] if url_m else line
    if "|" not in cred_part:
        return None
    parts = [p.strip() for p in cred_part.split("|")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None
    username = parts[0].lstrip("@").strip()
    password = parts[1].strip()
    if not username or not password or not _USER_RE.fullmatch(username):
        return None
    return username, password, totp_hint


def parse_account_notes_blob(raw: str) -> tuple[list[dict], list[str]]:
    """Devolve (entries, warnings).

    Cada entry: username, password, totp_secret (str | None).
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return [], ["Cole o lote de contas."]

    entries: list[dict] = []
    warnings: list[str] = []
    pending: dict | None = None
    seen: dict[str, int] = {}

    def _flush() -> None:
        nonlocal pending
        if not pending:
            return
        key = pending["username"].lower()
        if key in seen:
            idx = seen[key]
            prev = entries[idx]
            prev["password"] = pending["password"] or prev["password"]
            if pending.get("totp_secret"):
                prev["totp_secret"] = pending["totp_secret"]
            warnings.append(f"@{pending['username']}: duplicada no lote — atualizei a anterior.")
        else:
            if len(entries) >= _MAX_IMPORT:
                warnings.append(f"Limite de {_MAX_IMPORT} contas por cola. O resto foi ignorado.")
                pending = None
                return
            seen[key] = len(entries)
            entries.append(pending)
        pending = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        cred = _parse_cred_line(line)
        if cred:
            _flush()
            username, password, totp_hint = cred
            secret = extract_totp_from_text(totp_hint) if totp_hint else None
            pending = {
                "username": username,
                "password": password,
                "totp_secret": secret,
            }
            continue

        secret = extract_totp_from_text(line)
        if secret:
            if pending is None and entries:
                entries[-1]["totp_secret"] = secret
            elif pending is not None:
                pending["totp_secret"] = secret
            else:
                warnings.append(f"Chave 2FA sem usuário/senha ignorada: {line[:48]}")
            continue

        warnings.append(f"Linha ignorada: {line[:80]}")

    _flush()
    if not entries:
        warnings.append(
            "Nenhuma conta reconhecida. Use: usuario | senha | e na linha de baixo a URL do 2FA."
        )
    return entries, warnings
