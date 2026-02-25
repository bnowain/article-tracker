#!/usr/bin/env python3
"""
News Aggregator Web Interface

Card-based news reader with category tabs, source filtering,
auto-refresh, and full-text search.

Usage:
    python web.py                    # Start on port 5000
    python web.py --port 8080        # Custom port
    python web.py --host 0.0.0.0     # Listen on all interfaces
"""

import argparse
import json
from pathlib import Path
from markupsafe import Markup

from flask import Flask, request, jsonify, Response

from archiver.config import Config
from archiver.database import Database

app = Flask(__name__)
config = Config("config.json")
db = Database(config.database_path)

CATEGORIES = {
    "progressive": {"label": "Progressive", "color": "#2d7d46", "icon": "✊"},
    "mainstream_national": {"label": "Mainstream", "color": "#2d5a8e", "icon": "📰"},
    "california_state": {"label": "California", "color": "#b8860b", "icon": "🐻"},
    "ca02_local": {"label": "CA-02 Local", "color": "#c45a2c", "icon": "📍"},
    "north_state": {"label": "North State", "color": "#8b2252", "icon": "🏔"},
}


def _render(content_html, **ctx):
    """Render the full page with base layout."""
    stats = db.get_stats()
    cat_counts = {c["category"]: c["count"] for c in db.get_categories_with_counts()}
    active_cat = ctx.get("active_category", "")
    active_source = ctx.get("active_source", "")
    search_q = ctx.get("search_q", "")
    page_title = ctx.get("page_title", "News Aggregator")
    auto_refresh = ctx.get("auto_refresh", False)
    sources_list = db.get_sources_with_counts()

    cat_tabs = ""
    total = stats["total_articles"]
    all_class = "active" if not active_cat and not active_source and not search_q else ""
    cat_tabs += f'<a href="/" class="tab {all_class}">All <span class="count">{total}</span></a>'
    for key, info in CATEGORIES.items():
        cnt = cat_counts.get(key, 0)
        if cnt == 0:
            continue
        cls = "active" if active_cat == key else ""
        cat_tabs += f'<a href="/category/{key}" class="tab {cls}" style="--cat-color:{info["color"]}">{info["icon"]} {info["label"]} <span class="count">{cnt}</span></a>'

    source_options = ""
    for s in sources_list:
        sel = "selected" if s["source_slug"] == active_source else ""
        source_options += f'<option value="{s["source_slug"]}" {sel}>{s["source_name"]} ({s["count"]})</option>'

    refresh_checked = "checked" if auto_refresh else ""
    newest_ts = stats.get("newest") or ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<style>
:root {{
  --bg: #f0eeeb; --card-bg: #fff; --text: #1a1a1a; --muted: #6b6b6b;
  --border: #ddd; --link: #2d5a8e; --accent: #b34233;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
       background: var(--bg); color: var(--text); line-height: 1.5; }}
a {{ color: var(--link); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}

/* ── Top Bar ── */
.topbar {{ background: #1a1a1a; color: #fff; padding: 0.75rem 1.5rem;
           display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; }}
.topbar h1 {{ font-size: 1.1rem; font-weight: 700; letter-spacing: -0.02em; white-space: nowrap; }}
.topbar h1 span {{ color: var(--accent); }}

/* ── Tabs ── */
.tabs {{ background: #fff; border-bottom: 1px solid var(--border); padding: 0 1.5rem;
         display: flex; gap: 0; overflow-x: auto; -webkit-overflow-scrolling: touch; }}
.tab {{ padding: 0.75rem 1rem; font-size: 0.85rem; font-weight: 500; color: var(--muted);
        border-bottom: 2px solid transparent; white-space: nowrap; text-decoration: none; transition: all 0.15s; }}
.tab:hover {{ color: var(--text); background: #f8f8f8; text-decoration: none; }}
.tab.active {{ color: var(--text); border-bottom-color: var(--cat-color, var(--accent)); font-weight: 600; }}
.tab .count {{ font-size: 0.75rem; color: var(--muted); margin-left: 0.25rem; }}

/* ── Controls ── */
.controls {{ padding: 0.75rem 1.5rem; background: #fff; border-bottom: 1px solid var(--border);
             display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }}
.controls select, .controls input[type=text] {{
  padding: 0.4rem 0.75rem; border: 1px solid var(--border); border-radius: 6px;
  font-size: 0.85rem; background: #fff; }}
.controls select:focus, .controls input:focus {{ outline: none; border-color: var(--link); }}
.controls input[type=text] {{ width: 250px; }}
.controls button {{ padding: 0.4rem 1rem; border: 1px solid var(--border); border-radius: 6px;
  font-size: 0.85rem; background: #fff; cursor: pointer; }}
.controls button:hover {{ background: #f5f5f5; }}
.controls .spacer {{ flex: 1; }}
.refresh-toggle {{ display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--muted); }}
.refresh-toggle input {{ cursor: pointer; }}
#refresh-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                background: #ccc; transition: background 0.3s; }}
#refresh-dot.active {{ background: #2d7d46; animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:0.4; }} }}

/* ── Card Grid ── */
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
         gap: 1.25rem; padding: 1.5rem; max-width: 1600px; margin: 0 auto; }}
.card {{ background: var(--card-bg); border-radius: 10px; overflow: hidden;
         box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
         transition: box-shadow 0.2s, transform 0.2s; display: flex; flex-direction: column; }}
.card:hover {{ box-shadow: 0 4px 12px rgba(0,0,0,0.1); transform: translateY(-2px); }}
.card-new {{ animation: fadeIn 0.5s ease; }}
@keyframes fadeIn {{ from {{ opacity:0; transform:translateY(-10px); }} to {{ opacity:1; transform:translateY(0); }} }}

.card-img {{ width: 100%; aspect-ratio: 16/9; object-fit: cover; background: #e8e5e0; display: block; }}
.card-img-placeholder {{ width: 100%; aspect-ratio: 16/9; background: linear-gradient(135deg, #e8e5e0 0%, #d5d0c8 100%);
                         display: flex; align-items: center; justify-content: center; font-size: 2rem; opacity: 0.4; }}
.card-body {{ padding: 1rem 1.25rem 1.25rem; flex: 1; display: flex; flex-direction: column; }}
.card-meta {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem; flex-wrap: wrap; }}
.card-badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.7rem;
               font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; color: #fff; }}
.card-source {{ font-size: 0.8rem; font-weight: 600; color: var(--muted); }}
.card-date {{ font-size: 0.75rem; color: #999; margin-left: auto; white-space: nowrap;
              font-variant-numeric: tabular-nums; }}
.card h2 {{ font-size: 1.05rem; font-weight: 700; line-height: 1.35; margin-bottom: 0.5rem;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
.card h2 a {{ color: var(--text); }}
.card h2 a:hover {{ color: var(--link); text-decoration: none; }}
.card-desc {{ font-size: 0.85rem; color: var(--muted); line-height: 1.55; flex: 1;
              display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
.card-footer {{ margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid #f0f0f0;
                display: flex; justify-content: space-between; align-items: center; }}
.card-footer a {{ font-size: 0.8rem; font-weight: 500; }}
.card-byline {{ font-size: 0.75rem; color: #999; }}
.text-badge {{ font-size: 0.65rem; padding: 0.1rem 0.4rem; border-radius: 3px; font-weight: 600; }}
.text-badge.full {{ background: #e8f5e9; color: #2e7d32; }}
.text-badge.preview {{ background: #fff3e0; color: #e65100; }}
.card-img-link {{ display: block; }}

/* ── Article Page ── */
.article-page {{ max-width: 800px; margin: 0 auto; padding: 2rem 1.5rem; }}
.article-back {{ font-size: 0.85rem; margin-bottom: 1.5rem; display: inline-block; }}
.article-header {{ margin-bottom: 2rem; }}
.article-header h1 {{ font-size: 1.8rem; font-weight: 800; line-height: 1.3; margin-bottom: 0.75rem; }}
.article-meta {{ display: flex; gap: 1rem; align-items: center; flex-wrap: wrap;
                 font-size: 0.85rem; color: var(--muted); margin-bottom: 1rem; }}
.article-meta .card-badge {{ font-size: 0.75rem; }}
.article-hero {{ width: 100%; border-radius: 8px; margin-bottom: 2rem; }}
.article-body {{ font-size: 1.05rem; line-height: 1.8; color: #222; }}
.article-body p {{ margin-bottom: 1.2em; }}
.article-body h2, .article-body h3, .article-body h4 {{ margin: 1.5em 0 0.5em; font-weight: 700; line-height: 1.3; }}
.article-body h2 {{ font-size: 1.4rem; }}
.article-body h3 {{ font-size: 1.2rem; }}
.article-body ul, .article-body ol {{ margin: 0 0 1.2em 1.5em; }}
.article-body li {{ margin-bottom: 0.4em; }}
.article-body blockquote {{ border-left: 3px solid var(--accent); margin: 1.5em 0; padding: 0.5em 1.25em; color: #555; font-style: italic; }}
.article-body a {{ color: var(--link); text-decoration: underline; }}
.article-body a:hover {{ color: var(--accent); }}
.article-body img {{ max-width: 100%; height: auto; border-radius: 6px; margin: 1em 0; display: block; }}
.article-body figure {{ margin: 1.5em 0; }}
.article-body figcaption {{ font-size: 0.85rem; color: var(--muted); margin-top: 0.4em; text-align: center; }}
.article-body table {{ width: 100%; border-collapse: collapse; margin: 1.5em 0; font-size: 0.9rem; }}
.article-body th, .article-body td {{ border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }}
.article-body th {{ background: #f5f5f5; font-weight: 600; }}
.article-body iframe {{ max-width: 100%; border-radius: 6px; margin: 1em 0; }}
.embed-wrapper {{ position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; margin: 1.5em 0; border-radius: 6px; }}
.embed-wrapper iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: 0; }}
.article-preview-notice {{ background: #fff3e0; border: 1px solid #ffe0b2; border-radius: 8px;
                           padding: 1rem 1.25rem; margin-bottom: 1.5rem; font-size: 0.9rem; color: #e65100; }}
.article-preview-notice a {{ color: #e65100; font-weight: 600; }}
.article-links {{ margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid var(--border);
                  display: flex; gap: 1rem; font-size: 0.9rem; }}

/* ── Pagination ── */
.pagination {{ display: flex; justify-content: center; gap: 0.5rem; padding: 2rem 1.5rem; }}
.pagination a, .pagination span {{ padding: 0.5rem 1rem; border: 1px solid var(--border);
  border-radius: 6px; font-size: 0.85rem; background: #fff; }}
.pagination span.current {{ background: var(--link); color: #fff; border-color: var(--link); }}
.pagination a:hover {{ background: #f5f5f5; text-decoration: none; }}

/* ── Empty State ── */
.empty {{ text-align: center; padding: 4rem 2rem; color: var(--muted); }}
.empty h2 {{ font-size: 1.3rem; margin-bottom: 0.5rem; }}
.empty p {{ font-size: 0.95rem; }}

/* ── Notification Banner ── */
#new-banner {{ display: none; position: fixed; top: 0; left: 50%; transform: translateX(-50%);
               z-index: 100; background: var(--link); color: #fff; padding: 0.6rem 1.5rem;
               border-radius: 0 0 8px 8px; font-size: 0.85rem; font-weight: 500;
               cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }}
#new-banner:hover {{ background: #1e4a6e; }}

/* ── Responsive ── */
@media (max-width: 768px) {{
  .grid {{ grid-template-columns: 1fr; padding: 1rem; gap: 1rem; }}
  .controls input[type=text] {{ width: 100%; }}
  .tabs {{ padding: 0 0.75rem; }}
}}
</style>
</head>
<body>

<div class="topbar">
  <h1>📡 News <span>Aggregator</span></h1>
</div>

<div class="tabs">
  {cat_tabs}
</div>

<div class="controls">
  <form method="get" action="/search" style="display:flex;gap:0.5rem;align-items:center;">
    <input type="text" name="q" placeholder="Search articles…" value="{search_q}">
    <button type="submit">Search</button>
  </form>
  <select onchange="if(this.value) window.location='/source/'+this.value; else window.location='/';">
    <option value="">All sources</option>
    {source_options}
  </select>
  <div class="spacer"></div>
  <label class="refresh-toggle">
    <input type="checkbox" id="auto-refresh-toggle" {refresh_checked}>
    <span id="refresh-dot"></span> Auto-refresh
  </label>
</div>

<div id="new-banner" onclick="window.scrollTo({{top:0,behavior:'smooth'}});location.reload();">
  ↑ New articles available — click to refresh
</div>

{content_html}

<input type="hidden" id="newest-ts" value="{newest_ts}">
<input type="hidden" id="current-category" value="{active_cat}">
<input type="hidden" id="current-source" value="{active_source}">

<script>
// Auto-refresh via polling
let refreshInterval = null;
const toggle = document.getElementById('auto-refresh-toggle');
const dot = document.getElementById('refresh-dot');
const banner = document.getElementById('new-banner');

function startRefresh() {{
  dot.classList.add('active');
  refreshInterval = setInterval(checkForNew, 120000); // 2 minutes
}}
function stopRefresh() {{
  dot.classList.remove('active');
  if (refreshInterval) clearInterval(refreshInterval);
}}

toggle.addEventListener('change', function() {{
  if (this.checked) startRefresh(); else stopRefresh();
  localStorage.setItem('autoRefresh', this.checked);
}});

// Restore preference
if (localStorage.getItem('autoRefresh') === 'true') {{
  toggle.checked = true;
  startRefresh();
}}

async function checkForNew() {{
  const ts = document.getElementById('newest-ts').value;
  const cat = document.getElementById('current-category').value;
  const src = document.getElementById('current-source').value;
  if (!ts) return;
  let url = `/api/new-count?after=${{encodeURIComponent(ts)}}`;
  if (cat) url += `&category=${{cat}}`;
  if (src) url += `&source=${{src}}`;
  try {{
    const resp = await fetch(url);
    const data = await resp.json();
    if (data.count > 0) {{
      banner.textContent = `↑ ${{data.count}} new article${{data.count>1?'s':''}} — click to refresh`;
      banner.style.display = 'block';
    }}
  }} catch(e) {{}}
}}

// Image error fallback
document.addEventListener('DOMContentLoaded', () => {{
  document.querySelectorAll('.card-img').forEach(img => {{
    img.onerror = function() {{
      const ph = document.createElement('div');
      ph.className = 'card-img-placeholder';
      ph.textContent = '📰';
      this.replaceWith(ph);
    }};
  }});
}});
</script>
</body>
</html>"""


def _card_html(article: dict) -> str:
    """Render a single article card."""
    cat = article.get("category", "")
    cat_info = CATEGORIES.get(cat, {"label": cat, "color": "#888", "icon": ""})
    aid = article.get("id", 0)

    # Image — remote URL only, no local
    img_url = article.get("preview_image_url", "")
    if img_url:
        img_html = f'<img class="card-img" src="{img_url}" alt="" loading="lazy" referrerpolicy="no-referrer">'
    else:
        img_html = '<div class="card-img-placeholder">📰</div>'

    # Date formatting
    pub = article.get("publish_date") or article.get("discovered_at") or ""
    display_date = ""
    if pub:
        try:
            from dateutil import parser as dp
            dt = dp.parse(pub)
            display_date = dt.strftime("%b %d, %Y · %I:%M %p")
        except Exception:
            display_date = pub[:16]

    headline = article.get("headline") or article.get("url", "")
    desc = article.get("description", "") or ""
    byline = article.get("byline", "")
    source_name = article.get("source_name", "")
    url = article.get("url", "")
    has_text = bool(article.get("article_text", "").strip())

    # Badge showing if full text is available
    text_badge = '<span class="text-badge full">Full text</span>' if has_text else '<span class="text-badge preview">Preview only</span>'

    return f"""<article class="card">
  <a href="/article/{aid}" class="card-img-link">{img_html}</a>
  <div class="card-body">
    <div class="card-meta">
      <span class="card-badge" style="background:{cat_info['color']}">{cat_info['label']}</span>
      <span class="card-source">{source_name}</span>
      <span class="card-date">{display_date}</span>
    </div>
    <h2><a href="/article/{aid}">{headline}</a></h2>
    <p class="card-desc">{desc}</p>
    <div class="card-footer">
      <a href="{url}" target="_blank" rel="noopener">Original ↗</a>
      {text_badge}
      <span class="card-byline">{byline}</span>
    </div>
  </div>
</article>"""


def _cards_grid(articles: list[dict], page: int = 1, total: int = 0,
                base_url: str = "/", per_page: int = 60) -> str:
    """Render a grid of cards with pagination."""
    if not articles:
        return """<div class="empty"><h2>No articles yet</h2>
        <p>Run <code>python run.py</code> to start fetching feeds.</p></div>"""

    cards = "\n".join(_card_html(a) for a in articles)
    grid = f'<div class="grid">{cards}</div>'

    # Pagination
    total_pages = max(1, (total + per_page - 1) // per_page)
    if total_pages > 1:
        sep = "&" if "?" in base_url else "?"
        pages = '<div class="pagination">'
        if page > 1:
            pages += f'<a href="{base_url}{sep}page={page-1}">← Prev</a>'
        for p in range(max(1, page-3), min(total_pages+1, page+4)):
            if p == page:
                pages += f'<span class="current">{p}</span>'
            else:
                pages += f'<a href="{base_url}{sep}page={p}">{p}</a>'
        if page < total_pages:
            pages += f'<a href="{base_url}{sep}page={page+1}">Next →</a>'
        pages += '</div>'
        grid += pages

    return grid


# ── Routes ────────────────────────────────────────────────────────────

PER_PAGE = 60

@app.route("/")
def index():
    page = int(request.args.get("page", 1))
    offset = (page - 1) * PER_PAGE
    articles = db.get_articles(limit=PER_PAGE, offset=offset)
    total = db.count_articles()
    content = _cards_grid(articles, page, total, "/")
    return _render(content, page_title="News Aggregator")


@app.route("/category/<cat>")
def by_category(cat):
    page = int(request.args.get("page", 1))
    offset = (page - 1) * PER_PAGE
    articles = db.get_articles(category=cat, limit=PER_PAGE, offset=offset)
    total = db.count_articles(category=cat)
    label = CATEGORIES.get(cat, {}).get("label", cat)
    content = _cards_grid(articles, page, total, f"/category/{cat}")
    return _render(content, active_category=cat, page_title=f"{label} — News Aggregator")


@app.route("/source/<slug>")
def by_source(slug):
    page = int(request.args.get("page", 1))
    offset = (page - 1) * PER_PAGE
    articles = db.get_articles(source=slug, limit=PER_PAGE, offset=offset)
    total = db.count_articles(source=slug)
    name = articles[0]["source_name"] if articles else slug
    content = _cards_grid(articles, page, total, f"/source/{slug}")
    return _render(content, active_source=slug, page_title=f"{name} — News Aggregator")


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return _render('<div class="empty"><h2>Enter a search term</h2></div>',
                       page_title="Search — News Aggregator", search_q=q)
    try:
        articles = db.search(q)
    except Exception:
        articles = []
    content = _cards_grid(articles, total=len(articles))
    return _render(content, search_q=q, page_title=f'"{q}" — Search')


@app.route("/article/<int:aid>")
def article_detail(aid):
    article = db.get_article(aid)
    if not article:
        return _render('<div class="empty"><h2>Article not found</h2></div>',
                       page_title="Not Found"), 404

    cat = article.get("category", "")
    cat_info = CATEGORIES.get(cat, {"label": cat, "color": "#888", "icon": ""})
    headline = article.get("headline") or "Untitled"
    url = article.get("url", "")
    byline = article.get("byline", "")
    source_name = article.get("source_name", "")
    img_url = article.get("preview_image_url", "")
    article_text = article.get("article_text", "").strip()
    desc = article.get("description", "")

    pub = article.get("publish_date") or article.get("discovered_at") or ""
    display_date = ""
    if pub:
        try:
            from dateutil import parser as dp
            dt = dp.parse(pub)
            display_date = dt.strftime("%B %d, %Y · %I:%M %p")
        except Exception:
            display_date = pub[:16]

    # Hero image
    hero_html = ""
    if img_url:
        hero_html = f'<img class="article-hero" src="{img_url}" alt="" referrerpolicy="no-referrer">'

    # Article body — render HTML or fall back to plain text paragraphs
    if article_text:
        is_html = '<p>' in article_text or '<div' in article_text or '<img' in article_text
        if is_html:
            # Wrap YouTube/Vimeo iframes in responsive embed-wrapper
            import re as _re
            def _wrap_iframe(m):
                src = m.group(0)
                if 'youtube' in src or 'youtu.be' in src or 'vimeo' in src:
                    return f'<div class="embed-wrapper">{src}</div>'
                return src
            article_text = _re.sub(r'<iframe[^>]*>.*?</iframe>', _wrap_iframe,
                                   article_text, flags=_re.DOTALL | _re.IGNORECASE)
            body_html = Markup(article_text)
        else:
            # Legacy plain text — split on double newlines
            paragraphs = article_text.split("\n\n")
            body_html = Markup("".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip()))
        notice_html = ""
    else:
        # Preview only — show description
        body_html = Markup(f"<p>{desc}</p>") if desc else Markup("<p><em>No article text available.</em></p>")
        notice_html = f"""<div class="article-preview-notice">
            ⚠ Full article text was not available (likely paywalled).
            <a href="{url}" target="_blank" rel="noopener">Read the full article on {source_name} ↗</a>
        </div>"""

    byline_html = f"<strong>{byline}</strong> · " if byline else ""

    content = f"""<div class="article-page">
  <a href="javascript:history.back()" class="article-back">← Back to feed</a>
  <div class="article-header">
    <h1>{headline}</h1>
    <div class="article-meta">
      <span class="card-badge" style="background:{cat_info['color']}">{cat_info['label']}</span>
      <span>{byline_html}{source_name}</span>
      <span>{display_date}</span>
    </div>
  </div>
  {hero_html}
  {notice_html}
  <div class="article-body">
    {body_html}
  </div>
  <div class="article-links">
    <a href="{url}" target="_blank" rel="noopener">Read on {source_name} ↗</a>
  </div>
</div>"""

    return _render(content, page_title=f"{headline} — News Aggregator")


# ── API ───────────────────────────────────────────────────────────────

@app.route("/api/new-count")
def api_new_count():
    after = request.args.get("after", "")
    cat = request.args.get("category", "") or None
    src = request.args.get("source", "") or None
    if not after:
        return jsonify({"count": 0})
    articles = db.get_articles(category=cat, source=src, after=after, limit=100)
    return jsonify({"count": len(articles)})


@app.route("/api/articles")
def api_articles():
    cat = request.args.get("category", "") or None
    src = request.args.get("source", "") or None
    after = request.args.get("after", "") or None
    limit = min(int(request.args.get("limit", 60)), 200)
    articles = db.get_articles(category=cat, source=src, after=after, limit=limit)
    return jsonify(articles)


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.route("/api/system/shutdown", methods=["POST"])
def api_shutdown():
    import threading, os, logging
    logging.getLogger(__name__).info("Shutdown requested, exiting in 500ms")
    def _exit_soon():
        import time
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_exit_soon, daemon=True).start()
    return jsonify({"status": "shutting_down", "killed": []})


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    config = Config(args.config)
    db = Database(config.database_path)

    print(f"\n  📡 News Aggregator — http://{args.host}:{args.port}")
    print(f"  Database: {config.database_path}\n")
    app.run(host=args.host, port=args.port, debug=True)
