from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

DB_PATH = Path(os.environ.get("SPIDERPOOL_DB_PATH", "data/site_data.db"))

DEFAULT_DATA = {
    "domains": [],
    "external_links": [],
    "pages": {},
    "view_stats": {},
    "ai_logs": [],
    "bot_hits": [],
    "settings": {
        "auto_page_count": 8,
        "default_keywords": [],
        "deepseek_model": "deepseek-chat",
        "language": "zh",
        "ai_thread_count": 8,
        "article_min_words": 800,
        "article_max_words": 1500,
        "ai_console_log": True,
    },
}


def _get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS domains (
            host TEXT PRIMARY KEY,
            label TEXT,
            topic TEXT
        );
        CREATE TABLE IF NOT EXISTS external_links (
            url TEXT PRIMARY KEY,
            label TEXT
        );
        CREATE TABLE IF NOT EXISTS pages (
            slug TEXT PRIMARY KEY,
            title TEXT,
            topic TEXT,
            keywords TEXT,
            excerpt TEXT,
            body TEXT,
            host TEXT,
            generator TEXT,
            model TEXT,
            links TEXT,
            updated_at TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS view_stats (
            slug TEXT PRIMARY KEY,
            views INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT,
            message TEXT,
            meta TEXT,
            timestamp TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_hits (
            ua TEXT PRIMARY KEY,
            family TEXT,
            hits INTEGER DEFAULT 1,
            last_seen TEXT
        );
        """
    )
    _ensure_default_settings(conn)


def _ensure_default_settings(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT key FROM settings")
    existing = {row["key"] for row in cur.fetchall()}
    missing = {k: v for k, v in DEFAULT_DATA["settings"].items() if k not in existing}
    if not missing:
        return
    conn.executemany(
        "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
        [(key, json.dumps(value, ensure_ascii=False)) for key, value in missing.items()],
    )
    conn.commit()


def _fetch_settings(conn: sqlite3.Connection) -> Dict[str, Any]:
    settings: Dict[str, Any] = DEFAULT_DATA["settings"].copy()
    cur = conn.execute("SELECT key, value FROM settings")
    for row in cur.fetchall():
        try:
            settings[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            settings[row["key"]] = row["value"]
    return settings


def _fetch_table(conn: sqlite3.Connection, query: str) -> Iterable[sqlite3.Row]:
    cur = conn.execute(query)
    return cur.fetchall()


def load_data() -> Dict[str, Any]:
    conn = _get_connection()
    data: Dict[str, Any] = {
        "domains": [],
        "external_links": [],
        "pages": {},
        "view_stats": {},
        "ai_logs": [],
        "bot_hits": [],
        "settings": _fetch_settings(conn),
    }

    for row in _fetch_table(conn, "SELECT host, label, topic FROM domains"):
        data["domains"].append(dict(row))

    for row in _fetch_table(conn, "SELECT url, label FROM external_links"):
        data["external_links"].append(dict(row))

    for row in _fetch_table(conn, "SELECT * FROM pages"):
        keywords = json.loads(row["keywords"] or "[]")
        links = json.loads(row["links"] or "[]")
        page = {
            "slug": row["slug"],
            "title": row["title"],
            "topic": row["topic"],
            "keywords": keywords,
            "excerpt": row["excerpt"],
            "body": row["body"],
            "host": row["host"],
            "generator": row["generator"],
            "model": row["model"],
            "links": links,
            "updated_at": row["updated_at"],
            "created_at": row["created_at"],
        }
        data.setdefault("pages", {})[row["slug"]] = page

    for row in _fetch_table(conn, "SELECT slug, views FROM view_stats"):
        data["view_stats"][row["slug"]] = row["views"]

    for row in _fetch_table(conn, "SELECT level, message, meta, timestamp FROM ai_logs ORDER BY id DESC LIMIT 50"):
        meta = json.loads(row["meta"] or "{}")
        data["ai_logs"].append(
            {
                "level": row["level"],
                "message": row["message"],
                "meta": meta,
                "timestamp": row["timestamp"],
            }
        )

    for row in _fetch_table(conn, "SELECT ua, family, hits, last_seen FROM bot_hits ORDER BY hits DESC, last_seen DESC LIMIT 50"):
        data["bot_hits"].append(
            {
                "ua": row["ua"],
                "family": row["family"],
                "hits": row["hits"],
                "last_seen": row["last_seen"],
            }
        )

    return data


def save_data(payload: Dict[str, Any]) -> None:
    attempts = 0
    while True:
        attempts += 1
        try:
            conn = _get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM settings")
            cursor.executemany(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                [
                    (key, json.dumps(value, ensure_ascii=False))
                    for key, value in payload.get("settings", {}).items()
                ],
            )

            cursor.execute("DELETE FROM domains")
            cursor.executemany(
                "INSERT OR REPLACE INTO domains(host, label, topic) VALUES (?, ?, ?)",
                [
                    (item.get("host"), item.get("label"), item.get("topic"))
                    for item in payload.get("domains", [])
                    if item.get("host")
                ],
            )

            cursor.execute("DELETE FROM external_links")
            cursor.executemany(
                "INSERT OR REPLACE INTO external_links(url, label) VALUES (?, ?)",
                [
                    (item.get("url"), item.get("label"))
                    for item in payload.get("external_links", [])
                    if item.get("url")
                ],
            )

            cursor.execute("DELETE FROM pages")
            cursor.executemany(
                """
                INSERT OR REPLACE INTO pages(
                    slug, title, topic, keywords, excerpt, body, host, generator, model, links, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        slug,
                        page.get("title"),
                        page.get("topic"),
                        json.dumps(page.get("keywords", []), ensure_ascii=False),
                        page.get("excerpt"),
                        page.get("body"),
                        page.get("host"),
                        page.get("generator"),
                        page.get("model"),
                        json.dumps(page.get("links", []), ensure_ascii=False),
                        page.get("updated_at"),
                        page.get("created_at") or page.get("updated_at"),
                    )
                    for slug, page in payload.get("pages", {}).items()
                ],
            )

            existing_views = {
                row["slug"]: row["views"] for row in _fetch_table(conn, "SELECT slug, views FROM view_stats")
            }
            incoming_views = payload.get("view_stats", {})
            merged_views: Dict[str, int] = {}

            for slug, views in incoming_views.items():
                merged_views[slug] = max(int(views or 0), int(existing_views.get(slug, 0)))

            for slug, views in existing_views.items():
                if slug in merged_views:
                    continue
                if slug not in payload.get("pages", {}):
                    continue
                merged_views[slug] = int(views)

            cursor.execute("DELETE FROM view_stats")
            cursor.executemany(
                "INSERT OR REPLACE INTO view_stats(slug, views) VALUES (?, ?)",
                [(slug, views) for slug, views in merged_views.items()],
            )

            cursor.execute("DELETE FROM ai_logs")
            cursor.executemany(
                "INSERT INTO ai_logs(level, message, meta, timestamp) VALUES (?, ?, ?, ?)",
                [
                    (
                        item.get("level", "info"),
                        item.get("message"),
                        json.dumps(item.get("meta", {}), ensure_ascii=False),
                        item.get("timestamp"),
                    )
                    for item in payload.get("ai_logs", [])[-50:]
                ],
            )

            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError as exc:  # pragma: no cover - runtime resilience
            if "locked" not in str(exc).lower() or attempts >= 3:
                raise
            time.sleep(0.2 * attempts)
        finally:
            try:
                conn.close()
            except Exception:
                pass


def update_data(mutator: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    data = load_data()
    mutator(data)
    save_data(data)
    return data


def get_page(slug: str) -> Dict[str, Any] | None:
    data = load_data()
    return data.get("pages", {}).get(slug)


def record_view(slug: str, *, user_agent: str | None = None) -> None:
    conn = _get_connection()
    conn.execute(
        "INSERT INTO view_stats(slug, views) VALUES(?, 1) ON CONFLICT(slug) DO UPDATE SET views = views + 1",
        (slug,),
    )
    record_bot_hit(user_agent)
    conn.commit()
    conn.close()


def record_ai_event(message: str, *, level: str = "info", meta: Dict[str, Any] | None = None) -> None:
    conn = _get_connection()
    conn.execute(
        "INSERT INTO ai_logs(level, message, meta, timestamp) VALUES (?, ?, ?, datetime('now'))",
        (level, message, json.dumps(meta or {}, ensure_ascii=False),),
    )
    conn.commit()
    conn.close()


BOT_PATTERNS: List[Tuple[str, str]] = [
    ("googlebot", "Google"),
    ("bingbot", "Bing"),
    ("baiduspider", "Baidu"),
    ("bytespider", "ByteDance"),
    ("yisouspider", "360"),
    ("sogou spider", "Sogou"),
    ("duckduckbot", "DuckDuckGo"),
    ("slurp", "Yahoo"),
    ("seznambot", "Seznam"),
    ("mj12bot", "Majestic"),
]


def detect_bot(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    lowered = user_agent.lower()
    for pattern, family in BOT_PATTERNS:
        if re.search(pattern, lowered):
            return family
    if "bot" in lowered or "spider" in lowered:
        return "Unknown"
    return None


def record_bot_hit(user_agent: str | None) -> None:
    family = detect_bot(user_agent)
    if not family:
        return
    ua_value = user_agent or "Unknown"
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO bot_hits(ua, family, hits, last_seen)
        VALUES(?, ?, 1, datetime('now'))
        ON CONFLICT(ua) DO UPDATE SET
            hits = bot_hits.hits + 1,
            family = excluded.family,
            last_seen = datetime('now')
        """,
        (ua_value[:500], family),
    )
    conn.commit()
    conn.close()
