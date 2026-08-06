"""CSRF (session token) + headers de segurança HTTP."""
from __future__ import annotations

import logging
import secrets
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

log = logging.getLogger(__name__)

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"

_CSRF_EXEMPT_PREFIXES = (
    "/static/",
    "/media/",
    "/manifest.webmanifest",
    "/sw.js",
    "/readyz",
    "/healthz",
    "/robots.txt",
    "/accounts/meta/callback",
    "/accounts/meta/data-deletion",
    "/accounts/meta/deauthorize",
    "/api/extension/",
)


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token or not isinstance(token, str) or len(token) < 16:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def _is_exempt(path: str) -> bool:
    for prefix in _CSRF_EXEMPT_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def validate_csrf(request: Request, submitted: str | None) -> bool:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not isinstance(expected, str):
        return False
    if not submitted:
        return False
    return secrets.compare_digest(expected, submitted)


def security_headers_for(response: Response, *, production: bool) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    if production:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )


def csrf_forbidden_response(request: Request) -> Response:
    msg = "Token CSRF inválido ou ausente. Recarregue a página e tente de novo."
    accept = request.headers.get("accept", "")
    if "application/json" in accept or "fetch" in (
        request.headers.get("x-requested-with") or ""
    ).lower():
        return JSONResponse({"error": "csrf_invalid", "detail": msg}, status_code=403)
    return HTMLResponse(
        f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
        <title>CSRF</title></head>
        <body style="font-family:system-ui;background:#0b0d12;color:#eee;padding:24px">
        <h1>Requisição bloqueada</h1><p>{msg}</p>
        <p><a href="javascript:history.back()" style="color:#C9A227">Voltar</a></p>
        </body></html>""",
        status_code=403,
    )


async def extract_csrf_token(request: Request) -> str | None:
    """Extrai CSRF sem quebrar o parse posterior do FastAPI.

    Em forms urlencoded NÃO usa request.form() (isso esvaziava username/password
    no login/registro com Starlette 1.x). Lê o body cacheado e faz parse_qs.
    """
    header = request.headers.get(CSRF_HEADER) or request.headers.get("X-CSRF-Token")
    if header and header.strip():
        return header.strip()

    ctype = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ctype:
        try:
            form = await request.form()
            raw = form.get(CSRF_FORM_FIELD)
            return str(raw) if raw is not None else None
        except Exception:
            return None

    if "application/x-www-form-urlencoded" in ctype:
        try:
            body = await request.body()
            if not body:
                return None
            parsed = parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
            vals = parsed.get(CSRF_FORM_FIELD) or []
            return str(vals[0]) if vals else None
        except Exception:
            return None
    return None
