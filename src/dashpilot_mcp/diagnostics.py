"""Local diagnostics: redacted crash reporting.

When a tool call fails, a small crash record is kept for support: the tool name, the
error code, and a redacted shape of the arguments. Redaction is structural — strings,
numbers and blobs are replaced by their types, so a record can never carry an address,
a phone number, a key, or free text out with it. Records live in memory for the
session and ride the support bundle when one is sent.
"""
from __future__ import annotations

import time
from typing import Any

_MAX_RECORDS = 20
_records: list[dict] = []


def redact(value: Any) -> Any:
    """Replace a value with its shape. Structure (list sizes, dict keys that are
    themselves field names) survives; content does not."""
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, (int, float)):
        return "<num>"
    if value is None:
        return None
    return "<str>"


def capture_crash(tool_name: str, exc: Exception, args: dict | None = None) -> None:
    """Record a redacted crash. Never raises — diagnostics must not break the tool."""
    try:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool": tool_name,
            "error": type(exc).__name__,
            "code": getattr(exc, "code", None),
            "args": redact(args or {}),
        }
        _records.append(record)
        del _records[:-_MAX_RECORDS]
    except Exception:
        pass


def redacted_records() -> list[dict]:
    return list(_records)
