"""Load the baked NORAD sky catalog used by the traffic display."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "sky_catalog.json"
_cache: tuple[float, list[dict[str, Any]]] | None = None


def load_catalog() -> list[dict[str, Any]]:
    """Return catalog objects from ``data/sky_catalog.json`` (mtime-aware cache)."""
    global _cache
    if not _CATALOG_PATH.is_file():
        return []
    mtime = _CATALOG_PATH.stat().st_mtime
    if _cache is not None and _cache[0] == mtime:
        return list(_cache[1])
    payload = json.loads(_CATALOG_PATH.read_text())
    objects = list(payload.get("objects", []))
    _cache = (mtime, objects)
    return list(objects)


def get_entry(sat_id: str) -> dict[str, Any] | None:
    for entry in load_catalog():
        if str(entry.get("id")) == str(sat_id) or str(entry.get("norad_id")) == str(sat_id):
            return entry
    return None
