"""Validate `news-agent voicecheck` on an episode whose transcript has speaker labels.

Ground truth: the labeled paragraphs of a prepare.py case (gt.json), timed by aligning each paragraph's opening words
to Whisper word timestamps of the episode audio. Each speaker's first `--enroll` timed paragraphs are the voice samples
(as self-introductions would be); every later paragraph is a probe, cut to at most `--probe` seconds like a caption cue.

Usage: python voice_eval.py <gt.json> <words.json> <audio.mp3> [--enroll 2] [--probe 10]
"""

import argparse
import json
import re
from collections import defaultdict

import numpy as np

from news_agent.voice import Embedder, decide, load_clip


def norm_words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", s.lower().replace("’", "'")).split()


def align(lines: list[str], speakers: list[str], words: list[dict]) -> list[tuple[str, float, float]]:
    """(speaker, start, end) for paragraphs whose opening words are found, in order; end = next paragraph's start."""
    asr = [norm_words(w["w"])[0] if norm_words(w["w"]) else "" for w in words]
    hits, pos = [], 0
    for line, spk in zip(lines, speakers):
        if spk.startswith("(") or line == spk:
            continue
        toks = norm_words(line)
        if len(toks) < 6:
            continue
        for off in (0, 1, 2):
            key = toks[off:off + 4]
            at = next((i for i in range(pos, min(len(asr) - 4, pos + 4000)) if asr[i:i + 4] == key), None)
            if at is not None:
                hits.append((spk, words[at]["s"]))
                pos = at + 1
                break
    return [(spk, s, min(nxt, s + 30)) for (spk, s), (_, nxt) in zip(hits, hits[1:] + [(None, hits[-1][1] + 30)])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("gt")
    ap.add_argument("words")
    ap.add_argument("audio")
    ap.add_argument("--enroll", type=int, default=2)
    ap.add_argument("--probe", type=float, default=10)
    a = ap.parse_args()
    gt, words = json.load(open(a.gt)), json.load(open(a.words))
    spans = [s for s in align(gt["lines"], gt["speakers"], words) if s[2] - s[1] >= 3]
    by = defaultdict(list)
    for s in spans:
        by[s[0]].append(s)
    print("timed paragraphs per speaker:", {k: len(v) for k, v in by.items()})
    emb = Embedder()
    enrolled = {k: v[:a.enroll] for k, v in by.items() if len(v) > a.enroll}
    refs = {}
    for k, v in enrolled.items():
        r = emb([load_clip(a.audio, s, e) for _, s, e in v]).mean(axis=0)
        refs[k] = r / np.linalg.norm(r)
    probes = [s for k, v in by.items() if k in refs for s in v[a.enroll:]]
    rows = []
    for spk, s, e in probes:
        p = emb([load_clip(a.audio, s, min(e, s + a.probe))])[0]
        rows.append((spk, {k: float(p @ r) for k, r in refs.items()}))
    print(f"speakers enrolled: {len(refs)}, probes: {len(rows)}")
    top1 = sum(max(sims, key=sims.get) == spk for spk, sims in rows)
    print(f"top-1 accuracy (always answer): {top1}/{len(rows)} = {top1 / len(rows):.2%}")
    print("min_sim min_margin  answered  precision")
    for ms in (0.0, 0.9, 0.92, 0.94):
        for mm in (0.0, 0.03, 0.05):
            got = [(spk, decide(sims, ms, mm)) for spk, sims in rows]
            ans = [(spk, b) for spk, (v, b) in got if v == "match"]
            ok = sum(spk == b for spk, b in ans)
            if ans:
                print(f"  {ms:4.2f}   {mm:4.2f}     {len(ans):4}/{len(rows)}   {ok / len(ans):.2%}")
    per = defaultdict(lambda: [0, 0])
    for spk, sims in rows:
        per[spk][0] += max(sims, key=sims.get) == spk
        per[spk][1] += 1
    print("per speaker top-1:", {k: f"{a}/{b}" for k, (a, b) in per.items()})


if __name__ == "__main__":
    main()
