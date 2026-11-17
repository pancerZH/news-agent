"""SQLite (WAL) storage: items, clusters, cluster_items, snapshots, runs."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    tier INTEGER NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL,            -- ISO-8601 UTC, e.g. 2026-11-16T08:00:00Z
    fetched TEXT NOT NULL,
    hn_id INTEGER,
    hn_points INTEGER NOT NULL DEFAULT 0,
    hn_comments INTEGER NOT NULL DEFAULT 0,
    hn_top_comments TEXT NOT NULL DEFAULT '[]',
    embedding BLOB,
    embed_model TEXT,
    UNIQUE (source, canonical_url)
);
CREATE INDEX IF NOT EXISTS items_published ON items (published);
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- never reused: reports and memory cards cite C<id>
    day TEXT NOT NULL,
    title TEXT NOT NULL,
    score REAL NOT NULL,
    entities TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS clusters_day ON clusters (day);
CREATE TABLE IF NOT EXISTS cluster_items (
    cluster_id INTEGER NOT NULL REFERENCES clusters (id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES items (id),
    PRIMARY KEY (cluster_id, item_id)
);
CREATE TABLE IF NOT EXISTS snapshots (
    day TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    day TEXT NOT NULL,
    harness TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    exit_code INTEGER,
    session_id TEXT,
    resume TEXT,
    status TEXT,
    run_dir TEXT,
    report TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_items(conn: sqlite3.Connection, items: list[dict]) -> int:
    """Insert new items; refresh title/summary/HN stats of known ones. Returns the number of new rows."""
    new = 0
    with conn:
        for it in items:
            known = conn.execute("SELECT id FROM items WHERE source = ? AND canonical_url = ?",
                                 (it["source"], it["canonical_url"])).fetchone()
            if known is None:
                conn.execute(
                    "INSERT INTO items (source, tier, url, canonical_url, title, summary, published, fetched,"
                    " hn_id, hn_points, hn_comments, hn_top_comments) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["source"], it["tier"], it["url"], it["canonical_url"], it["title"], it.get("summary", ""),
                     it["published"], it["fetched"], it.get("hn_id"), it.get("hn_points", 0),
                     it.get("hn_comments", 0), json.dumps(it.get("hn_top_comments", []), ensure_ascii=False)))
                new += 1
            else:
                conn.execute(
                    "UPDATE items SET title = ?, hn_points = MAX(hn_points, ?), hn_comments = MAX(hn_comments, ?)"
                    " WHERE id = ?", (it["title"], it.get("hn_points", 0), it.get("hn_comments", 0), known["id"]))
                if it.get("hn_top_comments"):
                    conn.execute("UPDATE items SET hn_top_comments = ? WHERE id = ?",
                                 (json.dumps(it["hn_top_comments"], ensure_ascii=False), known["id"]))
    return new


def window_items(conn: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE published >= ? ORDER BY published, id", (since,)).fetchall()


def save_clusters(conn: sqlite3.Connection, day: str, clusters: list[dict]) -> list[int]:
    """Replace the day's clusters; returns the new cluster ids in the given order."""
    ids = []
    with conn:
        conn.execute("DELETE FROM clusters WHERE day = ?", (day,))
        for c in clusters:
            cur = conn.execute("INSERT INTO clusters (day, title, score, entities) VALUES (?,?,?,?)",
                               (day, c["title"], c["score"], json.dumps(c["entities"], ensure_ascii=False)))
            ids.append(cur.lastrowid)
            conn.executemany("INSERT INTO cluster_items (cluster_id, item_id) VALUES (?, ?)",
                             [(cur.lastrowid, item_id) for item_id in c["item_ids"]])
    return ids


def cluster_items(conn: sqlite3.Connection, cluster_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT i.* FROM items i JOIN cluster_items ci ON ci.item_id = i.id"
                        " WHERE ci.cluster_id = ? ORDER BY i.tier, i.published", (cluster_id,)).fetchall()


def save_snapshot(conn: sqlite3.Connection, day: str, data: dict) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO snapshots (day, data) VALUES (?, ?)",
                     (day, json.dumps(data, ensure_ascii=False)))


def load_snapshot(conn: sqlite3.Connection, day: str) -> dict | None:
    row = conn.execute("SELECT data FROM snapshots WHERE day = ?", (day,)).fetchone()
    return json.loads(row["data"]) if row else None
