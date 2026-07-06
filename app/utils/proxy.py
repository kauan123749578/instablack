"""Normalização de strings de proxy para o formato aceito pelo instagrapi."""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlparse


def normalize_proxy(raw: str) -> str:
    """Converte formatos comuns (ip:porta:user:senha, socks5, etc.) para URL válida."""
    s = (raw or "").strip()
    if not s:
        return ""

    if re.match(r"^(https?|socks5h?|socks4)://", s, re.I):
        return s

    if "@" in s:
        return f"http://{s}"

    parts = s.split(":")
    if len(parts) >= 4 and parts[1].isdigit():
        host, port, user = parts[0], parts[1], parts[2]
        passwd = ":".join(parts[3:])
        user_q = quote(user, safe="")
        pass_q = quote(passwd, safe="")
        return f"http://{user_q}:{pass_q}@{host}:{port}"

    if len(parts) == 2 and parts[1].isdigit():
        return f"http://{parts[0]}:{parts[1]}"

    if "://" not in s:
        return f"http://{s}"

    return s


def proxy_label(url: str) -> str:
    """Texto curto para UI (esconde senha)."""
    if not url:
        return ""
    try:
        p = urlparse(url if "://" in url else f"http://{url}")
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        user = unquote(p.username) if p.username else ""
        if user:
            return f"{user}@{host}{port}"
        return f"{host}{port}"
    except Exception:
        return "proxy"


def proxy_to_raw(url: str) -> str:
    """Converte URL normalizada de volta para ip:porta:user:senha."""
    if not url:
        return ""
    s = url.strip()
    if "://" not in s and s.count(":") >= 3:
        return s
    try:
        p = urlparse(s if "://" in s else f"http://{s}")
        if p.hostname and p.port is not None and p.username:
            user = unquote(p.username)
            passwd = unquote(p.password or "")
            return f"{p.hostname}:{p.port}:{user}:{passwd}"
        if p.hostname and p.port is not None:
            return f"{p.hostname}:{p.port}"
    except Exception:
        pass
    return s


def diagnose_proxy(raw: str) -> dict:
    """Testa proxy e retorna status legível (para UI/API)."""
    import requests

    from core.instagram import IPIFY_URL, IP_CHECK_TIMEOUT, _server_public_ip

    normalized = normalize_proxy(raw)
    if not normalized:
        return {"ok": False, "ip": None, "error": "Informe host:porta:user:senha"}

    try:
        resp = requests.get(
            IPIFY_URL,
            proxies={"http": normalized, "https": normalized},
            timeout=IP_CHECK_TIMEOUT,
        )
        if resp.status_code == 402:
            return {
                "ok": False,
                "ip": None,
                "error": "402 Payment Required — plano/saldo do proxy expirou",
            }
        resp.raise_for_status()
        ip = resp.text.strip()
    except requests.exceptions.ProxyError as exc:
        msg = str(exc)
        if "402" in msg:
            err = "402 Payment Required — plano/saldo do proxy expirou"
        elif "407" in msg:
            err = "407 — usuário ou senha do proxy incorretos"
        elif "403" in msg:
            err = "403 — proxy recusou a conexão"
        else:
            err = "Proxy inacessível ou fora do ar"
        return {"ok": False, "ip": None, "error": err}
    except Exception:
        return {"ok": False, "ip": None, "error": "Proxy inacessível ou fora do ar"}

    server_ip = _server_public_ip()
    if server_ip and ip == server_ip:
        return {
            "ok": False,
            "ip": ip,
            "error": "Proxy vazando IP do servidor — tráfego não está passando pelo proxy",
        }
    return {"ok": True, "ip": ip, "error": None}


def clean_sessionid(raw: str) -> str:
    """Limpa sessionid colado do navegador (URL-encoded ou cookie completo)."""
    sid = unquote(raw.strip())
    lower = sid.lower()
    if lower.startswith("sessionid="):
        sid = sid.split("=", 1)[1].strip()
    return sid
