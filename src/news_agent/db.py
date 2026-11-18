"""SQLite (WAL) storage: items, clusters, cluster_items, snapshots, runs, transcripts, claims."""

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
    content TEXT NOT NULL DEFAULT '',  -- full text (feed body or fetched); '' when none
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
CREATE TABLE IF NOT EXISTS transcripts (
    item_id INTEGER PRIMARY KEY REFERENCES items (id),
    kind TEXT NOT NULL,                 -- labeled (speaker labels in the text) | vtt (beehiiv captions) | none
    body TEXT NOT NULL,                 -- one "[P0001] ..." line per paragraph or caption cue
    audio TEXT,                         -- episode audio URL (vtt only; for `news-agent voicecheck`)
    status TEXT NOT NULL,               -- pending | done | skipped: ... | failed: ... | none
    attempts INTEGER NOT NULL DEFAULT 0,  -- failed extraction sessions so far
    speakers TEXT NOT NULL DEFAULT '[]',
    run_id TEXT,
    extracted TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- never reused: reports and memory cards cite TC<id>
    item_id INTEGER NOT NULL REFERENCES items (id),
    speaker TEXT NOT NULL,
    role TEXT NOT NULL,                     -- host | guest | clip | unknown
    attribution TEXT NOT NULL,              -- label | text_cue | org | role | unknown
    para INTEGER NOT NULL,                  -- paragraph whose text contains the quote
    quote TEXT NOT NULL,
    claim TEXT NOT NULL,
    type TEXT NOT NULL,                     -- fact | forecast | opinion | host_premise | hearsay
    numbers TEXT NOT NULL DEFAULT '',
    horizon TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL DEFAULT '[]',
    theses TEXT NOT NULL DEFAULT '[]',
    voice TEXT                              -- `news-agent voicecheck` result
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    if "content" not in {r["name"] for r in conn.execute("PRAGMA table_info(items)")}:  # databases before full text
        conn.execute("ALTER TABLE items ADD COLUMN content TEXT NOT NULL DEFAULT ''")
    if "attempts" not in {r["name"] for r in conn.execute("PRAGMA table_info(transcripts)")}:  # first version
        conn.execute("ALTER TABLE transcripts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    return conn


def upsert_items(conn: sqlite3.Connection, items: list[dict]) -> int:
    """Insert new items; refresh title/HN stats of known ones and fill in a longer full text. Returns the number
    of new rows. A known item's summary is kept: its embedding was computed from it."""
    new = 0
    with conn:
        for it in items:
            known = conn.execute("SELECT id FROM items WHERE source = ? AND canonical_url = ?",
                                 (it["source"], it["canonical_url"])).fetchone()
            if known is None:
                conn.execute(
                    "INSERT INTO items (source, tier, url, canonical_url, title, summary, content, published, fetched,"
                    " hn_id, hn_points, hn_comments, hn_top_comments) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (it["source"], it["tier"], it["url"], it["canonical_url"], it["title"], it.get("summary", ""),
                     it.get("content", ""), it["published"], it["fetched"], it.get("hn_id"), it.get("hn_points", 0),
                     it.get("hn_comments", 0), json.dumps(it.get("hn_top_comments", []), ensure_ascii=False)))
                new += 1
            else:
                conn.execute(
                    "UPDATE items SET title = ?, hn_points = MAX(hn_points, ?), hn_comments = MAX(hn_comments, ?)"
                    " WHERE id = ?", (it["title"], it.get("hn_points", 0), it.get("hn_comments", 0), known["id"]))
                conn.execute("UPDATE items SET content = ? WHERE id = ? AND length(content) < length(?)",
                             (it.get("content", ""), known["id"], it.get("content", "")))
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
