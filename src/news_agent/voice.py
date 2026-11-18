"""Speaker check for claims from unlabeled captions (`news-agent voicecheck`).

The extractor names a speaker only from a textual cue and lists, for each named speaker, caption cues that are certainly
theirs (self-introductions). This compares the voice in the claim's cue with those samples: the episode audio is
downloaded once, each cue is cut by its caption timestamps, and WavLM x-vectors are compared by cosine similarity.
Nothing is transcribed; the text stays the publisher's.
"""

import json
import re
from pathlib import Path

import numpy as np

from . import collect

MODEL = "microsoft/wavlm-base-plus-sv"
RATE = 16_000
CUE = re.compile(r"^\((\d+):(\d\d):(\d\d)\) ")


def cue_spans(body: str, max_seconds: float = 30) -> dict[int, tuple[float, float]]:
    """Paragraph number -> (start, end) seconds, from "[P0001] (hh:mm:ss) text" caption lines; a cue ends where
    the next one starts."""
    starts = {}
    for m in re.finditer(r"^\[P(\d+)\] (.*)$", body, re.M):
        t = CUE.match(m.group(2))
        if t:
            h, mi, s = map(int, t.groups())
            starts[int(m.group(1))] = h * 3600 + mi * 60 + s
    keys = sorted(starts)
    return {k: (starts[k], min(starts[nxt], starts[k] + max_seconds) if nxt else starts[k] + max_seconds)
            for k, nxt in zip(keys, keys[1:] + [None])}


def load_clip(path: Path, start: float, end: float) -> np.ndarray:
    """Mono 16 kHz float32 samples of [start, end) seconds."""
    import soundfile as sf
    from scipy.signal import resample_poly

    with sf.SoundFile(path) as f:
        f.seek(min(int(start * f.samplerate), f.frames))
        x = f.read(max(0, int((end - start) * f.samplerate)), dtype="float32", always_2d=True).mean(axis=1)
        rate = f.samplerate
    return resample_poly(x, RATE, rate).astype(np.float32) if rate != RATE else x


class Embedder:
    def __init__(self, device: str | None = None):
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.fe = AutoFeatureExtractor.from_pretrained(MODEL)
        self.model = WavLMForXVector.from_pretrained(MODEL).to(self.device).eval()

    def __call__(self, clips: list[np.ndarray]) -> np.ndarray:
        """Unit-length x-vectors, one per clip."""
        out = []
        for clip in clips:
            inputs = self.fe(clip, sampling_rate=RATE, return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                e = self.model(**inputs).embeddings[0].float().cpu().numpy()
            out.append(e / np.linalg.norm(e))
        return np.stack(out)


def decide(sims: dict[str, float], min_sim: float, min_margin: float) -> tuple[str, str | None]:
    """("match", name) when the best candidate is similar enough and clearly ahead of the runner-up."""
    ranked = sorted(sims.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return "no samples", None
    best, sim = ranked[0]
    margin = sim - ranked[1][1] if len(ranked) > 1 else sim
    return ("match", best) if sim >= min_sim and margin >= min_margin else ("inconclusive", best)


def audio_file(cache: Path, item_id: int, url: str, timeout: float) -> Path:
    path = cache / f"{item_id}.mp3"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(collect.fetch(url, timeout * 15))
        tmp.rename(path)
    return path


def check(conn, cfg: dict, root: Path, claim_id: int, embedder=None) -> str:
    """Voice-check one claim and store the result in claims.voice. Returns the result line."""
    v = cfg["voice"]
    c = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if c is None:
        return f"TC{claim_id}: no such claim"
    t = conn.execute("SELECT * FROM transcripts WHERE item_id = ?", (c["item_id"],)).fetchone()
    if t["kind"] != "vtt":
        return f"TC{claim_id}: not needed, the transcript has speaker labels"
    if not t["audio"]:
        return f"TC{claim_id}: no episode audio known"
    spans = cue_spans(t["body"])
    samples = {s["name"]: [int(re.sub(r"\D", "", str(p))) for p in s.get("samples") or []]
               for s in json.loads(t["speakers"]) if isinstance(s, dict) and s.get("name")}
    samples = {k: [p for p in ps if p in spans and p != c["para"]] for k, ps in samples.items()}
    samples = {k: ps for k, ps in samples.items() if ps}
    if len(samples) < 2 or c["para"] not in spans:
        result = "no samples: fewer than two named speakers with sample cues"
    else:
        path = audio_file(root / v["cache"], c["item_id"], t["audio"], cfg["collect"]["timeout_seconds"])
        embedder = embedder or Embedder()
        refs, unsure = {}, []
        for name, ps in samples.items():
            e = embedder([load_clip(path, *spans[p]) for p in ps])
            if len(e) > 1 and (e @ e.T).min() < v["min_sim"]:  # the samples are not all one voice
                unsure.append(name)
                continue
            refs[name] = e.mean(axis=0) / np.linalg.norm(e.mean(axis=0))
        probe = embedder([load_clip(path, *spans[c["para"]])])[0]
        sims = {k: float(probe @ r) for k, r in refs.items()}
        verdict, best = decide(sims, v["min_sim"], v["min_margin"]) if len(refs) >= 2 else ("no samples", None)
        top = ", ".join(f"{k} {s:.2f}" for k, s in sorted(sims.items(), key=lambda kv: -kv[1])[:3])
        note = f"；样本不一致已排除: {', '.join(unsure)}" if unsure else ""
        result = f"{verdict}: {best}（{top}{note}）"
    with conn:
        conn.execute("UPDATE claims SET voice = ? WHERE id = ?", (result, claim_id))
    return f"TC{claim_id}: {result}"
