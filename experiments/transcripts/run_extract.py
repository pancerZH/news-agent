"""Run the extraction prompt with agy (gemini-4-hydrogen-high) on every case directory that has no claims.json yet.

Usage: python run_extract.py <cases dir> [parallel=3] [case-name-filter]
"""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from news_agent.runner import clean_env

PROMPT = Path(__file__).with_name("extract_prompt.md").read_text(encoding="utf-8")
CMD = ["agy", "--print={prompt}", "--model", "gemini-4-hydrogen-high", "--output-format", "json",
       "--dangerously-skip-permissions", "--disable-slash-commands"]


def run(case: Path) -> str:
    n = sum(1 for line in (case / "input.md").read_text(encoding="utf-8").splitlines() if line.startswith("[P"))
    prompt = (PROMPT.replace("{{input}}", str(case / "input.md")).replace("{{output}}", str(case / "claims.json"))
              .replace("{{n}}", str(n)).replace("{{last}}", f"{n:04d}"))
    t0 = time.time()
    with open(case / "agy.out", "w") as out, open(case / "agy.err", "w") as err:
        code = subprocess.run([a.replace("{prompt}", prompt) for a in CMD], cwd=case, env=clean_env(),
                              stdin=subprocess.DEVNULL, stdout=out, stderr=err, timeout=1800).returncode
    dt = time.time() - t0
    (case / "agy.time").write_text(f"{dt:.1f}\n")
    return f"{case.name:34} exit={code} {dt:6.1f}s claims.json={'yes' if (case / 'claims.json').exists() else 'NO'}"


if __name__ == "__main__":
    root, par = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 3
    flt = sys.argv[3] if len(sys.argv) > 3 else ""
    todo = sorted(d for d in root.iterdir() if d.is_dir() and flt in d.name and not (d / "claims.json").exists())
    with ThreadPoolExecutor(par) as pool:
        for line in pool.map(run, todo):
            print(line, flush=True)
