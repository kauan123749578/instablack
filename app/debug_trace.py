"""Instrumentação temporária — sessão debug 1f5cf9."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-1f5cf9.log"
_SESSION = "1f5cf9"
_logger = logging.getLogger("debug.1f5cf9")


def dbg(hypothesis_id: str, location: str, message: str, data: dict | None = None) -> None:
    # #region agent log
    payload = {
        "sessionId": _SESSION,
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data or {},
        "hypothesisId": hypothesis_id,
    }
    line = json.dumps(payload, default=str)
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    _logger.warning("DEBUG1f5cf9 %s", line)
    # #endregion
