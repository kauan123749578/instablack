"""Perfil do navegador capturado pela extensão Chrome (UA + fingerprint leve).

Usado na API web para alinhar headers com a sessão real do usuário —
em vez do Chrome genérico hardcoded.
"""
from __future__ import annotations

import json
from typing import Any

from app.security import decrypt_secret, encrypt_secret

BROWSER_KEYS = (
    "user_agent",
    "language",
    "languages",
    "platform",
    "vendor",
    "hardware_concurrency",
    "device_memory",
    "screen_width",
    "screen_height",
    "color_depth",
    "pixel_ratio",
    "timezone",
    "timezone_offset",
    "cookie_enabled",
    "do_not_track",
    "webdriver",
    "max_touch_points",
    "chrome_version",
    "captured_at",
)


def normalize_browser_profile(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitiza o payload da extensão — só campos úteis, tipos estáveis."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}

    ua = str(raw.get("user_agent") or raw.get("userAgent") or "").strip()
    if ua:
        out["user_agent"] = ua[:512]

    lang = str(raw.get("language") or "").strip()
    if lang:
        out["language"] = lang[:32]

    langs = raw.get("languages")
    if isinstance(langs, list):
        cleaned = [str(x).strip()[:32] for x in langs if str(x).strip()]
        if cleaned:
            out["languages"] = cleaned[:12]

    for key in ("platform", "vendor", "timezone", "chrome_version", "captured_at"):
        val = str(raw.get(key) or "").strip()
        if val:
            out[key] = val[:128]

    screen = raw.get("screen") if isinstance(raw.get("screen"), dict) else {}
    for src, dst in (
        ("width", "screen_width"),
        ("height", "screen_height"),
        ("colorDepth", "color_depth"),
        ("color_depth", "color_depth"),
    ):
        val = raw.get(dst, screen.get(src))
        try:
            if val is not None:
                out[dst] = int(val)
        except (TypeError, ValueError):
            pass

    for key in (
        "hardware_concurrency",
        "device_memory",
        "timezone_offset",
        "max_touch_points",
    ):
        try:
            if raw.get(key) is not None:
                out[key] = int(raw[key])
        except (TypeError, ValueError):
            pass

    try:
        if raw.get("pixel_ratio") is not None:
            out["pixel_ratio"] = float(raw["pixel_ratio"])
    except (TypeError, ValueError):
        pass

    for key in ("cookie_enabled", "webdriver"):
        if key in raw:
            out[key] = bool(raw[key])

    dnt = raw.get("do_not_track")
    if dnt is not None and str(dnt).strip():
        out["do_not_track"] = str(dnt).strip()[:16]

    return out


def encrypt_web_browser(profile: dict[str, Any] | None) -> str | None:
    normalized = normalize_browser_profile(profile)
    if not normalized.get("user_agent"):
        return None
    return encrypt_secret(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))


def decrypt_web_browser(token: str | None) -> dict[str, Any] | None:
    raw = decrypt_secret(token)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return normalize_browser_profile(payload)


def accept_language_header(profile: dict[str, Any] | None) -> str | None:
    if not profile:
        return None
    langs = profile.get("languages")
    if isinstance(langs, list) and langs:
        parts: list[str] = []
        for i, lang in enumerate(langs[:8]):
            q = 1.0 - (i * 0.1)
            if i == 0:
                parts.append(str(lang))
            else:
                parts.append(f"{lang};q={q:.1f}")
        return ",".join(parts)
    lang = str(profile.get("language") or "").strip()
    return lang or None
