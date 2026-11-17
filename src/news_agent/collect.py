"""Feed collection: RSS/Atom + Hacker News (Algolia), URL canonicalisation, gzip/BOM tolerant fetching."""

import calendar
import gzip
import html
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
HN_API = "https://hn.algolia.com/api/v1"
TRACKING_PARAMS = {"ref", "ref_src", "fbclid", "gclid", "mc_cid", "mc_eid"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_url(url: str) -> str:
    """https, lower-case host without www., no tracking params / fragment / trailing slash, sorted query."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS)
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(query), ""))


def clean_text(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def decode_body(data: bytes) -> bytes:
    """Undo a bare gzip body (served without Content-Encoding) and drop a UTF-8 BOM."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.removeprefix(b"\xef\xbb\xbf")


def fetch(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return decode_body(resp.read())


def fetch_json(url: str, timeout: float = 20):
    return json.loads(fetch(url, timeout))


def parse_feed(feed: dict, data: bytes, fetched: str) -> list[dict]:
    parsed = feedparser.parse(decode_body(data))
    items = []
    for e in parsed.entries:
        link, title = e.get("link"), clean_text(e.get("title"))
        if not link or not title:
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        published = to_iso(calendar.timegm(stamp)) if stamp else fetched
        summary = e.get("summary") or (e.get("content") or [{}])[0].get("value", "")
        items.append({"source": feed["id"], "tier": feed["tier"], "url": link, "canonical_url": canonical_url(link),
                      "title": title, "summary": clean_text(summary)[:3000],
                      "published": min(published, fetched), "fetched": fetched})
    return items


def hn_comments(item: dict, top_n: int = 8, max_chars: int = 300) -> list[str]:
    """Top-level comments in HN display order, each with its first reply ("↳ ...")."""
    out = []
    for c in item.get("children") or []:
        if not c.get("text"):
            continue
        out.append(f"{c.get('author') or '?'}: {clean_text(c['text'])[:max_chars]}")
        reply = next((r for r in c.get("children") or [] if r.get("text")), None)
        if reply:
            out.append(f"↳ {reply.get('author') or '?'}: {clean_text(reply['text'])[:max_chars]}")
        if sum(not line.startswith("↳") for line in out) >= top_n:
            break
    return out


def collect_hn(cfg: dict, window_hours: int, timeout: float, fetched: str) -> list[dict]:
    since = int(time.time()) - window_hours * 3600
    hits = fetch_json(f"{HN_API}/search?tags=front_page&hitsPerPage={cfg['front_page_hits']}", timeout)["hits"]
    hits += fetch_json(f"{HN_API}/search_by_date?tags=story&hitsPerPage=100"
                       f"&numericFilters=points>={cfg['min_points']},created_at_i>{since}", timeout)["hits"]
    stories = {}
    for h in hits:
        if h.get("title") and h["objectID"] not in stories:
            stories[h["objectID"]] = h
    hot = sorted(stories.values(), key=lambda h: h.get("points") or 0, reverse=True)[:cfg["comment_posts"]]
    hot_ids = {h["objectID"] for h in hot}
    items = []
    for h in stories.values():
        discussion = f"https://news.ycombinator.com/item?id={h['objectID']}"
        url = h.get("url") or discussion
        comments = []
        if h["objectID"] in hot_ids:
            try:
                comments = hn_comments(fetch_json(f"{HN_API}/items/{h['objectID']}", timeout), cfg["top_comments"])
            except Exception:
                pass
        items.append({"source": "hacker_news", "tier": 3, "url": url, "canonical_url": canonical_url(url),
                      "title": clean_text(h["title"]), "summary": clean_text(h.get("story_text"))[:1500],
                      "published": min(to_iso(h["created_at_i"]), fetched), "fetched": fetched,
                      "hn_id": int(h["objectID"]), "hn_points": h.get("points") or 0,
                      "hn_comments": h.get("num_comments") or 0, "hn_top_comments": comments})
    return items


def collect_all(feeds: list[dict], cfg: dict) -> tuple[list[dict], dict[str, str]]:
    """Fetch every feed (and HN) concurrently. Returns (items, {source_id: error})."""
    fetched, timeout = now_iso(), cfg["collect"]["timeout_seconds"]
    items, errors = [], {}

    def one(feed):
        return parse_feed(feed, fetch(feed["url"], timeout), fetched)

    with ThreadPoolExecutor(cfg["collect"]["workers"]) as pool:
        futures = {f["id"]: pool.submit(one, f) for f in feeds}
        if cfg["hn"]["enabled"]:
            futures["hacker_news"] = pool.submit(collect_hn, cfg["hn"], cfg["collect"]["window_hours"], timeout, fetched)
        for source, fut in futures.items():
            try:
                got = fut.result()
                items += got
                if not got:
                    errors[source] = "no entries"
            except Exception as e:
                errors[source] = f"{type(e).__name__}: {e}"
    return items, errors


def fulltext(url: str, max_chars: int, timeout: float = 20) -> str:
    import trafilatura

    try:
        text = trafilatura.extract(fetch(url, timeout).decode("utf-8", "replace"), include_comments=False)
    except Exception:
        return ""
    return clean_text(text)[:max_chars]
