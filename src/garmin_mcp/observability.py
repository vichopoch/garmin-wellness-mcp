"""Minimal structured production logs; never serialize tool arguments/results.

Unknown fields, arbitrary log messages, exception messages and tracebacks are
omitted rather than attempting to recognize every possible secret format.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")
_REQUEST_ID = re.compile(r"^[0-9a-f-]{32,36}$")
_STATUSES = {"success", "error", "denied", "unavailable", "started", "finished", "skipped"}
_EVENTS = {"mcp_tool_call", "http_request", "sync", "startup", "shutdown", "garmin_call", "auth"}


def _safe_fields(fields: dict) -> dict:
    result = {}
    for key in ("tool", "error_type"):
        value = fields.get(key)
        if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            result[key] = value
    if isinstance(fields.get("status"), str) and fields["status"] in _STATUSES:
        result["status"] = fields["status"]
    duration = fields.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and 0 <= duration < 86400000:
        result["duration_ms"] = round(duration, 2)
    request_id = fields.get("request_id")
    if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
        result["request_id"] = request_id
    return result


class PrivateJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "safe_event", None)
        payload = {"event": event if isinstance(event, str) and event in _EVENTS else "library_log", "level": record.levelname}
        payload.update(_safe_fields(getattr(record, "safe_fields", {})))
        # Do not call getMessage(), formatException(), or serialize record.args.
        return json.dumps(payload, ensure_ascii=True, allow_nan=False)


def log_event(event: str, **fields) -> None:
    logging.getLogger("garmin_mcp.audit").info("", extra={"safe_event": event, "safe_fields": _safe_fields(fields)})


def configure_private_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(PrivateJSONFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    # Production intentionally does not support payload debugging.
    root.setLevel(level if level in {"INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "mcp", "garminconnect", "garth", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
