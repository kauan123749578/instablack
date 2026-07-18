"""Integração com a API de pedidos PIX da GBProxies."""
from __future__ import annotations

from typing import Any

import requests

from app.config import settings


class GBProxiesError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.gbproxies_api_token.strip())


def _request(method: str, path: str, *, json: dict | None = None) -> dict[str, Any]:
    if not is_configured():
        raise GBProxiesError("Token da GBProxies não configurado.")
    response = requests.request(
        method,
        f"{settings.gbproxies_api_base_url.rstrip('/')}/{path.lstrip('/')}",
        headers={
            "Authorization": f"Bearer {settings.gbproxies_api_token.strip()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=json,
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok or payload.get("success") is False:
        detail = (
            payload.get("message")
            or payload.get("error")
            or response.text[:500]
            or f"HTTP {response.status_code}"
        )
        raise GBProxiesError(str(detail))
    return payload


def create_pix_order(*, proxy_type: str, country_id: int, quantity: int) -> dict[str, Any]:
    return _request(
        "POST",
        "create-pix-order",
        json={"type": proxy_type, "country_id": country_id, "quantity": quantity},
    )


def get_order(provider_order_id: str) -> dict[str, Any]:
    return _request("GET", f"order/{provider_order_id}")


def order_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return str(data.get("order_id") or data.get("id") or "")


def normalized_order(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    pix = data.get("pix") if isinstance(data.get("pix"), dict) else {}
    proxies = data.get("proxies") if isinstance(data.get("proxies"), list) else []
    qr_code = data.get("qr_code") or data.get("qr_code_base64") or pix.get("qr_code")
    if isinstance(qr_code, str):
        qr_code = qr_code.strip()
        if len(qr_code) > 100 and not qr_code.startswith(("data:image/", "https://")):
            qr_code = f"data:image/png;base64,{qr_code}"
        elif not qr_code.startswith(("data:image/", "https://")):
            qr_code = None
    return {
        "provider_order_id": order_id(payload),
        "status": str(data.get("status") or "pending"),
        "paid": bool(data.get("paid") or str(data.get("status", "")).lower() == "paid"),
        "amount": str(data.get("amount") or data.get("total") or "") or None,
        "pix_code": (
            data.get("pix_code")
            or data.get("pix_copy_paste")
            or data.get("copy_paste")
            or pix.get("code")
            or pix.get("copy_paste")
        ),
        "qr_code": qr_code,
        "proxies": [str(item) for item in proxies if item],
    }
