"""Load the baked NORAD sky catalog used by the traffic display."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "sky_catalog.json"
_cache: tuple[float, list[dict[str, Any]]] | None = None

#: Synthetic objects deployed at runtime, keyed by id. Kept in memory rather
#: than written to the catalog file so a demo never mutates real data.
_runtime: dict[str, dict[str, Any]] = {}


def load_catalog() -> list[dict[str, Any]]:
    """Return catalog objects from ``data/sky_catalog.json`` (mtime-aware cache).

    Runtime-deployed synthetic objects are appended to the baked catalog.
    """
    global _cache
    if not _CATALOG_PATH.is_file():
        return list(_runtime.values())
    mtime = _CATALOG_PATH.stat().st_mtime
    if _cache is None or _cache[0] != mtime:
        payload = json.loads(_CATALOG_PATH.read_text())
        _cache = (mtime, list(payload.get("objects", [])))
    return list(_cache[1]) + list(_runtime.values())


def register_runtime_objects(objects: list[dict[str, Any]]) -> None:
    """Add or replace synthetic objects visible to the catalog."""
    for entry in objects:
        _runtime[str(entry["id"])] = entry


def clear_runtime_objects() -> None:
    """Remove every synthetic object."""
    _runtime.clear()


def get_entry(sat_id: str) -> dict[str, Any] | None:
    for entry in load_catalog():
        if str(entry.get("id")) == str(sat_id) or str(entry.get("norad_id")) == str(sat_id):
            return entry
    return None
