"""Small storage helpers for local JSON persistence.

The app is intentionally dependency-free, so local state is stored in JSON
files. These helpers keep writes atomic and centralize pruning logic.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_JSON_FILE_LOCK = threading.Lock()


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    return payload


def atomic_write_json(path: Path, payload: Any, *, ensure_ascii: bool = True) -> None:
    body = json.dumps(payload, indent=2, ensure_ascii=ensure_ascii)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with _JSON_FILE_LOCK:
        tmp_path.write_text(body, encoding="utf-8")
        os.replace(tmp_path, path)


def prune_mapping(mapping: dict[str, Any], max_items: int, timestamp_key: str = "createdAt") -> dict[str, Any]:
    if max_items <= 0 or len(mapping) <= max_items:
        return mapping

    def item_time(item: Any) -> float:
        if isinstance(item, dict):
            value = item.get(timestamp_key) or item.get("resolvedAt") or item.get("cachedAt")
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    return 0.0
        return 0.0

    return dict(sorted(mapping.items(), key=lambda pair: item_time(pair[1]), reverse=True)[:max_items])
