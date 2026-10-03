"""Fail-closed tool registration policy backed by an implementation review.

This is a guard against accidental registration/source drift, not a sandbox for
untrusted Python plugins. Anyone who can rewrite policy code can rewrite policy.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Callable


class Classification(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    UNKNOWN = "UNKNOWN"


_MANIFEST = json.loads(Path(__file__).with_name("read_only_manifest.json").read_text())
TOOL_AUDIT = MappingProxyType({key: MappingProxyType(value) for key, value in _MANIFEST["tools"].items()})
READ_METHODS = frozenset(_MANIFEST["sdk_read_methods"])


def read_only_enabled() -> bool:
    """Absent means secure; malformed values fail startup instead of disabling."""
    value = os.getenv("GARMIN_READ_ONLY", "true").strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError("GARMIN_READ_ONLY must be true or false")
    return value in {"true", "1", "yes"}


def sdk_is_reviewed() -> bool:
    """Reject dependency drift, including same-version patched SDK source."""
    try:
        if importlib.metadata.version("garminconnect") != _MANIFEST["reviewed_sdk_version"]:
            return False
        for module_name, digest in _MANIFEST["sdk_files"].items():
            module = importlib.import_module(module_name)
            if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != digest:
                return False
    except (OSError, TypeError, ImportError, importlib.metadata.PackageNotFoundError):
        return False
    return True


def validate_read_method(name: str) -> None:
    """Only reviewed high-level read methods may be requested by the service.

    Generic `connectapi`, `client`, `download`, arbitrary GraphQL, login/logout,
    upload and future unknown helpers are deliberately unavailable here.
    """
    if name not in READ_METHODS or not sdk_is_reviewed():
        raise PermissionError("Garmin operation is not an audited read method")


def classify_tool(fn: Callable) -> Classification:
    """Classify the actual handler, never the name supplied to @app.tool."""
    module_name = getattr(fn, "__module__", "")
    name = getattr(fn, "__name__", "")
    record = TOOL_AUDIT.get(f"{module_name}.{name}")
    if record is None or getattr(fn, "__qualname__", "") != record["qualname"]:
        return Classification.UNKNOWN
    try:
        module = importlib.import_module(module_name)
        path = Path(module.__file__).resolve()
        handler = fn
        seen = set()
        while hasattr(handler, "__wrapped__"):
            if id(handler) in seen:
                return Classification.UNKNOWN
            seen.add(id(handler))
            if (Path(inspect.getfile(handler)).resolve() != path or
                    handler.__code__.co_qualname not in record.get("wrapper_qualnames", [])):
                return Classification.UNKNOWN
            handler = handler.__wrapped__
        code = handler.__code__
        if Path(inspect.getfile(handler)).resolve() != path or code.co_name != name:
            return Classification.UNKNOWN
        if code.co_qualname != record["qualname"]:
            return Classification.UNKNOWN
        if hashlib.sha256(path.read_bytes()).hexdigest() != _MANIFEST["modules"].get(module_name):
            return Classification.UNKNOWN
        # A reviewed handler may delegate into another local module. Pin that
        # helper implementation too, so changing an imported helper cannot
        # silently add side effects while the handler's source remains intact.
        for dependency, digest in record.get("dependencies", {}).items():
            helper = importlib.import_module(dependency)
            if hashlib.sha256(Path(helper.__file__).read_bytes()).hexdigest() != digest:
                return Classification.UNKNOWN
        if not sdk_is_reviewed():
            return Classification.UNKNOWN
    except (OSError, TypeError, AttributeError, ImportError):
        return Classification.UNKNOWN
    return Classification(record["classification"])


def is_reviewed_read(fn: Callable) -> bool:
    return classify_tool(fn) is Classification.READ
