import json
import shutil
import subprocess
import tomllib

import pytest

from news_agent.cli import init_memory, load_config
from news_agent.propose import propose


def ms(ws, *args):
    return subprocess.run([shutil.which("mindslice"), "-w", str(ws), *args], check=True, capture_output=True, text=True)


def thesis(evidence: str) -> str:
    return "\n".join(["- **核心假设 (Premise)**: p", "- **当前状态与置信度 (Conviction)**: c",
                      f"- **关键验证记录 (Evidence)**: {evidence}", "- **预设证伪条件 (Kill Criteria)**: k"])


@pytest.fixture
def mem(project, workspace):
    """Seed cards baked into @invest, plus one card that is only in @overlay."""
    config, root = load_config(project / "config.toml")
    init_memory(config, root)
    ms(workspace, "bake", "@invest", "--prefix", "invest/", "--yes")
    ms(workspace, "write", "invest/entities/ant", "--title", "ANT", "--summary", "s", "--content", "- x",
       "--tag", "invest", "--reason", "r")
    return config["memory"]


def test_valid_changes_are_staged_with_the_invest_tag(mem, workspace):
    new = {"id": "invest/entities/xyz", "op": "create", "title": "XYZ", "summary": "s", "tags": ["entities"],
           "content": ["- links [[@invest/invest/theses/ai-compute-capex]] and [[invest/entities/ant]]"],
           "reason": "r"}
    update = {"id": "invest/theses/ai-compute-capex", "op": "replace", "content": thesis("- [[invest/entities/xyz]]"),
              "reason": "r"}
    staged, problems = propose(mem, [new, update])
    assert problems == []
    assert staged == ["staged: @overlay/invest/entities/xyz (create)",
                      "staged: @overlay/invest/theses/ai-compute-capex (replace)"]
    card = workspace / "staging/invest/entities/xyz.md"
    assert card.exists() and not (workspace / "overlay/invest/entities/xyz.md").exists()
    assert tomllib.loads(card.read_text().split("+++")[1])["tags"] == ["invest", "entities"]
    assert len(json.loads(ms(workspace, "--json", "diff").stdout)["items"]) == 2


def test_one_bad_change_stages_nothing(mem, workspace):
    writes = [
        {"id": "invest/entities/ok", "title": "ok", "summary": "s", "content": "- x", "reason": "r"},
        {"id": "reference/sec", "content": "- x", "reason": "r"},
        {"id": "invest/theses/ai-compute-capex", "op": "append", "content": "- x", "reason": "r"},
        {"id": "invest/entities/loud", "content": "- x", "load": "always", "reason": "r"},
        {"id": "invest/entities/nvda", "op": "append", "content": "- see [[invest/theses/frontier-model-economics]]",
         "reason": "r"},
        {"id": "invest/entities/googl", "op": "append", "content": "- see [[invest/theses/nope]]"},
    ]
    staged, problems = propose(mem, writes)
    text = "\n".join(problems)
    assert staged == [] and not (workspace / "staging/invest").exists()
    assert "reference/sec: id 只能是" in text
    assert "主线卡不能 append" in text
    assert "load=\"always\"" in text
    assert "要写成 [[@invest/invest/theses/frontier-model-economics]]" in text
    assert "[[invest/theses/nope]] 指向不存在的卡片" in text and "googl: 缺少 reason" in text
    assert "invest/entities/ok" not in text


def test_thesis_cards_keep_their_sections_and_dated_evidence(mem, workspace):
    card = "invest/theses/ai-compute-capex"
    staged, problems = propose(mem, [{"id": card, "op": "replace", "reason": "r",
                                      "content": thesis("[2026-11-18] a (C1)；[2026-11-17] b (C2)")}])
    assert problems == [] and staged
    ms(workspace, "apply", f"@overlay/{card}")

    dropped = {"id": card, "op": "replace", "reason": "r", "content": thesis("[2026-11-19] new (C3)；[2026-11-18] a (C1)")}
    no_kill = {"id": card, "op": "replace", "reason": "r", "content": thesis("x").split("\n- **预设")[0]}
    patch = {"id": card, "op": "patch", "reason": "r", "edits": [{"old": "[2026-11-17] b (C2)", "new": "b"}]}
    new = {"id": "invest/theses/new-idea", "title": "t", "summary": "s", "content": "- just a note", "reason": "r"}
    staged, problems = propose(mem, [dropped, no_kill, patch, new])
    text = "\n".join(problems)
    assert staged == [] and len(problems) == 4
    assert "丢掉了原有的 [2026-11-17]" in text and "缺少 **预设证伪条件 (Kill Criteria)**" in text
    assert "new-idea: 主线卡正文要有这四段" in text

    keep = {"id": card, "op": "patch", "reason": "r",
            "edits": [{"old": "[2026-11-18] a (C1)", "new": "[2026-11-19] new (C3)；[2026-11-18] a (C1)"}]}
    assert propose(mem, [keep])[1] == []
