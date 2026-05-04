"""Admin-editable settings (prompts, paths) stored as JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings


def _path() -> Path:
    p = Path(get_settings().sqlite_db_path).parent / "admin_settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_admin_settings() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_admin_settings(data: dict[str, Any]) -> None:
    _path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_prompt_override(key: str) -> Optional[str]:
    return load_admin_settings().get("prompts", {}).get(key)


def is_admin_user(email: Optional[str]) -> bool:
    if not email:
        return False
    s = get_settings()
    admins = load_admin_settings().get("admin_emails") or []
    if email in admins:
        return True
    # Dev bypass
    if s.skip_entra_auth and email.endswith("@local.test"):
        return True
    return False
