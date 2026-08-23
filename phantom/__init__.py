"""Phantom — camada stealth sobre instagrapi (headers + TLS + login Bloks CAA).

Uso no Instablack via ``core.instagram._build_client`` (EnhancedClient).
"""
from __future__ import annotations

from .client import EnhancedClient
from .device import SAMSUNG_M04_DEVICE, apply_samsung_device
from .login import LoginFlow, login
from .navigation import NavigationTracker
from .transport import PhantomSession, create_session

__all__ = [
    "EnhancedClient",
    "LoginFlow",
    "login",
    "NavigationTracker",
    "PhantomSession",
    "create_session",
    "SAMSUNG_M04_DEVICE",
    "apply_samsung_device",
]
