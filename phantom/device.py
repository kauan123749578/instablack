"""Device Samsung alinhado ao Phantom (SteeL / melhorias).

instagrapi default = Pixel 8 Pro; headers Phantom = SM-E045F.
Sem isso o Instagram vê aparelho no body e outro no User-Agent.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("phantom.device")

# Instagram 434.0.0.44.74 Android (33/13; 300dpi; 720x1600;
# samsung; SM-E045F; m04; mt6765; …; 996255552)
SAMSUNG_M04_DEVICE: dict[str, Any] = {
    "app_version": "434.0.0.44.74",
    "android_version": 33,
    "android_release": "13",
    "dpi": "300dpi",
    "resolution": "720x1600",
    "manufacturer": "samsung",
    "device": "m04",
    "model": "SM-E045F",
    "cpu": "mt6765",
    "version_code": "996255552",
}

SAMSUNG_USER_AGENT = (
    "Instagram 434.0.0.44.74 Android "
    "(33/13; 300dpi; 720x1600; samsung; SM-E045F; m04; mt6765; pt_BR; 996255552)"
)


def apply_samsung_device(client) -> bool:
    """Aplica fingerprint Samsung SM-E045F no client instagrapi/Phantom."""
    try:
        if hasattr(client, "set_device"):
            client.set_device(SAMSUNG_M04_DEVICE)
        else:
            client.device_settings = dict(SAMSUNG_M04_DEVICE)
        # Força UA coerente (alguns builds do instagrapi não regeneram sozinho)
        if hasattr(client, "set_user_agent"):
            try:
                client.set_user_agent(SAMSUNG_USER_AGENT)
            except TypeError:
                client.user_agent = SAMSUNG_USER_AGENT
        else:
            client.user_agent = SAMSUNG_USER_AGENT
        # HeaderBuilder do Phantom é recriado na próxima request
        if hasattr(client, "_header_builder"):
            client._header_builder = None
        log.info(
            "Phantom device=samsung SM-E045F (m04) app=%s",
            SAMSUNG_M04_DEVICE["app_version"],
        )
        return True
    except Exception as exc:
        log.warning("Falha ao aplicar device Samsung: %s", exc)
        return False


def settings_has_device(settings_dict: dict | None) -> bool:
    if not isinstance(settings_dict, dict):
        return False
    ds = settings_dict.get("device_settings") or settings_dict.get("device")
    return isinstance(ds, dict) and bool(ds.get("model") or ds.get("manufacturer"))
