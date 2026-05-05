"""Append-only NDJSON debug logs for agent sessions (no secrets)."""

from __future__ import annotations

import json
import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-49b0e1.log"
_SESSION_ID = "49b0e1"


def agent_debug_log(
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    run_id: str = "run1",
) -> None:
    payload = {
        "sessionId": _SESSION_ID,
        "timestamp": int(time.time() * 1000),
        "location": location,
        "message": message,
        "data": data,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
    }
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        pass
