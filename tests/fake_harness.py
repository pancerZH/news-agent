"""Stand-in for agy/claude/codex: reads the rendered prompt, writes the report and proposes one memory change.

FAKE_HARNESS_MODE=direct bypasses `news-agent propose` and writes memory directly (a guardrail violation).
As the memory reviewer it approves every staged change except those whose diff says BAD;
FAKE_REVIEW_MODE=tamper also writes memory itself, =bystander has another session write outside invest/ meanwhile,
=outsider skips the invest changes and approves
the first non-invest pending change instead.
"""

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

prompt = sys.argv[1]
workspace = str(Path(tomllib.loads(Path("config.toml").read_text())["memory"]["workspace"]).expanduser())

if "独立审阅员" in prompt:
    changes = json.loads(Path(re.search(r"\*\*待审变更\*\*：`([^`]+)`", prompt).group(1)).read_text())
    decisions = Path(re.search(r"JSON 数组到 `([^`]+)`", prompt).group(1))
    mode = os.environ.get("FAKE_REVIEW_MODE")
    if mode == "tamper":
        subprocess.run(["mindslice", "-w", workspace, "write", "invest/theses/sneaky", "--title", "s", "--summary", "s",
                        "--content", "- s", "--tag", "invest", "--reason", "r"], check=True, capture_output=True)
    if mode == "bystander":
        subprocess.run(["mindslice", "-w", workspace, "write", "projects/elsewhere", "--title", "e", "--summary", "s",
                        "--content", "- e", "--reason", "r"], check=True, capture_output=True)
    out = [{"address": c["address"], "decision": "discard" if "BAD" in c["diff"] else "apply", "reason": "fake"}
           for c in changes]
    if mode == "outsider":
        out = [{"address": "@overlay/prefs/p", "decision": "apply", "reason": "not mine to approve"}]
    decisions.write_text(json.dumps(out))
    if mode == "fail-after-decisions":
        sys.exit(1)
    print(json.dumps({"session_id": "fake-review-1", "result": "done"}))
    sys.exit(0)

if "访谈/播客/座谈的文稿" in prompt:  # transcript claim extraction: one real quote, one invented
    source = Path(re.search(r"文稿：`([^`]+)`", prompt).group(1)).read_text()
    output = Path(re.search(r"JSON 到 `([^`]+)`", prompt).group(1))
    para, text = next((m.group(1), m.group(2)) for m in re.finditer(r"^\[P(\d+)\] (.*)$", source, re.M)
                      if len(m.group(2).split()) >= 12)
    quote = " ".join(text.split(": ", 1)[-1].split()[:10])
    claims = [{"speaker": "Dylan Patel", "role": "guest", "attribution": "label", "paragraphs": [f"P{para}"],
               "quote": quote, "claim_zh": "资本开支继续增长", "type": "forecast", "horizon": "2028",
               "entities": ["NVDA"], "theses": ["ai-compute-capex"]},
              {"speaker": "Dylan Patel", "role": "guest", "attribution": "label", "paragraphs": [f"P{para}"],
               "quote": "words nobody said anywhere in this transcript", "claim_zh": "编造", "type": "fact",
               "entities": ["NVDA"], "theses": []}]
    output.write_text(json.dumps({"speakers": [{"name": "Dylan Patel", "role": "guest", "affiliation": "SemiAnalysis"}],
                                  "claims": claims}))
    print(json.dumps({"session_id": "fake-transcript-1", "response": "done"}))
    sys.exit(0)

packet = Path(re.search(r"\*\*当日数据包\*\*：`([^`]+)`", prompt).group(1))
report = Path(re.search(r"中文 Markdown 写入 `([^`]+)`", prompt).group(1))

Path("env.json").write_text(json.dumps(sorted(os.environ)))
first_cluster = re.search(r"### (C\d+)", packet.read_text()).group(1)
report.write_text(f"# 科技/AI + 宏观日报\n\n## ① 核心信号提炼\n- 见 {first_cluster}\n")

evidence = "\n".join(["- **核心假设 (Premise)**: p", "- **当前状态与置信度 (Conviction)**: c",
                      f"- **关键验证记录 (Evidence)**: fake evidence ({first_cluster})", "- **预设证伪条件 (Kill Criteria)**: k"])
if os.environ.get("FAKE_HARNESS_MODE") == "direct":
    subprocess.run(["mindslice", "-w", workspace, "write", "invest/theses/ai-compute-capex", "--op", "replace",
                    "--content", evidence, "--reason", "fake harness"], check=True, capture_output=True)
else:
    change = {"id": "invest/theses/ai-compute-capex", "op": "replace", "content": evidence, "reason": "fake harness"}
    subprocess.run([sys.executable, "-m", "news_agent.cli", "--config", "config.toml", "propose", "-"],
                   input=json.dumps(change), text=True, check=True, capture_output=True)
print(json.dumps({"type": "result", "session_id": "fake-session-123", "result": "done"}))
