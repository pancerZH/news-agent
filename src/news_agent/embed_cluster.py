"""Local GPU embedding (Qwen3-Embedding), 72h centroid- and entity-gated story clustering, packet export."""

import gc
import json
import math
import os
import re

import numpy as np

# ---------------------------------------------------------------- embedding


def item_text(row) -> str:
    return f"{row['title']}\n{(row['summary'] or '')[:1000]}".strip()


def encode(texts: list[str], cfg: dict) -> np.ndarray:
    """Encode with bf16 on GPU, keep the first `dim` (MRL) dims, L2-normalise in fp32, then free VRAM."""
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    import torch
    from sentence_transformers import SentenceTransformer

    cuda = torch.cuda.is_available()
    model = SentenceTransformer(cfg["model"], device="cuda" if cuda else "cpu",
                                model_kwargs={"dtype": torch.bfloat16 if cuda else torch.float32})
    model.max_seq_length = cfg["max_seq_length"]
    try:
        emb = model.encode(texts, prompt=f"Instruct: {cfg['instruction']}\nQuery: ", batch_size=cfg["batch_size"],
                           convert_to_tensor=True, normalize_embeddings=False)
        emb = emb.float().cpu().numpy()
    finally:
        del model
        gc.collect()
        if cuda:
            torch.cuda.empty_cache()
    return normalize(emb[:, : cfg["dim"]])


def normalize(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)


def embed_missing(conn, rows, cfg: dict) -> int:
    """Encode window items that have no embedding for the configured model/dim. Returns how many were encoded."""
    key = f"{cfg['model']}@{cfg['dim']}"
    todo = [r for r in rows if r["embed_model"] != key]
    if not todo:
        return 0
    vecs = encode([item_text(r) for r in todo], cfg)
    with conn:
        conn.executemany("UPDATE items SET embedding = ?, embed_model = ? WHERE id = ?",
                         [(v.astype(np.float32).tobytes(), key, r["id"]) for r, v in zip(todo, vecs)])
    return len(todo)


# ---------------------------------------------------------------- entities


def compile_entities(entities: dict[str, list[str]]) -> dict[str, re.Pattern]:
    """One pattern per entity: ASCII aliases match case-sensitively on word boundaries, CJK ones as substrings."""
    out = {}
    for key, aliases in entities.items():
        parts = [re.escape(a) if not a.isascii() else rf"(?<![\w-]){re.escape(a)}(?![\w-])" for a in [key, *aliases]]
        out[key] = re.compile("|".join(parts))
    return out


def find_entities(text: str, patterns: dict[str, re.Pattern]) -> set[str]:
    return {key for key, pat in patterns.items() if pat.search(text)}


def compile_keywords(keywords: list[str]) -> re.Pattern:
    """Topic keywords: case-insensitive, word boundaries, optional plural s."""
    return re.compile(r"(?<![\w-])(?:" + "|".join(map(re.escape, keywords)) + r")s?(?![\w-])", re.IGNORECASE)


# ---------------------------------------------------------------- clustering


def cluster(items: list[dict], emb: np.ndarray, high: float, low: float) -> list[list[int]]:
    """Single pass over items in time order. An item joins the cluster that

    1. already holds its canonical_url, else
    2. has a member with cosine >= high AND a centroid with cosine >= high (no single-link drift), unless both
       sides name entities and share none (same sector, different company), else
    3. has member and centroid cosine >= low AND shares an entity.

    Among candidates the best centroid match wins. Returns clusters as lists of item indices.
    """
    sims = emb @ emb.T
    clusters: list[dict] = []
    by_url: dict[str, int] = {}
    for i, it in enumerate(items):
        target = by_url.get(it["canonical_url"])
        if target is None:
            best = -1.0
            for ci, c in enumerate(clusters):
                s_cen = float(emb[i] @ c["sum"]) / max(float(np.linalg.norm(c["sum"])), 1e-12)
                s_max = float(sims[i, c["members"]].max())
                shared = bool(it["entities"] & c["entities"])
                conflict = bool(it["entities"]) and bool(c["entities"]) and not shared
                ok = (s_max >= high and s_cen >= high and not conflict) or (s_max >= low and s_cen >= low and shared)
                if ok and s_cen > best:
                    target, best = ci, s_cen
        if target is None:
            clusters.append({"members": [], "sum": np.zeros_like(emb[i]), "entities": set()})
            target = len(clusters) - 1
        c = clusters[target]
        c["members"].append(i)
        c["sum"] = c["sum"] + emb[i]
        c["entities"] |= it["entities"]
        by_url.setdefault(it["canonical_url"], target)
    return [c["members"] for c in clusters]


def score_cluster(rows, entities: set[str], cfg: dict, watchlist: set[str], relevant: bool = True) -> float:
    """Tier + cross-source + HN engagement + watchlist. An off-topic HN-only story gets its tier weight only."""
    weights = cfg["tier_weights"]
    if not relevant:
        return max(weights[str(r["tier"])] for r in rows)
    return (max(weights[str(r["tier"])] for r in rows)
            + cfg["cross_source"] * (len({r["source"] for r in rows}) - 1)
            + cfg["hn_points"] * math.log1p(max(r["hn_points"] for r in rows))
            + cfg["hn_comments"] * math.log1p(max(r["hn_comments"] for r in rows))
            + cfg["watchlist"] * len(entities & watchlist))


def build_clusters(rows, cfg: dict) -> list[dict]:
    """Cluster embedded window rows; returns clusters sorted by score: {title, score, entities, item_ids, rows}."""
    rows = [r for r in rows if r["embedding"] is not None]
    if not rows:
        return []
    patterns = compile_entities(cfg["entities"])
    topic = compile_keywords(cfg["relevance"]["keywords"])
    watchlist = set(cfg["watchlist"]["tickers"])
    items = [{"canonical_url": r["canonical_url"], "entities": find_entities(item_text(r), patterns)} for r in rows]
    emb = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    weights = cfg["score"]["tier_weights"]
    out = []
    for members in cluster(items, emb, cfg["cluster"]["high"], cfg["cluster"]["low"]):
        mrows = [rows[i] for i in members]
        ents = set().union(*(items[i]["entities"] for i in members))
        lead = max(mrows, key=lambda r: (weights[str(r["tier"])], r["source"] != "hacker_news"))
        relevant = any(r["source"] != "hacker_news" or topic.search(r["title"]) for r in mrows)
        score = score_cluster(mrows, ents, cfg["score"], watchlist, relevant)
        out.append({"title": lead["title"], "score": round(score, 2),
                    "entities": sorted(ents), "item_ids": [r["id"] for r in mrows], "rows": mrows})
    return sorted(out, key=lambda c: c["score"], reverse=True)


# ---------------------------------------------------------------- packet


def render_item(r, summary_chars: int = 500) -> list[str]:
    if r["source"] == "hacker_news":
        head = f"- [HN] {r['published'][:10]} [{r['title']}]({r['url']}) · {r['hn_points']} points · " \
               f"{r['hn_comments']} comments · [讨论](https://news.ycombinator.com/item?id={r['hn_id']})"
    else:
        head = f"- [T{r['tier']} {r['source']}] {r['published'][:10]} [{r['title']}]({r['url']})"
    lines = [head]
    if r["summary"]:
        lines.append(f"  > {r['summary'][:summary_chars]}")
    lines += [f"  - {c}" for c in json.loads(r["hn_top_comments"])]
    return lines


def render_cluster(c: dict, max_items: int | None = 6, summary_chars: int = 500) -> list[str]:
    sources = sorted({r["source"] for r in c["rows"]})
    lines = [f"### C{c['id']} · {c['title']}",
             f"score {c['score']} · {len(sources)} 源 ({', '.join(sources)}) · 实体: {', '.join(c['entities']) or '—'}",
             ""]
    shown = c["rows"] if max_items is None else c["rows"][:max_items]
    for r in shown:
        lines += render_item(r, summary_chars)
    if len(c["rows"]) > len(shown):
        lines.append(f"- …另有 {len(c['rows']) - len(shown)} 条（`uv run news-agent show {c['id']}`）")
    return lines + [""]


def render_packet(day: str, since: str, clusters: list[dict], panel_md: str, errors: dict, top_k: int,
                  macro_k: int = 0) -> str:
    """Hard-data panel, the top_k clusters in full, then up to macro_k more macro (tier 4) clusters, then the rest."""
    top = clusters[:top_k]
    macro = [c for c in clusters[top_k:] if any(r["tier"] == 4 for r in c["rows"])][:macro_k]
    tail = [c for c in clusters[top_k:] if all(c is not m for m in macro)]
    multi = sum(len({r["source"] for r in c["rows"]}) > 1 for c in clusters)
    lines = [f"# News Packet · {day}", "",
             f"- 时间窗：{since} 起（UTC）",
             f"- 条目 {sum(len(c['rows']) for c in clusters)} · 事件簇 {len(clusters)} · 多源交叉簇 {multi}",
             f"- 抓取失败信源：{', '.join(f'{k} ({v})' for k, v in errors.items()) or '无'}",
             "- 详情：`uv run news-agent show <簇编号>`；检索：`uv run news-agent search <关键词>`", "",
             "## 1. 客观硬数据面板", "", panel_md,
             f"## 2. Top {len(top)} 重点事件簇", ""]
    for c in top:
        lines += render_cluster(c)
    lines += [f"## 3. 宏观要闻（Top {len(top)} 之外）", ""]
    for c in macro:
        lines += render_cluster(c)
    if not macro:
        lines += ["_无_", ""]
    lines += ["## 4. 长尾事件索引", ""]
    for c in tail:
        sources = sorted({r["source"] for r in c["rows"]})
        lines.append(f"- C{c['id']} · {c['score']} · {c['title']} ({', '.join(sources)})")
    return "\n".join(lines) + "\n"
