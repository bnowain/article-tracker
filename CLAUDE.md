# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python news aggregation system that fetches articles from 45+ configurable sources (RSS, Google News), extracts full-text content with paywall bypass, stores in SQLite with FTS5 search, and serves a Flask web interface.

## Commands

```bash
# Setup
python -m venv venv
source venv/Scripts/activate  # Windows Git Bash
pip install -r requirements.txt

# Fetch articles (single pass)
python run.py

# Fetch single source
python run.py --source <slug>

# Continuous monitoring
python run.py --continuous --interval 30

# Web interface (http://localhost:5000)
python web.py

# Historical backfill
python backfill.py --source <slug> --years 2

# Discover RSS feeds for a new source
python discover_rss.py
```

No test suite exists. `test_playwright.py` is a manual paywall bypass strategy tester, not automated tests.

## Architecture

```
config.json ──→ run.py ──→ archiver/feeds.py ──→ archiver/database.py ──→ data/news_archive.db
                                                                              ↑
                 web.py (Flask) ──────────────────────────────────────────────┘
```

**archiver/config.py** — Loads `config.json`, exposes database path, storage dirs, scraping settings, and per-source configuration via `get_enabled_sites()` / `get_site(slug)`.

**archiver/database.py** — SQLite with WAL journaling. Two tables: `articles` (with UNIQUE url constraint for dedup) and `source_checks`. FTS5 virtual table `articles_fts` with auto-sync triggers on headline, byline, description, article_text, tags.

**archiver/feeds.py** (largest file, ~700 lines) — Core processing engine:
- `process_source()` orchestrates the full pipeline: fetch feeds → deduplicate → enrich with OpenGraph metadata → bypass paywall for full text → download images → store
- `fetch_article_text_via_bypass()` uses a 6-method fallback chain: direct fetch → Google referrer → Facebook referrer → 12ft.io → removepaywalls.com → Playwright
- `extract_article_text()` sanitizes HTML preserving media/structure while stripping ads, scripts, nav, paywalls
- Rate limiting with exponential backoff, random jitter, user-agent rotation

**web.py** — Flask app with routes: `/` (home), `/category/<cat>`, `/source/<slug>`, `/search`, `/article/<id>`, plus JSON API at `/api/new-count`, `/api/articles`, `/api/stats`. Inline CSS/templates, 60 articles per page, 2-minute auto-refresh polling.

**backfill.py** — Historical article fetcher using RSS, Google News date-range searches, Wayback Machine, and sitemaps.

## Database Schema

The `articles` table key columns: `url` (UNIQUE), `source_slug`, `category`, `headline`, `byline`, `description`, `article_text` (sanitized HTML), `publish_date`, `discovered_at`, `preview_image_url`, `preview_image_local`, `tags` (JSON array). Indexed on source_slug, category, publish_date DESC, discovered_at DESC.

Categories in use: `progressive`, `mainstream_national`, `california`, `ca02_local`, `north_state`.

## config.json Structure

Each source entry has: `name`, `slug`, `base_url`, `category`, `enabled`, `rss` (array of feed URLs), optional `bypass_paywall`, `check_interval_minutes`, `selectors` (DOM selectors for article extraction), `article_url_patterns`, `exclude_patterns`. The config also defines global scraping settings (timeouts, rate limits, jitter, anti-detection).

## Key Patterns

- All HTTP requests go through `fetch_with_retry()` with exponential backoff on 429s
- Articles are deduplicated by URL uniqueness constraint — `add_article()` catches IntegrityError silently
- Image filenames are MD5 hashes of their URLs, stored in `data/images/`
- Playwright is optional and only used as last-resort bypass; install separately with `playwright install chromium`
- The web interface uses inline HTML templates (no separate template files)

## Atlas Integration

This project is a spoke in the **Atlas** hub-and-spoke ecosystem. Atlas is a central orchestration hub that routes queries across spoke apps. It lives in a sibling directory (`E:\0-Automated-Apps\Atlas`).

**Rules:**

1. Only modify **this** project by default. Do not modify other spoke projects or Atlas unless explicitly asked.
2. If approved, changes to other projects are allowed — but always propose first and wait for approval.
3. Suggest API endpoint changes in other spokes if they would improve integration, but never write code in another project without explicit approval.
4. This app must remain **independently functional** — it works on its own without Atlas or any other spoke.
5. **No spoke-to-spoke dependencies.** All cross-app communication goes through Atlas.
6. If modifying or removing an API endpoint that Atlas may depend on, **stop and warn** before proceeding.
7. New endpoints added for Atlas integration should be general-purpose and useful standalone, not tightly coupled to Atlas internals.

**Spoke projects** (sibling directories, may be loaded via `--add-dir` for reference):

- **civic_media** — meeting transcription, diarization, voiceprint learning
- **article-tracker** — local news aggregation and monitoring (this project)
- **Shasta-DB** — civic media archive browser and metadata editor (FastAPI/HTMX)
- **Facebook-Offline** — local personal Facebook archive for LLM querying (private, local only)
