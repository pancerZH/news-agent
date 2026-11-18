"""Print scored claims compactly for manual review. Usage: python show_claims.py <scored.jsonl> [case filter] [verdict filter]"""

import json
import sys

flt = sys.argv[2] if len(sys.argv) > 2 else ""
vflt = sys.argv[3] if len(sys.argv) > 3 else ""
for line in open(sys.argv[1], encoding="utf-8"):
    r = json.loads(line)
    if flt in r["case"] and vflt in r["verdict"]:
        print(f"[{r['case']} P{r['para']}] {r['verdict']} truth={r['truth']} pred={r['pred']} "
              f"({r.get('attribution_basis')}/{r.get('attribution_confidence')}) {r.get('type')} {r.get('theses')}")
        print(f"    {r.get('claim_zh')}  | {r.get('numbers')} | {r.get('horizon')}")
        print(f"    \"{r.get('quote')}\"")
