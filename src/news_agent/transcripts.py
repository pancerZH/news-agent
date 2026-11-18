"""Interview / podcast transcripts: detect them, number their paragraphs, extract attributed claims with a harness
session, and list the claims in the packet.

A transcript is either the item's own full text with speaker labels ("labeled": Dwarkesh, Stratechery, ChipStrat,
Fabricated Knowledge) or a beehiiv WebVTT file linked from the episode page ("vtt": Latent Space, no speaker labels).
Claims carry a verbatim quote that must occur in the cited paragraph; claims that fail the check are dropped.
"""

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import collect, runner

NAME_LINE = re.compile(r"[A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,3}")      # "Dwarkesh Patel" alone on a line
PREFIX = re.compile(r"^([A-Z][\w.'’-]*(?: [A-Z][\w.'’-]*){0,2}):\s")  # "Doug: ...", "Dylan Patel: ...", "DB: ..."
INITIALS = re.compile(r"[A-Z]{2,3}")
VTT_LINK = re.compile(r"https://rss\.beehiiv\.com/podcasts/transcriptions/[\w-]+\.vtt")
AUDIO_LINK = re.compile(r"https://podcasts\.beehiiv\.com/[\w/-]+\.mp3")
TYPES = {"fact": "事实", "forecast": "预测", "opinion": "观点", "host_premise": "主持人前提", "hearsay": "转述"}


def speaker_labels(text: str) -> Counter:
    """Speaker labels that recur in the text: whole-line names or "Label:" prefixes."""
    lines = text.split("\n")
    names = Counter(l for l in lines if len(l.split()) <= 4 and NAME_LINE.fullmatch(l))
    prefixes = Counter(m.group(1) for l in lines if (m := PREFIX.match(l)))
    return names + prefixes


def is_labeled_transcript(text: str) -> bool:
    """At least 15 labeled turns, from two speakers with three or more turns each or from one interviewee given by
    initials (Stratechery and ChipStrat mark the host's questions only in bold)."""
    recurring = {k: n for k, n in speaker_labels(text).items() if n >= 3}
    two = len(recurring) >= 2 or any(INITIALS.fullmatch(k) and n >= 10 for k, n in recurring.items())
    return two and sum(recurring.values()) >= 15


def vtt_lines(vtt: str) -> list[str]:
    """One line per cue: "(hh:mm:ss) text"."""
    out = []
    for block in vtt.replace("\r", "").split("\n\n"):
        rows = [r for r in block.strip().split("\n") if r]
        timing = next((n for n, r in enumerate(rows) if "-->" in r), None)
        if timing is not None:
            text = collect.clean_text(" ".join(rows[timing + 1:]))
            if text:
                out.append(f"({rows[timing].split('.')[0].strip()}) {text}")
    return out


def numbered(lines: list[str]) -> str:
    return "\n".join(f"[P{n:04d}] {line}" for n, line in enumerate(lines, 1))


def paragraphs(body: str) -> dict[int, str]:
    return {int(m.group(1)): m.group(2) for m in re.finditer(r"^\[P(\d+)\] (.*)$", body, re.M)}


def norm(s: str) -> str:
    s = s.replace("**", "").lower()
    s = re.sub(r"[‘’`´]", "'", re.sub(r"[“”]", '"', s))
    s = re.sub(r"[^\w'$%.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def entity_density(text: str, patterns: dict) -> float:
    """Mentions of config entities (watchlist companies, labs, their products) per 10,000 characters. Theme keywords
    such as "AI" are left out: AI-safety and history episodes are full of them."""
    return sum(len(p.findall(text)) for p in patterns.values()) * 10_000 / max(len(text), 1)


# ---------------------------------------------------------------- finding transcripts


def find_vtt(url: str, timeout: float) -> tuple[str | None, str | None]:
    """(beehiiv transcript, episode audio URL) linked from an episode page; None where missing."""
    try:
        page = collect.fetch(url, timeout).decode("utf-8", "replace")
        link, audio = VTT_LINK.search(page), AUDIO_LINK.search(page)
        vtt = collect.fetch(link.group(0), timeout).decode("utf-8", "replace") if link else None
        return vtt, audio.group(0) if audio else None
    except Exception:
        return None, None


def discover(conn, cfg: dict, since: str, recheck_since: str) -> int:
    """Register the transcripts among items published since `since`: labeled full texts, and VTT files for items of
    feeds marked `vtt = true`. Transcripts that rarely mention the watchlist entities are recorded as skipped.
    Episodes published since `recheck_since` whose page had no captions yet are looked at again."""
    from .embed_cluster import compile_entities

    t, c = cfg["transcripts"], cfg["collect"]
    vtt_feeds = {f["id"] for f in cfg["feeds"] if f.get("vtt")}
    patterns = compile_entities(cfg["entities"])
    rows = conn.execute("SELECT id, source, url, title, content FROM items WHERE published >= ? AND (id NOT IN"
                        " (SELECT item_id FROM transcripts) OR (published >= ? AND id IN"
                        " (SELECT item_id FROM transcripts WHERE kind = 'none')))", (since, recheck_since)).fetchall()
    found = []
    for r in rows:
        if is_labeled_transcript(r["content"]):
            found.append((r, "labeled", r["content"].split("\n"), None))
        elif r["source"] in vtt_feeds:
            vtt, audio = find_vtt(r["url"], c["timeout_seconds"])
            found.append((r, "vtt", vtt_lines(vtt), audio) if vtt else (r, "none", [], None))
    with conn:
        for r, kind, lines, audio in found:
            text = "\n".join(lines)
            dense = entity_density(text, patterns) >= t["min_density"]
            status = "none" if kind == "none" else "pending" if dense else "skipped: few watchlist entity mentions"
            conn.execute("INSERT OR REPLACE INTO transcripts (item_id, kind, body, audio, status)"
                         " VALUES (?, ?, ?, ?, ?)",
                         (r["id"], kind, numbered(lines), audio, status))
    return sum(kind != "none" for _, kind, _, _ in found)


# ---------------------------------------------------------------- extraction


def load_output(path: Path) -> dict:
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def checked_claims(out: dict, body: str) -> tuple[list[dict], int]:
    """Claims whose quote occurs in one of the cited paragraphs, with that paragraph number; and the dropped count."""
    paras = paragraphs(body)
    kept, dropped = [], 0
    for c in out.get("claims") or []:
        if not isinstance(c, dict):
            dropped += 1
            continue
        q = norm(str(c.get("quote", "")))
        cited = [int(n) for p in c.get("paragraphs") or [] for n in re.findall(r"\d+", str(p))]
        para = next((n for n in cited if q and q in norm(paras.get(n, ""))), None)
        if para is None:
            dropped += 1
            continue
        kept.append({**c, "para": para})
    return kept, dropped


def as_list(v) -> list:
    return [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else []


def extract_one(cfg: dict, root: Path, issue: str, harness: str, item, transcript) -> str:
    """One harness session for one transcript; stores its claims. Returns the transcript's new status."""
    run_id, run_dir = runner.new_run_dir(cfg, root, f"{issue}-transcript-{item['id']}-{harness}")
    run_dir.mkdir(parents=True, exist_ok=True)
    source, output = run_dir / "transcript.md", run_dir / "claims.json"
    paras = paragraphs(transcript["body"])
    source.write_text(f"# {item['title']}\n\n- 来源: {item['source']}\n- 发布: {item['published'][:10]}\n"
                      f"- 链接: {item['url']}\n- 段落数: {len(paras)}\n\n---\n\n{transcript['body']}\n", encoding="utf-8")
    kind = "带说话人标注的文字稿" if transcript["kind"] == "labeled" else "没有说话人标注的自动字幕（ASR，一行可能混有两个人的话）"
    prompt = runner.render_prompt((root / cfg["paths"]["transcript_prompt"]).read_text(encoding="utf-8"),
                                  input=source, output=output, n=len(paras), last=f"{len(paras):04d}", kind=kind)
    res = runner.launch(cfg, root, run_dir, harness, prompt)
    out = load_output(output)
    claims, dropped = checked_claims(out, transcript["body"])
    runner.save_meta(run_dir, {"run_id": run_id, "item_id": item["id"], "harness": harness, "status": res["status"],
                               "claims": len(claims), "dropped": dropped, "session_id": res["session_id"],
                               "resume": res["resume"], "cmd": res["cmd"]})
    if not out:
        return f"failed: {res['status'] if res['status'] != 'ok' else 'no claims JSON'} ({run_id})"
    return json.dumps({"run_id": run_id, "speakers": out.get("speakers") or [], "claims": claims,
                       "dropped": dropped}, ensure_ascii=False)


def extract_pending(conn, cfg: dict, root: Path, issue: str) -> int:
    """Run the extraction for up to max_per_run pending transcripts (in parallel). Returns the number of new claims."""
    t = cfg["transcripts"]
    harness = cfg["harness"]["default"]
    todo = conn.execute("SELECT t.*, i.title, i.source, i.published, i.url FROM transcripts t JOIN items i"
                        " ON i.id = t.item_id WHERE t.status = 'pending' ORDER BY i.published DESC LIMIT ?",
                        (t["max_per_run"],)).fetchall()
    if not todo:
        return 0
    item = lambda r: {"id": r["item_id"], "title": r["title"], "source": r["source"], "published": r["published"],
                      "url": r["url"]}
    with ThreadPoolExecutor(max(1, t["parallel"])) as pool:
        results = list(pool.map(lambda r: extract_one(cfg, root, issue, harness, item(r), r), todo))
    added = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with conn:
        for r, res in zip(todo, results):
            if res.startswith("failed"):  # retried by the next preps, up to max_attempts sessions in all
                status = "pending" if r["attempts"] + 1 < t["max_attempts"] else res
                conn.execute("UPDATE transcripts SET status = ?, attempts = attempts + 1 WHERE item_id = ?",
                             (status, r["item_id"]))
                continue
            out = json.loads(res)
            for c in out["claims"]:
                conn.execute(
                    "INSERT INTO claims (item_id, speaker, role, attribution, para, quote, claim, type, numbers,"
                    " horizon, entities, theses) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["item_id"], str(c.get("speaker", "")), str(c.get("role", "")),
                     str(c.get("attribution", "unknown")), c["para"], str(c.get("quote", "")),
                     str(c.get("claim_zh", "")), str(c.get("type", "")), str(c.get("numbers", "")),
                     str(c.get("horizon", "")), json.dumps(as_list(c.get("entities"))),
                     json.dumps(as_list(c.get("theses")))))
                added += 1
            conn.execute("UPDATE transcripts SET status = 'done', speakers = ?, run_id = ?, extracted = ?"
                         " WHERE item_id = ?", (json.dumps(out["speakers"], ensure_ascii=False), out["run_id"], now,
                                                r["item_id"]))
    return added


def process(conn, cfg: dict, root: Path, issue: str) -> tuple[int, int]:
    """prep step: (transcripts found, claims added)."""
    t, now, fmt = cfg["transcripts"], datetime.now(timezone.utc), "%Y-%m-%dT%H:%M:%SZ"
    found = discover(conn, cfg, (now - timedelta(days=t["lookback_days"])).strftime(fmt),
                     (now - timedelta(hours=t["vtt_recheck_hours"])).strftime(fmt))
    return found, extract_pending(conn, cfg, root, issue)


# ---------------------------------------------------------------- packet


def render_section(conn, since: str, per_transcript: int) -> list[str]:
    """Claims of transcripts extracted since `since`, grouped by transcript: the speakers once, then the first
    `per_transcript` claims tied to an entity or thesis (the extractor ranks them by importance)."""
    rows = conn.execute("SELECT t.*, i.title, i.source, i.published FROM transcripts t JOIN items i ON i.id = t.item_id"
                        " WHERE t.status = 'done' AND t.extracted >= ? ORDER BY t.extracted DESC", (since,)).fetchall()
    lines = []
    for t in rows:
        claims = [c for c in conn.execute("SELECT * FROM claims WHERE item_id = ? ORDER BY id", (t["item_id"],))
                  if json.loads(c["entities"]) or json.loads(c["theses"])]
        if not claims:
            continue
        speakers = [s for s in json.loads(t["speakers"]) if isinstance(s, dict) and s.get("name")]
        who = "；".join(s["name"] + (f"（{s['affiliation']}）" if s.get("affiliation") else "") for s in speakers)
        kind = "文字稿，说话人有标注" if t["kind"] == "labeled" else "自动字幕，说话人无标注"
        more = f"；另有 {len(claims) - per_transcript} 条见 `uv run news-agent claims #{t['item_id']}`" \
            if len(claims) > per_transcript else ""
        lines += [f"### 访谈 #{t['item_id']} · {t['title']}（{t['source']} · {t['published'][:10]}）", "",
                  f"- {kind}；说话人：{who or '未知'}", f"- 原文 `uv run news-agent read {t['item_id']}`（带段落号）{more}", ""]
        for c in claims[:per_transcript]:
            unsure = "〔说话人未核实〕" if c["attribution"] not in ("label", "text_cue") else ""
            when = f" · {c['horizon']}" if c["horizon"] else ""
            lines.append(f"- TC{c['id']} · {c['speaker'] or '未知说话人'}{unsure} · {TYPES.get(c['type'], c['type'])}{when}"
                         f" · {c['claim']}（P{c['para']:04d}）")
        lines.append("")
    return lines or ["_无_", ""]


def render_claims(conn, ids: list[str]) -> str:
    """Full claim records (quote, speaker, attribution, voice check) for claim ids "TC12" or transcript ids "#2989"."""
    rows = []
    for raw in ids:
        if raw.upper().startswith("TC"):
            rows += conn.execute("SELECT * FROM claims WHERE id = ?", (int(raw[2:]),)).fetchall()
        else:
            rows += conn.execute("SELECT * FROM claims WHERE item_id = ? ORDER BY id", (int(raw.lstrip("#")),)).fetchall()
    out = []
    for c in rows:
        t = conn.execute("SELECT t.kind, i.title FROM transcripts t JOIN items i ON i.id = t.item_id WHERE t.item_id = ?",
                         (c["item_id"],)).fetchone()
        out += [f"TC{c['id']} · #{c['item_id']} {t['title'] if t else ''} · P{c['para']:04d}",
                f"  说话人: {c['speaker']}（{c['role']}，归属依据 {c['attribution']}"
                + (f"，声纹 {c['voice']}" if c["voice"] else "") + "）",
                f"  类型: {c['type']}" + (f" · 时间点 {c['horizon']}" if c["horizon"] else "")
                + (f" · 数值 {c['numbers']}" if c["numbers"] else ""),
                f"  转述: {c['claim']}", f"  原文: \"{c['quote']}\"", ""]
    return "\n".join(out)
