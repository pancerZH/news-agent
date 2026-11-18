import json
import sqlite3

import numpy as np
import soundfile as sf

from news_agent import cli, embed_cluster, mail, transcripts, voice
from news_agent.transcripts import checked_claims, is_labeled_transcript, numbered, vtt_lines

from conftest import fake_encode

DWARKESH = "Intro.\nTranscript\n" + "Dwarkesh Patel\nA question about compute?\nDylan Patel\nAn answer.\n" * 8
PREFIX = "Doug: Welcome.\nVal: Thanks.\n" * 8
STRATECHERY = "Good morning.\nWelcome to Stratechery.\n" + "DB: An answer.\nA bold question?\n" * 16
HEADINGS = "Race: a bug.\nObservability: logs.\n" * 3 + "Race: again.\n" * 3  # a blog post with labeled bullets


def test_labeled_transcript_formats_are_detected_and_label_like_headings_are_not():
    assert is_labeled_transcript(DWARKESH) and is_labeled_transcript(PREFIX) and is_labeled_transcript(STRATECHERY)
    assert not is_labeled_transcript(HEADINGS)
    assert not is_labeled_transcript("Pricing: $5.\nLaunch: today.\nAvailability: now.\n")


def test_vtt_cues_become_timestamped_lines():
    vtt = "WEBVTT\n\n1\n00:00:00.240 --> 00:00:02.920\nHappy to bring\nyou this.\n\n2\n01:02:03.300 --> 01:02:05.0\nNext.\n"
    assert vtt_lines(vtt) == ["(00:00:00) Happy to bring you this.", "(01:02:03) Next."]


def test_claims_must_quote_the_cited_paragraph():
    body = numbered(["Dylan Patel: Capex will pass two trillion dollars by 2028, I think.", "Dwarkesh: Why?"])
    out = {"claims": [
        {"quote": "Capex will pass two trillion dollars by 2028", "paragraphs": ["P0001"]},
        {"quote": "Capex will pass two trillion dollars by 2028", "paragraphs": ["P0002"]},  # wrong paragraph
        {"quote": "an invented sentence", "paragraphs": ["P0001"]},
        "not a claim"]}
    kept, dropped = checked_claims(out, body)
    assert [c["para"] for c in kept] == [1] and dropped == 3


def transcript_feed() -> bytes:
    body = "".join(f"<p>{line}</p>" for line in (
        ["Dylan Patel joins to talk about NVIDIA, TSMC and AI capex.", "Transcript"] +
        ["Dwarkesh Patel", "How much will hyperscaler capex for AI compute grow, and can NVIDIA keep up?",
         "Dylan Patel", "AI capex will pass two trillion dollars by 2028, and NVIDIA GPUs plus TSMC CoWoS remain the "
                        "bottleneck for compute and inference."] * 8))
    from test_collect import feed_xml
    return feed_xml("Dylan Patel on compute", body)


def test_prep_extracts_transcript_claims_into_packet_and_mail_drops_their_ids(project, monkeypatch, capsys):
    monkeypatch.setattr(embed_cluster, "encode", fake_encode)
    config = (project / "config.toml").read_text().replace("[transcripts]\nenabled = false", "[transcripts]\nenabled = true")
    (project / "config.toml").write_text(config)
    (project / "feeds" / "a.xml").write_bytes(transcript_feed())
    args = ["--config", str(project / "config.toml")]
    assert cli.main([*args, "prep", "--day", "2026-11-16", "--edition", "am"]) == 0
    packet = (project / "data/packets/2026-11-16-am.md").read_text()
    assert "## 4. 访谈观点" in packet and "## 5. 长尾事件索引" in packet
    assert "说话人：Dylan Patel（SemiAnalysis）" in packet
    assert "- TC1 · Dylan Patel · 预测 · 2028 · 资本开支继续增长（P0004）" in packet
    assert "编造" not in packet  # its quote is not in the transcript
    conn = sqlite3.connect(project / "data/news.sqlite")
    assert conn.execute("SELECT status FROM transcripts").fetchall() == [("done",)]
    item = conn.execute("SELECT item_id FROM transcripts").fetchone()[0]
    capsys.readouterr()
    assert cli.main([*args, "read", str(item)]) == 0
    assert "[P0004] How much will hyperscaler capex" in capsys.readouterr().out
    assert cli.main([*args, "claims", "TC1"]) == 0
    assert '原文: "How much will hyperscaler capex for AI compute grow, and"' in capsys.readouterr().out
    # a second prep does not extract the same transcript again
    assert cli.main([*args, "prep", "--day", "2026-11-16", "--edition", "pm"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    assert mail.scrub_clusters("算力将翻倍（TC1、C12）。") == "算力将翻倍。"


def test_decide_needs_similarity_and_a_margin():
    assert voice.decide({"A": 0.97, "B": 0.85}, 0.92, 0.05) == ("match", "A")
    assert voice.decide({"A": 0.97, "B": 0.95}, 0.92, 0.05) == ("inconclusive", "A")
    assert voice.decide({"A": 0.90, "B": 0.70}, 0.92, 0.05) == ("inconclusive", "A")


class LoudnessEmbedder:
    """Stands in for WavLM: speaker A speaks quietly, speaker B loudly."""

    def __call__(self, clips):
        out = np.stack([[1.0, 0.0] if np.abs(c).mean() < 0.2 else [0.0, 1.0] for c in clips])
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def test_voicecheck_compares_the_claim_cue_with_speaker_samples(project, tmp_path):
    cfg, root = cli.load_config(project / "config.toml")
    conn = cli.db.connect(root / cfg["paths"]["db"])
    rate, levels = 16_000, [0.1, 0.5, 0.1, 0.5, 0.1]  # five 4-second cues: A, B, A, B, A
    audio = np.concatenate([np.full(4 * rate, x, dtype=np.float32) for x in levels])
    (root / cfg["voice"]["cache"]).mkdir(parents=True)
    sf.write(root / cfg["voice"]["cache"] / "1.mp3", audio, rate, format="WAV")
    lines = [f"(00:00:{4 * i:02d}) cue {i}" for i in range(5)]
    with conn:
        conn.execute("INSERT INTO items (id, source, tier, url, canonical_url, title, published, fetched)"
                     " VALUES (1, 's', 2, 'u', 'u', 't', 'p', 'f')")
        conn.execute("INSERT INTO transcripts (item_id, kind, body, audio, status, speakers) VALUES (1, 'vtt', ?, 'x',"
                     " 'done', ?)", (numbered(lines), json.dumps([{"name": "A", "samples": ["P0001"]},
                                                                 {"name": "B", "samples": ["P0002", "P0004"]}])))
        for para in (3, 4):
            conn.execute("INSERT INTO claims (item_id, speaker, role, attribution, para, quote, claim, type)"
                         " VALUES (1, 'Handshake 嘉宾方', 'guest', 'org', ?, 'q', 'c', 'fact')", (para,))
    assert voice.check(conn, cfg, root, 1, LoudnessEmbedder()).startswith("TC1: match: A")
    assert voice.check(conn, cfg, root, 2, LoudnessEmbedder()).startswith("TC2: match: B")
    assert conn.execute("SELECT voice FROM claims WHERE id = 2").fetchone()[0].startswith("match: B")
