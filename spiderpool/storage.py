from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict

DATA_PATH = Path(os.environ.get("SPIDERPOOL_DATA_FILE", "data/site_data.json"))

DEFAULT_DATA = {
    "domains": [],
    "external_links": [],
    "pages": {},
    "view_stats": {},
    "settings": {
        "auto_page_count": 12,
        "default_keywords": [],
        "deepseek_model": "deepseek-chat",
        "language": "zh",
    },
}


def _ensure_storage_file() -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_PATH.exists():
        DATA_PATH.write_text(json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2), encoding="utf-8")


def load_data() -> Dict[str, Any]:
    _ensure_storage_file()
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # fallback to defaults if file got corrupted
        DATA_PATH.write_text(json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def save_data(payload: Dict[str, Any]) -> None:
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_data(mutator: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    data = load_data()
    mutator(data)
    save_data(data)
    return data


def get_page(slug: str) -> Dict[str, Any] | None:
    data = load_data()
    return data.get("pages", {}).get(slug)


def record_view(slug: str) -> None:
    def _mutate(payload: Dict[str, Any]) -> None:
        stats = payload.setdefault("view_stats", {})
        stats[slug] = stats.get(slug, 0) + 1

    update_data(_mutate)
