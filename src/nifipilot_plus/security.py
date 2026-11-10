"""Output redaction and the audit log."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "passcode",
    "keytab",
    "credential",
    "apikey",
    "api_key",
)
REDACTED = "***REDACTED***"


def _is_sensitive(key: Any) -> bool:
    k = str(key).lower()
    return any(part in k for part in _SENSITIVE_KEY_PARTS)


def redact(obj: Any, max_items: int = 200) -> Any:
    """Mask sensitive-looking keys and cap very large lists (keeps LLM context small)."""
    if isinstance(obj, dict):
        return {
            k: (REDACTED if _is_sensitive(k) and v not in (None, "") else redact(v, max_items))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        items = [redact(x, max_items) for x in obj[:max_items]]
        if len(obj) > max_items:
            items.append({"truncated": True, "omitted_count": len(obj) - max_items})
        return items
    return obj


class Audit:
    """Append-only JSON-lines log of every tool invocation."""

    def __init__(self, enabled: bool, path: str):
        self.enabled = enabled
        self.path = Path(path)
        self._lock = threading.Lock()

    def log(self, tool: str, params: dict[str, Any], result: str, error: str | None = None) -> None:
        if not self.enabled:
            return
        entry = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "tool": tool,
            "params": redact(params),
            "result": result,
            "error": error,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
