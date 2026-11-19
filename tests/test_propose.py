import json
import shutil
import subprocess
import tomllib

import pytest

from news_agent.cli import init_memory, load_config
from news_agent.propose import propose


def ms(ws, *args):
    return subprocess.run([shutil.which("mindslice"), "-w", str(ws), *args], check=True, capture_output=True, text=True)


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
    update = {"id": "invest/theses/ai-compute-capex", "op": "replace", "content": "- [[invest/entities/xyz]]",
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
