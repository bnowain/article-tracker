"""Database for the news aggregator — SQLite + FTS5."""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class Database:
    def __init__(self, db_path: str = "data/news_archive.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                source_slug TEXT NOT NULL,
                source_name TEXT NOT NULL,
                category TEXT DEFAULT '',
                headline TEXT DEFAULT '',
                byline TEXT DEFAULT '',
                description TEXT DEFAULT '',
                article_text TEXT DEFAULT '',
                publish_date TEXT,
                discovered_at TEXT NOT NULL,
                preview_image_url TEXT DEFAULT '',
                preview_image_local TEXT DEFAULT '',
                image_urls TEXT DEFAULT '[]',
                local_image_paths TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                article_section TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_art_source ON articles(source_slug);
            CREATE INDEX IF NOT EXISTS idx_art_cat ON articles(category);
            CREATE INDEX IF NOT EXISTS idx_art_pub ON articles(publish_date DESC);
            CREATE INDEX IF NOT EXISTS idx_art_disc ON articles(discovered_at DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                headline, byline, description, article_text, tags,
                content='articles', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS art_ai AFTER INSERT ON articles BEGIN
                INSERT INTO articles_fts(rowid, headline, byline, description, article_text, tags)
                VALUES (new.id, new.headline, new.byline, new.description, new.article_text, new.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS art_ad AFTER DELETE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, headline, byline, description, article_text, tags)
                VALUES ('delete', old.id, old.headline, old.byline, old.description, old.article_text, old.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS art_au AFTER UPDATE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, headline, byline, description, article_text, tags)
                VALUES ('delete', old.id, old.headline, old.byline, old.description, old.article_text, old.tags);
                INSERT INTO articles_fts(rowid, headline, byline, description, article_text, tags)
                VALUES (new.id, new.headline, new.byline, new.description, new.article_text, new.tags);
            END;

            CREATE TABLE IF NOT EXISTS source_checks (
                source_slug TEXT PRIMARY KEY,
                last_checked TEXT NOT NULL,
                articles_found INTEGER DEFAULT 0
            );
        """)
        self.conn.commit()

    # ── Write ─────────────────────────────────────────────────────────

    def add_article(self, **kwargs) -> Optional[int]:
        now = datetime.now(timezone.utc).isoformat()
        kwargs.setdefault("discovered_at", now)
        for key in ("image_urls", "local_image_paths", "tags"):
            v = kwargs.get(key)
            if isinstance(v, (list, tuple)):
                kwargs[key] = json.dumps(v)
        cols = ", ".join(kwargs.keys())
        ph = ", ".join(["?"] * len(kwargs))
        try:
            cur = self.conn.execute(
                f"INSERT OR IGNORE INTO articles ({cols}) VALUES ({ph})",
                list(kwargs.values()),
            )
            self.conn.commit()
            return cur.lastrowid if cur.rowcount > 0 else None
        except sqlite3.Error:
            return None

    def set_last_check(self, slug: str, ts: Optional[str] = None):
        ts = ts or datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO source_checks (source_slug, last_checked)
               VALUES (?, ?) ON CONFLICT(source_slug)
               DO UPDATE SET last_checked=excluded.last_checked""",
            (slug, ts),
        )
        self.conn.commit()

    # ── Read ──────────────────────────────────────────────────────────

    def get_articles(self, category=None, source=None, limit=60, offset=0, after=None):
        w, p = [], []
        if category:
            w.append("category = ?"); p.append(category)
        if source:
            w.append("source_slug = ?"); p.append(source)
        if after:
            w.append("(COALESCE(publish_date, discovered_at) > ?)")
            p.append(after)
        clause = ("WHERE " + " AND ".join(w)) if w else ""
        rows = self.conn.execute(
            f"SELECT * FROM articles {clause} ORDER BY COALESCE(publish_date, discovered_at) DESC LIMIT ? OFFSET ?",
            p + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def count_articles(self, category=None, source=None):
        w, p = [], []
        if category:
            w.append("category = ?"); p.append(category)
        if source:
            w.append("source_slug = ?"); p.append(source)
        clause = ("WHERE " + " AND ".join(w)) if w else ""
        return self.conn.execute(f"SELECT COUNT(*) FROM articles {clause}", p).fetchone()[0]

    def get_article(self, aid: int):
        r = self.conn.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
        return dict(r) if r else None

    def get_article_by_url(self, url: str):
        r = self.conn.execute("SELECT * FROM articles WHERE url = ?", (url,)).fetchone()
        return dict(r) if r else None

    def url_exists(self, url: str) -> bool:
        return self.conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,)).fetchone() is not None

    def search(self, query, category=None, source=None, limit=60):
        w, p = ["articles_fts MATCH ?"], [query]
        if category:
            w.append("a.category = ?"); p.append(category)
        if source:
            w.append("a.source_slug = ?"); p.append(source)
        rows = self.conn.execute(
            f"""SELECT a.* FROM articles a JOIN articles_fts ON articles_fts.rowid=a.id
                WHERE {' AND '.join(w)} ORDER BY rank LIMIT ?""",
            p + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sources_with_counts(self):
        rows = self.conn.execute("""
            SELECT source_slug, source_name, category, COUNT(*) as count,
                   MAX(COALESCE(publish_date, discovered_at)) as latest
            FROM articles GROUP BY source_slug ORDER BY category, source_name
        """).fetchall()
        return [dict(r) for r in rows]

    def get_categories_with_counts(self):
        rows = self.conn.execute(
            "SELECT category, COUNT(*) as count FROM articles GROUP BY category ORDER BY category"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_last_check(self, slug: str):
        r = self.conn.execute("SELECT last_checked FROM source_checks WHERE source_slug=?", (slug,)).fetchone()
        return r["last_checked"] if r else None

    def get_newest_timestamp(self):
        r = self.conn.execute("SELECT MAX(COALESCE(publish_date, discovered_at)) as ts FROM articles").fetchone()
        return r["ts"] if r else None

    def get_stats(self):
        total = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        sources = self.conn.execute("SELECT COUNT(DISTINCT source_slug) FROM articles").fetchone()[0]
        return {"total_articles": total, "total_sources": sources, "newest": self.get_newest_timestamp()}

    def close(self):
        self.conn.close()
