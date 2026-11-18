import shutil
import subprocess
import tomllib

import pytest

from news_agent.cli import init_memory, load_config
from news_agent.runner import now_iso, others_since, verify


def ms(ws, *args):
    return subprocess.run([shutil.which("mindslice"), "-w", str(ws), *args], check=True, capture_output=True, text=True)


def write(ws, card_id, content="- fact", *extra):
    ms(ws, "write", card_id, "--title", card_id, "--summary", "s", "--content", content, "--reason", "test", *extra)


@pytest.fixture
def cfg(project):
    return load_config(project / "config.toml")


def test_init_memory_is_idempotent_and_clean(cfg, workspace):
    config, root = cfg
    created = init_memory(config, root)
    assert "invest/watchlist" in created and "invest/theses/ai-compute-capex" in created
    assert "invest/entities/nvda" in created and len(created) == 1 + 5 + 8
    assert init_memory(config, root) == []
    nvda = tomllib.loads((workspace / "overlay/invest/entities/nvda.md").read_text().split("+++")[1])
    assert {"NVDA", "NVIDIA", "英伟达", "Blackwell"} <= set(nvda["aliases"]) and "invest" in nvda["tags"]
    assert verify(config["memory"]) == []


def test_always_load_and_untagged_invest_cards_are_flagged_but_tags_elsewhere_are_free(cfg, workspace):
    mem = cfg[0]["memory"]
    write(workspace, "invest/theses/loud", "- x", "--tag", "invest", "--load", "always", "--no-stage")
    write(workspace, "invest/theses/untagged")
    write(workspace, "reference/data-source", "- x", "--tag", "invest")  # another session's card: not ours
    problems = "\n".join(verify(mem))
    assert "load=\"always\"" in problems and "loud.md" in problems
    assert "缺少 \"invest\" 标签" in problems and "untagged.md" in problems
    assert "data-source" not in problems


def test_history_checks_out_of_bounds_and_unstaged_writes(cfg, workspace):
    mem = cfg[0]["memory"]
    since = now_iso()
    write(workspace, "invest/theses/staged", "- x", "--tag", "invest", "--stage")
    assert verify(mem, since=since, staged_only=True) == []
    write(workspace, "invest/theses/direct", "- x", "--tag", "invest")
    write(workspace, "projects/elsewhere")
    problems = "\n".join(verify(mem, since=since, staged_only=True))
    assert "未经 staging 的直写: @overlay/invest/theses/direct" in problems
    assert "projects/elsewhere" not in problems  # other sessions write outside invest/ all day
    assert any("@overlay/projects/elsewhere" in w for w in others_since(workspace, since))
    assert not any("invest/" in w for w in others_since(workspace, since))
    assert "invest/theses/direct" not in "\n".join(verify(mem, since=since, staged_only=False))


def test_links_after_bake_must_be_mount_qualified(cfg, workspace):
    config, root = cfg
    init_memory(config, root)
    ms(workspace, "bake", "@invest", "--prefix", "invest/", "--yes")
    assert not (workspace / "overlay/invest").exists()
    assert verify(config["memory"]) == []  # links inside the baked slice still resolve
    assert init_memory(config, root) == []  # seeds found in @invest, not recreated

    write(workspace, "invest/theses/new", "- see [[@invest/invest/watchlist]]", "--tag", "invest")
    assert verify(config["memory"]) == []
    write(workspace, "invest/theses/bad", "- see [[invest/watchlist]]", "--tag", "invest")
    [problem] = verify(config["memory"])
    assert "断链" in problem and "@overlay/invest/theses/bad" in problem

    # a baked always-loaded invest card is still caught
    slice_card = workspace / "slices/invest.slice/invest/watchlist.md"
    slice_card.write_text(slice_card.read_text().replace("+++\ntitle", "+++\nload = \"always\"\ntitle", 1))
    assert any("load=\"always\"" in p for p in verify(config["memory"]))
