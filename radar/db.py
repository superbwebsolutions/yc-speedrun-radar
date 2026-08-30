"""
Persistent state — the "memory" that makes the bot stateful.

SQLite (stdlib, zero setup) with three tables:

  companies  — every company we've ever seen in either directory.
               This is what lets us diff "new" against "already known"
               so we never re-alert on the same listing.

  signals    — every social post we've ever harvested (X + LinkedIn),
               keyed by (platform, external_id) so a tweet that keeps
               showing up in searches is alerted exactly once.

  alerts     — what we actually sent to Slack and when (audit trail +
               makes `status` command meaningful).

  meta       — small key/value store: cached YC Algolia key, last-poll
               timestamps per source, cold-start flag.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Company, SocialPost

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    source        TEXT NOT NULL,
    slug          TEXT NOT NULL,
    name          TEXT NOT NULL,
    batch         TEXT,
    one_liner     TEXT,
    url           TEXT,
    raw           TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (source, slug)
);

CREATE TABLE IF NOT EXISTS signals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    platform       TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    author_name    TEXT,
    author_handle  TEXT,
    author_url     TEXT,
    author_bio     TEXT,
    text           TEXT,
    post_url       TEXT,
    created_at     TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    first_seen_at  TEXT NOT NULL,
    UNIQUE (platform, external_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,      -- new_company | early_signal | test
    ref     TEXT NOT NULL,      -- company slug or signal external_id
    slack_ts TEXT,
    ok      INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> str:
    """ISO-8601 UTC timestamp — one canonical format everywhere."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the DB (creating parent dirs), enable WAL, apply schema."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------ meta ---

def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# -------------------------------------------------------------- companies ---

def known_slugs(conn: sqlite3.Connection, source: str) -> set[str]:
    rows = conn.execute("SELECT slug FROM companies WHERE source = ?", (source,))
    return {r["slug"] for r in rows}


def upsert_company(conn: sqlite3.Connection, c: Company) -> bool:
    """Insert a company if new. Returns True if it was NOT known before."""
    now = utcnow()
    known = conn.execute(
        "SELECT 1 FROM companies WHERE source=? AND slug=?", (c.source, c.slug)
    ).fetchone()
    if known:
        conn.execute(
            "UPDATE companies SET last_seen_at=? WHERE source=? AND slug=?",
            (now, c.source, c.slug),
        )
        conn.commit()
        return False
    conn.execute(
        "INSERT INTO companies "
        "(source, slug, name, batch, one_liner, url, raw, first_seen_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (c.source, c.slug, c.name, c.batch, c.one_liner, c.url,
         json.dumps(c.raw, default=str), now, now),
    )
    conn.commit()
    return True


# --------------------------------------------------------------- signals ---

def insert_signal(conn: sqlite3.Connection, p: SocialPost) -> bool:
    """Insert a social post if new. Returns True if it was NOT known before."""
    try:
        conn.execute(
            "INSERT INTO signals (platform, external_id, author_name, author_handle,"
            " author_url, author_bio, text, post_url, created_at, first_seen_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (p.platform, p.external_id, p.author_name, p.author_handle,
             p.author_url, p.author_bio, p.text, p.post_url, p.created_at, utcnow()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # already seen this exact post — never re-alert


def signal_exists(conn: sqlite3.Connection, platform: str, external_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM signals WHERE platform=? AND external_id=?",
        (platform, external_id),
    ).fetchone()
    return row is not None


def update_signal_status(conn: sqlite3.Connection, external_id: str, status: str) -> None:
    conn.execute("UPDATE signals SET status=? WHERE external_id=?", (status, external_id))
    conn.commit()


# ---------------------------------------------------------------- alerts ---

def record_alert(conn: sqlite3.Connection, kind: str, ref: str,
                 slack_ts: str | None, ok: bool) -> None:
    conn.execute(
        "INSERT INTO alerts (kind, ref, slack_ts, ok, sent_at) VALUES (?,?,?,?,?)",
        (kind, ref, slack_ts, int(ok), utcnow()),
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "companies_total": one("SELECT COUNT(*) FROM companies"),
        "companies_yc": one("SELECT COUNT(*) FROM companies WHERE source='yc_directory'"),
        "companies_speedrun": one("SELECT COUNT(*) FROM companies WHERE source='speedrun'"),
        "signals_seen": one("SELECT COUNT(*) FROM signals"),
        "signals_early": one("SELECT COUNT(*) FROM signals WHERE status='early'"),
        "alerts_sent": one("SELECT COUNT(*) FROM alerts WHERE ok=1"),
    }
