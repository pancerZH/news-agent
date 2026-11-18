from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import gzip
import json
import re
import sqlite3

from news_agent import cli, collect, db, embed_cluster
from news_agent.collect import canonical_url, decode_body, fetch, hn_comments, parse_feed

from conftest import fake_encode, rss

FETCHED = "2026-11-16T12:00:00Z"
FEED = {"id": "src", "tier": 1}


def test_canonical_url_strips_tracking_and_normalises():
    variants = ["http://WWW.Example.com/post/?utm_source=x&utm_medium=y#top",
                "https://example.com/post?fbclid=abc&ref=hn",
                "https://www.example.com/post/"]
    assert {canonical_url(u) for u in variants} == {"https://example.com/post"}
    assert canonical_url("https://example.com/a?b=2&a=1") == "https://example.com/a?a=1&b=2"


def test_bare_gzip_and_bom_are_decoded():
    xml = rss([("Title", "https://example.com/x", "desc")]).encode()
    assert decode_body(gzip.compress(b"\xef\xbb\xbf" + xml)) == xml
    items = parse_feed(FEED, gzip.compress(b"\xef\xbb\xbf" + xml), FETCHED)
    assert [i["title"] for i in items] == ["Title"]


def test_fetch_handles_bare_gzip_file(tmp_path):
    path = tmp_path / "feed.xml"
    path.write_bytes(gzip.compress(rss([("Gz", "https://example.com/gz", "d")]).encode()))
    assert b"<rss" in fetch(path.as_uri())


def test_parse_atom_feed_with_html_summary():
    atom = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
    <entry><title>Atom &amp; post</title><link href="https://example.com/a?utm_campaign=z"/>
    <updated>2026-11-15T08:00:00Z</updated><summary type="html">&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</summary>
    </entry></feed>"""
    [item] = parse_feed(FEED, atom, FETCHED)
    assert item["title"] == "Atom & post"
    assert item["summary"] == "Hello world"
    assert item["canonical_url"] == "https://example.com/a"
    assert item["published"] == "2026-11-15T08:00:00Z"


def test_future_dates_are_clamped_to_fetch_time():
    xml = (b"<rss version='2.0'><channel><item><title>t</title><link>https://example.com/t</link>"
           b"<pubDate>Fri, 01 Jan 2100 00:00:00 GMT</pubDate></item></channel></rss>")
    [item] = parse_feed(FEED, xml, FETCHED)
    assert item["published"] == FETCHED


def test_hn_comments_take_top_level_with_first_reply():
    item = {"children": [
        {"author": "a", "text": "<p>First &amp; best</p>", "children": [
            {"author": "r", "text": None}, {"author": "b", "text": "reply one"}]},
        {"author": "dead", "text": None, "children": []},
        {"author": "c", "text": "second", "children": []},
        {"author": "d", "text": "third", "children": []},
    ]}
    assert hn_comments(item, top_n=2) == ["a: First & best", "↳ b: reply one", "c: second"]


def feed_xml(description: str, body: str | None = None) -> bytes:
    content = f"<content:encoded><![CDATA[{body}]]></content:encoded>" if body is not None else ""
    return (f'<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>t</title>'
            f"<item><title>Post</title><link>https://example.com/p</link><description><![CDATA[{description}]]>"
            f"</description>{content}<pubDate>{format_datetime(datetime.now(timezone.utc) - timedelta(hours=2))}</pubDate>"
            "</item></channel></rss>").encode()


BODY = "<p>First paragraph about <b>Rubin</b> GPUs.</p><p>Second paragraph.<br>Third line.</p>" + "<p>more text</p>" * 80


def test_content_encoded_is_kept_with_paragraphs_and_teaser_summary_uses_its_head():
    [item] = parse_feed(FEED, feed_xml("Listen now", BODY), FETCHED)
    assert item["content"].startswith("First paragraph about Rubin GPUs.\nSecond paragraph.\nThird line.\nmore text")
    assert item["summary"].startswith("First paragraph about Rubin GPUs. Second paragraph. Third line. more text")


def test_informative_description_stays_the_summary():
    description = "A real abstract. " * 20
    [item] = parse_feed(FEED, feed_xml(description, BODY), FETCHED)
    assert item["summary"] == description.strip()
    assert item["content"].startswith("First paragraph")


def test_description_holding_the_full_text_becomes_content():
    long = "<p>" + "word " * 1000 + "</p><p>end</p>"
    [item] = parse_feed(FEED, feed_xml(long), FETCHED)
    assert item["content"].endswith("word\nend") and len(item["summary"]) == 3000
    [short] = parse_feed(FEED, feed_xml("Just a teaser"), FETCHED)
    assert short["content"] == "" and short["summary"] == "Just a teaser"
    [shorter_body] = parse_feed(FEED, feed_xml("A teaser, longer than the body", "<p>Body</p>"), FETCHED)
    assert shorter_body["summary"] == "A teaser, longer than the body"


def test_old_database_gains_content_and_known_items_get_it_backfilled(tmp_path):
    path = tmp_path / "news.sqlite"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("    content TEXT NOT NULL DEFAULT '',  -- full text (feed body or fetched); '' when none\n", ""))
    old.execute("INSERT INTO items (source, tier, url, canonical_url, title, summary, published, fetched)"
                " VALUES ('src', 1, 'https://example.com/p', 'https://example.com/p', 'Post', 'teaser', ?, ?)",
                (FETCHED, FETCHED))
    old.commit()
    old.close()
    conn = db.connect(path)
    [item] = parse_feed(FEED, feed_xml("Listen now", BODY), FETCHED)
    assert db.upsert_items(conn, [item]) == 0
    row = conn.execute("SELECT summary, content FROM items").fetchone()
    assert row["summary"] == "teaser" and row["content"] == item["content"]  # summary kept: it was embedded
    assert db.upsert_items(conn, [item | {"content": ""}]) == 0
    assert conn.execute("SELECT content FROM items").fetchone()["content"] == item["content"]


def test_full_text_reaches_packet_marker_and_read_command(project, monkeypatch, capsys):
    monkeypatch.setattr(embed_cluster, "encode", fake_encode)
    monkeypatch.setattr(collect, "fulltext", lambda url, max_chars, timeout: "Fetched article.\n" + "body " * 500)
    (project / "feeds" / "a.xml").write_bytes(feed_xml("Listen now", BODY))
    feeds = (project / "feeds.toml").read_text()
    (project / "feeds.toml").write_text(feeds + "fulltext = true\n")  # feed_b: no body in the feed, fetched
    config = ["--config", str(project / "config.toml")]
    assert cli.main([*config, "prep", "--day", "2026-11-16", "--edition", "am"]) == 0
    packet = (project / "data/packets/2026-11-16-am.md").read_text()
    ids = re.findall(r"全文 [\d,]+ 字（`uv run news-agent read (\d+)`）", packet)
    assert len(ids) == 2
    capsys.readouterr()
    assert cli.main([*config, "read", *ids]) == 0
    out = capsys.readouterr().out
    assert "Second paragraph.\nThird line." in out and "Fetched article.\nbody" in out
    assert cli.main([*config, "search", "Third", "line"]) == 0
    assert "Post" in capsys.readouterr().out
    lookups = [json.loads(l) for l in (project / "data/lookups.jsonl").read_text().splitlines()]
    assert [(l["cmd"], l["args"]) for l in lookups] == [("read", ids), ("search", ["Third", "line"])]
