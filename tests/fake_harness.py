"""Stand-in for agy/claude/codex: reads the rendered prompt, writes the report and one memory change.

FAKE_HARNESS_MODE=direct writes memory without --stage (a guardrail violation in scheduled runs).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

prompt = sys.argv[1]
packet = Path(re.search(r"\*\*当日数据包\*\*：`([^`]+)`", prompt).group(1))
report = Path(re.search(r"中文 Markdown 写入 `([^`]+)`", prompt).group(1))
workspace = re.search(r"MindSlice 工作区 `([^`]+)`", prompt).group(1)

Path("env.json").write_text(json.dumps(sorted(os.environ)))
first_cluster = re.search(r"### (C\d+)", packet.read_text()).group(1)
report.write_text(f"# 科技/AI + 宏观日报\n\n## ① 核心信号提炼\n- 见 {first_cluster}\n")

stage = [] if os.environ.get("FAKE_HARNESS_MODE") == "direct" else ["--stage"]
subprocess.run(["mindslice", "-w", workspace, "write", "invest/theses/ai-compute-capex", "--op", "append",
                "--content", f"- **关键验证记录 (Evidence)**: fake evidence ({first_cluster})", *stage,
                "--reason", "fake harness"], check=True, capture_output=True)
print(json.dumps({"type": "result", "session_id": "fake-session-123", "result": "done"}))
