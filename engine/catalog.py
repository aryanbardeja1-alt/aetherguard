"""Load the baked NORAD sky catalog used by the traffic display."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "sky_catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> list[dict[str, Any]]:
    """Return catalog objects from ``data/sky_catalog.json``."""
    if not _CATALOG_PATH.is_file():
        return []
    payload = json.loads(_CATALOG_PATH.read_text())
    objects = payload.get("objects", [])
    return list(objects)


def get_entry(sat_id: str) -> dict[str, Any] | None:
    for entry in load_catalog():
        if str(entry.get("id")) == str(sat_id) or str(entry.get("norad_id")) == str(sat_id):
            return entry
    return None
