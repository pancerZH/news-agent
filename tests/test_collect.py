import gzip

from news_agent.collect import canonical_url, decode_body, fetch, hn_comments, parse_feed

from conftest import rss

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
