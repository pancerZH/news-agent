"""Score extracted claims: is the quote really in the cited paragraph, and is the speaker right?

Usage: python score.py <cases dir>   (writes <cases dir>/../scored.jsonl and prints a per-case table)
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

HOSTS = {"dwarkesh": "Dwarkesh Patel", "chipstrat": "Austin Lyons", "fk": "Doug O'Laughlin",
         "stratechery": "Ben Thompson"}


def norm(s: str) -> str:
    s = s.replace("**", "").lower()
    s = re.sub(r"[‘’`´]", "'", s)
    s = re.sub(r"[“”]", '"', s)
    s = re.sub(r"[–—-]+", " ", s)
    s = re.sub(r"[^\w'$%. ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def trigram_hit(q: str, p: str) -> float:
    w = q.split()
    grams = [" ".join(w[i:i + 3]) for i in range(max(1, len(w) - 2))]
    return sum(g in p for g in grams) / len(grams)


def locate(quote: str, cited: list[int], paras: list[str]) -> tuple[str, int | None]:
    q = norm(quote)
    if not q:
        return "missing", None
    for i in cited:
        if 0 <= i < len(paras) and q in paras[i]:
            return "exact", i
    for i, p in enumerate(paras):
        if q in p:
            return "wrong_para", i
    best = max(range(len(paras)), key=lambda i: trigram_hit(q, paras[i]))
    if trigram_hit(q, paras[best]) >= 0.6:
        return "near", best
    return "missing", None


def canon(pred: str, names: list[str], host: str | None) -> str:
    p = pred.lower()
    if not p or "unknown" in p or "未知" in p:
        return "unknown"
    for n in names:
        if re.search(rf"\b{re.escape(n.split()[0].lower())}\b", p):
            return n
    last = [n for n in names if len(n.split()) > 1 and re.search(rf"\b{re.escape(n.split()[-1].lower())}\b", p)]
    if len(last) == 1:
        return last[0]
    if host and ("host" in p or "主持" in p):
        return host
    return f"other:{pred}"


def main(root: Path) -> None:
    rows, table = [], []
    for case in sorted(d for d in root.iterdir() if (d / "claims.json").exists()):
        gt = json.loads((case / "gt.json").read_text())
        paras = [norm(l) for l in gt["lines"]]
        try:
            out = json.loads(re.sub(r"^```(json)?|```$", "", (case / "claims.json").read_text().strip()))
        except json.JSONDecodeError as e:
            table.append(f"{case.name:34} JSON error: {e}")
            continue
        names = sorted({s for s in gt["speakers"] or [] if not s.startswith("(")})
        host = {"chipstrat-goldman": "David Goldman"}.get(case.name.split("--")[0]) or HOSTS.get(case.name.split("-")[0])
        stats, pos, conf = Counter(), [], Counter()
        roles = {sp.get("name", ""): sp.get("role", "") for sp in out.get("speakers", [])}
        for c in out.get("claims", []):
            cited = [int(m) - 1 for p in c.get("paragraphs", []) for m in re.findall(r"\d+", str(p))]
            how, at = locate(c.get("quote", ""), cited, paras)
            stats[how] += 1
            if at is not None:
                pos.append(at / len(paras))
            truth = gt["speakers"][at] if gt["speakers"] and at is not None else None
            pred = canon(c.get("speaker", ""), names, host) if names else c.get("speaker", "")
            role = roles.get(c.get("speaker", ""), "")
            if truth is None:
                verdict = "no_gt"
            elif truth.startswith("("):
                verdict = "narration"
            elif pred == "unknown" and role in ("host", "guest"):
                verdict = "role_ok" if (role == "host") == (truth == host) else "wrong_host_guest"
            elif pred == "unknown":
                verdict = "abstain"
            elif pred == truth:
                verdict = "correct"
            elif host in (pred, truth):
                verdict = "wrong_host_guest"
            else:
                verdict = "wrong_guest_guest"
            stats[verdict] += 1
            conf[(c.get("attribution_confidence"), verdict)] += 1
            stats[f"type:{c.get('type')}"] += 1
            stats[f"basis:{c.get('attribution_basis')}"] += 1
            rows.append({"case": case.name, "verdict": verdict, "locate": how, "truth": truth, "pred": pred,
                         "para": at + 1 if at is not None else None, **c})
        n = len(out.get("claims", []))
        judged = sum(stats[k] for k in ("correct", "role_ok", "abstain", "wrong_host_guest", "wrong_guest_guest"))
        pos.sort()
        q = lambda f: f"{pos[int(f * (len(pos) - 1))]:.2f}" if pos else "-"
        table.append(
            f"{case.name:34} n={n:2} quote exact/wrongP/near/miss={stats['exact']}/{stats['wrong_para']}/"
            f"{stats['near']}/{stats['missing']}  attr ok={stats['correct']}+role_ok={stats['role_ok']}/{judged} abstain={stats['abstain']} "
            f"host<->guest={stats['wrong_host_guest']} guest<->guest={stats['wrong_guest_guest']} "
            f"narr={stats['narration']}  pos q25/50/75/max={q(.25)}/{q(.5)}/{q(.75)}/{q(1)}  "
            f"basis={ {k[6:]: v for k, v in stats.items() if k.startswith('basis:')} }  "
            f"types={ {k[5:]: v for k, v in stats.items() if k.startswith('type:')} }  "
            f"speakers={[s.get('name') for s in out.get('speakers', [])]}")
        low = {k: v for k, v in conf.items() if k[0] in ("medium", "low")}
        if low:
            table.append(f"{'':34} confidence x verdict (medium/low): {dict(low)}")
    (root.parent / "scored.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print("\n".join(table))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
