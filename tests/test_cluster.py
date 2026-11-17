from pathlib import Path

import numpy as np
import pytest

from news_agent.embed_cluster import build_clusters, cluster, compile_entities, encode, find_entities, normalize


def vecs(*degrees: float) -> np.ndarray:
    """Unit vectors in a plane: cos(a, b) = cos(angle difference)."""
    r = np.radians(degrees)
    return normalize(np.stack([np.cos(r), np.sin(r), np.zeros_like(r)], axis=1))


H, L = 0.88, 0.85  # config.toml [cluster]


def items(*entity_sets, urls=None):
    return [{"canonical_url": (urls or [f"https://x/{i}" for i in range(len(entity_sets))])[i], "entities": set(e)}
            for i, e in enumerate(entity_sets)]


def test_same_canonical_url_merges_even_when_dissimilar():
    got = cluster(items((), (), urls=["https://x/a", "https://x/a"]), vecs(0, 90), H, L)
    assert got == [[0, 1]]


def test_high_cosine_merges():
    assert cluster(items((), ()), vecs(0, 20), H, L) == [[0, 1]]  # cos 0.94


def test_same_sector_different_entities_stay_apart():
    # cos 0.94, but NVIDIA vs AMD: same template, different company
    assert cluster(items({"NVDA"}, {"AMD"}), vecs(0, 20), H, L) == [[0], [1]]


def test_mid_cosine_needs_a_shared_entity():
    assert cluster(items({"NVDA"}, {"NVDA", "TSM"}), vecs(0, 29), H, L) == [[0, 1]]  # cos 0.875
    assert cluster(items((), ()), vecs(0, 29), H, L) == [[0], [1]]
    assert cluster(items({"NVDA"}, {"NVDA"}), vecs(0, 35), H, L) == [[0], [1]]  # cos 0.82: too far even so


def test_centroid_gate_stops_single_link_drift():
    # A-B and B-C are each cos 0.91, A-C only 0.64: C must not chain onto {A, B}
    assert cluster(items((), (), ()), vecs(0, 25, 50), H, L) == [[0, 1], [2]]


CFG = {"entities": {"NVDA": ["NVIDIA"]}, "watchlist": {"tickers": ["NVDA"]}, "cluster": {"high": H, "low": L},
       "relevance": {"keywords": ["GPU"]},
       "score": {"tier_weights": {"1": 3.0, "2": 2.0, "3": 1.0, "4": 2.0}, "cross_source": 1.5, "hn_points": 0.4,
                 "hn_comments": 0.3, "watchlist": 1.0}}


def rows_for(specs, v):
    return [{"id": i, "source": s, "tier": t, "title": title, "summary": "", "canonical_url": f"https://x/{i}",
             "hn_points": p, "hn_comments": 0, "embedding": v[i].tobytes()} for i, (s, t, title, p) in enumerate(specs)]


def test_build_clusters_ranks_multi_source_watchlist_story_first():
    rows = rows_for([("feed", 2, "NVIDIA ships Rubin", 0), ("hacker_news", 3, "NVIDIA Rubin is out", 300),
                     ("blog", 1, "Unrelated essay", 0)], vecs(0, 5, 90))
    top, other = build_clusters(rows, CFG)
    assert top["item_ids"] == [0, 1] and top["entities"] == ["NVDA"]
    assert top["title"] == "NVIDIA ships Rubin"  # the non-HN, higher-tier headline leads
    assert top["score"] > other["score"]


def test_off_topic_hn_story_gets_no_engagement_bonus():
    rows = rows_for([("hacker_news", 3, "Rocket lands on a barge", 1700), ("blog", 1, "Supercomputer installs dip", 0),
                     ("hacker_news", 3, "A new open GPU benchmark", 300)], vecs(0, 60, 120))
    assert [c["title"] for c in build_clusters(rows, CFG)] == [
        "A new open GPU benchmark", "Supercomputer installs dip", "Rocket lands on a barge"]


MODEL = "Qwen/Qwen3-Embedding-0.6B"
CACHED = (Path.home() / ".cache/huggingface/hub" / f"models--{MODEL.replace('/', '--')}").exists()


@pytest.mark.skipif(not CACHED, reason=f"{MODEL} is not in the local Hugging Face cache")
def test_cross_lingual_same_event_clusters_with_real_model(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    texts = ["NVIDIA reports record data center revenue as Blackwell GPU demand surges",
             "英伟达数据中心营收创纪录，Blackwell GPU 需求激增",
             "AMD reports record data center revenue as Instinct GPU demand surges",
             "Federal Reserve holds interest rates steady, signals patience on cuts"]
    cfg = {"model": MODEL, "dim": 1024, "batch_size": 8, "max_seq_length": 256,
           "instruction": "Represent this technology or macroeconomic news article for event clustering and deduplication"}
    emb = encode(texts, cfg)
    assert emb.shape == (4, 1024) and np.allclose(np.linalg.norm(emb, axis=1), 1, atol=1e-5)
    patterns = compile_entities({"NVDA": ["NVIDIA", "英伟达", "Blackwell"], "AMD": ["AMD", "Instinct"],
                                 "FED": ["Federal Reserve"]})
    its = [{"canonical_url": f"https://x/{i}", "entities": find_entities(t, patterns)} for i, t in enumerate(texts)]
    assert cluster(its, emb, H, L) == [[0, 1], [2], [3]]
