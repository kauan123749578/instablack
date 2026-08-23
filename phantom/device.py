"""Device Samsung alinhado ao Phantom (SteeL / melhorias).

instagrapi default = Pixel 8 Pro; headers Phantom = SM-E045F.
Sem isso o Instagram vê aparelho no body e outro no User-Agent.

IMPORTANTE: sempre manter ``bloks_versioning_id`` (hash do instagrapi).
Sem ele o CAA falha com: Client.bloks_versioning_id is empty.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("phantom.device")

# Hash padrão do instagrapi 2.18.x (CAA / Bloks). NÃO omitir no set_device.
DEFAULT_BLOKS_VERSIONING_ID = (
    "7189b949425f9bf80ea8bd880cf5a3080b292d9b1c4b38a18d112f7c4b71e7a8"
)

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
    "bloks_versioning_id": DEFAULT_BLOKS_VERSIONING_ID,
}

SAMSUNG_USER_AGENT = (
    "Instagram 434.0.0.44.74 Android "
    "(33/13; 300dpi; 720x1600; samsung; SM-E045F; m04; mt6765; pt_BR; 996255552)"
)


def apply_samsung_device(client) -> bool:
    """Aplica fingerprint Samsung SM-E045F no client instagrapi/Phantom."""
    try:
        # Preserva bloks do client atual se já existir (evita CAA quebrado).
        existing = getattr(client, "device_settings", None) or {}
        bloks = (
            (existing.get("bloks_versioning_id") or "").strip()
            or DEFAULT_BLOKS_VERSIONING_ID
        )
        device = {**SAMSUNG_M04_DEVICE, "bloks_versioning_id": bloks}

        if hasattr(client, "set_device"):
            client.set_device(device)
        else:
            client.device_settings = dict(device)

        # set_device do instagrapi às vezes dropa campos extras — reforça bloks
        try:
            if hasattr(client, "device_settings") and isinstance(client.device_settings, dict):
                if not (client.device_settings.get("bloks_versioning_id") or "").strip():
                    client.device_settings["bloks_versioning_id"] = bloks
            if hasattr(client, "bloks_versioning_id"):
                client.bloks_versioning_id = bloks
        except Exception:
            pass

        if hasattr(client, "set_user_agent"):
            try:
                client.set_user_agent(SAMSUNG_USER_AGENT)
            except TypeError:
                client.user_agent = SAMSUNG_USER_AGENT
        else:
            client.user_agent = SAMSUNG_USER_AGENT

        if hasattr(client, "_header_builder"):
            client._header_builder = None

        log.info(
            "Phantom device=samsung SM-E045F (m04) app=%s bloks=%s…",
            device["app_version"],
            bloks[:12],
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
