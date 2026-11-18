"""Build evaluation cases for dialogue transcripts: paragraph-numbered inputs plus per-paragraph speaker ground truth.

Variants:
  asis      text exactly as the collector stores it (items.content, or clean_block of the feed HTML)
  bold      like asis, but <strong> kept as **...** (host questions in Stratechery/ChipStrat are only bold)
  stripped  speaker labels removed (simulates an unlabeled transcript)
  vtt       beehiiv WebVTT cues, one per line, no speaker labels (no ground truth)

Usage: python prepare.py <news.sqlite> <raw feed dir> <out dir>
"""

import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

import feedparser

from news_agent.collect import BLOCK_BREAK, USER_AGENT, clean_text

A_LABEL = re.compile(r"[A-Z][\w.'’-]*( [A-Z][\w.'’-]*){0,3}")
B_LABEL = re.compile(r"^([A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,2}):\s+")

CASES = {
    "dwarkesh-dylan": {"item": 3002, "fmt": "A", "variants": ["asis", "stripped"]},
    "dwarkesh-intel": {"item": 2994, "fmt": "A", "variants": ["asis"]},
    "chipstrat-panel": {"item": 46, "fmt": "B", "variants": ["asis", "stripped"],
                        "labels": {"AL": "Austin Lyons", "SR": "Stacy Rasgon", "DP": "Dylan Patel"}},
    "chipstrat-goldman": {"item": 52, "fmt": "B", "variants": ["asis"]},
    "fk-val": {"item": 26, "fmt": "B", "variants": ["asis"],
               "labels": {"Doug": "Doug O'Laughlin", "Val": "Val Bercovici"}},
    "chipstrat-amd": {"item": 58, "fmt": "C", "variants": ["asis", "bold"], "feed": "chipstrat.xml",
                      "key": "agentic-cpus", "host": "Austin Lyons", "labels": {"MR": "Madhu Rangarajan"}},
    "stratechery-roblox": {"item": 14, "fmt": "C", "variants": ["asis", "bold"], "feed": "stratechery.xml",
                           "key": "roblox", "host": "Ben Thompson", "labels": {"DB": "David Baszucki"}},
    "latent-handshake": {"item": 2960, "fmt": "D", "variants": ["vtt"],
                         "vtt": "https://rss.beehiiv.com/podcasts/transcriptions/01a1c104-0c36-7bb0-9cd9-d552c4d789a7.vtt"},
}

NARRATION, HEADING = "(narration)", "(heading)"


def is_heading(line: str) -> bool:
    return len(line.split()) <= 8 and not re.search(r"[.?!:”\"]$", line)


def gt_format_a(lines: list[str]) -> list[tuple[str, str, bool]]:
    """Dwarkesh: speaker name alone on a line after 'Transcript'. Returns (line, speaker, is_label)."""
    start = lines.index("Transcript") + 1
    names = {l for l in lines[start:] if len(l.split()) <= 4 and A_LABEL.fullmatch(l)}
    out, cur = [(l, NARRATION, False) for l in lines[:start]], NARRATION
    for l in lines[start:]:
        if l in names:
            cur = l
            out.append((l, l, True))
        elif re.match(r"^\(?\s*\d{1,2}:\d{2}:\d{2}", l):
            out.append((l, HEADING, False))
        else:
            out.append((l, cur, False))
    return out


def gt_format_b(lines: list[str], labels: dict) -> list[tuple[str, str, bool]]:
    out, cur = [], NARRATION
    for l in lines:
        m = B_LABEL.match(l)
        if m:
            cur = labels.get(m.group(1), m.group(1))
            out.append((l, cur, True))
        else:
            out.append((l, HEADING if cur != NARRATION and is_heading(l) else cur, False))
    return out


def gt_format_c(html: str, case: dict) -> list[tuple[str, str, str]]:
    """Stratechery/ChipStrat: guest turns start with <strong>XX:</strong>, host turns are fully bold.
    Returns (asis line, bold line, speaker)."""
    frags = [f for f in BLOCK_BREAK.sub("\n", html).split("\n") if clean_text(f)]
    guest = re.compile(r"^\s*(?:<[^>]+>\s*)*<strong>\s*([A-Z]{1,3}):\s*</strong>")
    first = next(n for n, f in enumerate(frags) if guest.match(f))
    out, cur = [], NARRATION
    for n, f in enumerate(frags):
        text = clean_text(f)
        bold = clean_text(re.sub(r"</?(strong|b)>", "**", f)).replace("** **", " ")
        outside = clean_text(re.sub(r"<strong>.*?</strong>", "", f, flags=re.S))
        m = guest.match(f)
        if n >= first - 1:
            if m:
                cur = case["labels"].get(m.group(1), m.group(1))
            elif "<strong>" in f and not outside:
                cur = case["host"]
            elif re.search(r"<h[1-6]", f):
                out.append((text, bold, HEADING))
                continue
        out.append((text, bold, cur))
    return out


def vtt_lines(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    out = []
    for block in raw.split("\n\n"):
        rows = [r for r in block.strip().split("\n") if r]
        t = next((r for r in rows if "-->" in r), None)
        if t:
            text = clean_text(" ".join(rows[rows.index(t) + 1:]))
            if text:
                out.append(f"({t.split('.')[0]}) {text}")
    return out


def render(meta: dict, lines: list[str]) -> str:
    head = (f"# {meta['title']}\n\n- 来源: {meta['source']}\n- 发布: {meta['published'][:10]}\n- 链接: {meta['url']}\n"
            f"- 段落数: {len(lines)}（P0001–P{len(lines):04d}）\n\n---\n\n")
    return head + "\n".join(f"[P{n:04d}] {l}" for n, l in enumerate(lines, 1)) + "\n"


def main(db_path: str, raw_dir: str, out_dir: str) -> None:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    for name, case in CASES.items():
        meta = dict(db.execute("SELECT title, source, published, url, content FROM items WHERE id = ?",
                               (case["item"],)).fetchone())
        lines = meta["content"].split("\n")
        variants: dict[str, tuple[list[str], list[str] | None]] = {}
        if case["fmt"] in "AB":
            gt = gt_format_a(lines) if case["fmt"] == "A" else gt_format_b(lines, case.get("labels", {}))
            variants["asis"] = ([l for l, _, _ in gt], [s for _, s, _ in gt])
            kept = [(B_LABEL.sub("", l), s) for l, s, lab in gt if not (lab and case["fmt"] == "A")]
            variants["stripped"] = ([l for l, _ in kept], [s for _, s in kept])
        elif case["fmt"] == "C":
            entry = next(e for e in feedparser.parse(Path(raw_dir, case["feed"]).read_bytes()).entries
                         if case["key"] in e.link)
            gt = gt_format_c(entry.content[0].value, case)
            stored = set(lines)
            missing = sum(t not in stored for t, _, _ in gt)
            print(f"{name}: {missing}/{len(gt)} feed lines differ from items.content")
            variants["asis"] = ([t for t, _, _ in gt], [s for _, _, s in gt])
            variants["bold"] = ([b for _, b, _ in gt], [s for _, _, s in gt])
        else:
            variants["vtt"] = (vtt_lines(case["vtt"]), None)
        for v in case["variants"]:
            text_lines, speakers = variants[v]
            d = Path(out_dir, f"{name}--{v}")
            d.mkdir(parents=True, exist_ok=True)
            (d / "input.md").write_text(render(meta, text_lines), encoding="utf-8")
            (d / "gt.json").write_text(json.dumps({"item": case["item"], "fmt": case["fmt"], "variant": v,
                                                   "lines": text_lines, "speakers": speakers},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
            labeled = sorted({s for s in speakers or [] if not s.startswith("(")})
            print(f"{d.name:34} {len(text_lines):5} lines {sum(map(len, text_lines)):7} chars  speakers={labeled}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
