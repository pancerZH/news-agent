"""Score adjudicated, deduplicated interview claims against the specified text sources."""

import argparse
import json
import math
from pathlib import Path


def interval(item, start, end):
    a, b = item
    if not start <= a < b <= end:
        raise ValueError(f"interval {item} outside sample [{start}, {end}]")
    return a, b


def union_seconds(windows):
    total = 0
    right = -math.inf
    for start, end in sorted(windows):
        total += max(0, end - max(start, right))
        right = max(right, end)
    return total


def score(asr, plan, review):
    start, seconds = asr["start"], asr["seconds"]
    if not (math.isfinite(seconds) and seconds > 0):
        raise ValueError("invalid ASR duration")
    if review["episode"] != plan["episode"] or review["model"] != asr["model"]:
        raise ValueError("episode or model mismatch")
    if (start, seconds, asr["media"]) != (plan["start"], plan["seconds"], plan["media"]):
        raise ValueError("ASR sample differs from the planned media or time window")
    anchors = set(plan["anchors"])
    baselines = set(review["baselines"])
    if not baselines:
        raise ValueError("at least one text baseline is required")
    counts = dict.fromkeys(baselines, 0)
    pooled = pending = accepted = unresolved = 0
    hits = set()
    seen = set()
    for claim in review["claims"]:
        if not claim["id"] or claim["id"] in seen:
            raise ValueError("each distinct claim needs a unique id")
        seen.add(claim["id"])
        interval(claim["window"], start, start + seconds)
        if claim["anchor"] is not None and claim["anchor"] not in anchors:
            raise ValueError(f"unknown anchor in {claim}")
        if claim["verdict"] not in {"supported", "rejected", "unresolved"}:
            raise ValueError("invalid verdict")
        unresolved += claim["verdict"] == "unresolved"
        if claim["verdict"] != "supported":
            continue
        if not all(claim.get(key) for key in ("speaker", "identity_basis", "question", "answer", "kind", "evidence")):
            raise ValueError("supported claim missing attribution or evidence")
        coverage = claim["coverage"]
        if set(coverage) != baselines or set(coverage.values()) - {"present", "absent", "unchecked"}:
            raise ValueError("each baseline needs an explicit judgment")
        accepted += 1
        if claim["anchor"] is not None:
            hits.add(claim["anchor"])
        for name, status in coverage.items():
            counts[name] += status == "absent"
        if "present" not in coverage.values():
            if "unchecked" in coverage.values() or claim.get("media_checked") is not True:
                pending += 1
            else:
                pooled += 1

    windows = [interval(w, start, start + seconds) for w in review["replay_windows"]]
    times = {key: review[key] for key in ("human_review_minutes", "human_replay_minutes")}
    if any(value is not None and (not math.isfinite(value) or value < 0) for value in times.values()):
        raise ValueError("invalid review time")
    return {"sample_seconds": seconds, "supported": accepted, "candidates": len(review["claims"]),
            "unresolved_candidates": unresolved,
            "absent_by_baseline": counts, "confirmed_new_vs_all": pooled, "pending_novelty": pending,
            "confirmed_new_per_audio_hour": round(pooled * 3600 / seconds, 2),
            "anchors_hit": len(hits), "anchors_total": len(anchors), "missed_anchors": sorted(anchors - hits),
            "proposed_replay_minutes": round(union_seconds(windows) / 60, 2),
            **times}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("asr", "plan", "review"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    result = score(*(json.loads(p.read_text()) for p in (args.asr, args.plan, args.review)))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
