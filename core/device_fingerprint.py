"""Fingerprint estável por @ — compartilhado por instagrapi e aiograpi."""
from __future__ import annotations

import hashlib
import uuid


def stable_uuids(username: str) -> dict[str, str]:
    """Mesmo @ → mesmo device fingerprint (evita 'aparelho novo' a cada tentativa)."""
    seed = hashlib.sha256(f"instablack:{username.lower()}".encode()).hexdigest()

    def _u(n: int) -> str:
        h = hashlib.md5(f"{seed}:{n}".encode(), usedforsecurity=False).hexdigest()
        return str(uuid.UUID(h))

    phone = _u(1)
    return {
        "phone_id": phone,
        "uuid": _u(2),
        "client_session_id": _u(3),
        "advertising_id": _u(4),
        "android_device_id": f"android-{seed[:16]}",
        "request_id": _u(5),
        "tray_session_id": _u(6),
    }
