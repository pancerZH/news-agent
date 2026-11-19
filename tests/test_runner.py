import json
import os
from datetime import date

from news_agent import cli, embed_cluster
from news_agent.runner import clean_env, final_text, find_session_id

from conftest import fake_encode


def test_clean_env_strips_nested_harness_variables():
    env = {"PATH": "/bin", "HOME": "/h", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "ANTIGRAVITY_AGENT": "1",
           "CODEX_THREAD_ID": "t", "CODEX_HOME": "/c", "MINDSLICE_PLUGIN_WORKSPACE": "/w", "AI_AGENT": "x"}
    assert clean_env(env) == {"PATH": "/bin", "HOME": "/h", "CODEX_HOME": "/c"}


def test_session_id_and_final_text_from_each_harness():
    claude = json.dumps({"type": "result", "session_id": "c-1", "result": "claude says"})
    agy = json.dumps({"status": "SUCCESS", "conversation_id": "a-1", "response": "agy says"})
    codex = "\n".join(json.dumps(e) for e in [
        {"type": "thread.started", "thread_id": "x-1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "codex says"}},
        {"type": "turn.completed"}])
    assert [find_session_id(o) for o in (claude, agy, codex)] == ["c-1", "a-1", "x-1"]
    assert [final_text(o) for o in (claude, agy, codex)] == ["claude says", "agy says", "codex says"]
    assert find_session_id("plain text") is None


def test_prep_run_verify_end_to_end(project, workspace, monkeypatch, capsys):
    monkeypatch.setattr(embed_cluster, "encode", fake_encode)
    monkeypatch.chdir(project)
    for k, v in {"CLAUDECODE": "1", "ANTIGRAVITY_CONVERSATION_ID": "outer", "CODEX_HOME": "/keep"}.items():
        monkeypatch.setenv(k, v)
    config = ["--config", str(project / "config.toml")]
    day = date.today().isoformat()

    assert cli.main([*config, "init-memory"]) == 0
    assert cli.main([*config, "prep", "--edition", "am"]) == 0
    packet = (project / f"data/packets/{day}-am.md").read_text()
    assert "多源交叉簇 1" in packet and "(feed_a, feed_b)" in packet  # the two Rubin links are one URL

    assert cli.main([*config, "run", "--edition", "am"]) == 0
    out = capsys.readouterr().out
    assert "status: ok" in out and "fake --resume fake-session-123" in out
    report = project / f"reports/{day}-am.md"
    assert "核心信号提炼" in report.read_text() and "fake-session-123" not in report.read_text()
    assert os.readlink(project / "reports/latest.md") == f"{day}-am.md"
    assert (workspace / "staging/invest/theses/ai-compute-capex.md").exists()  # staged, not live
    assert "fake evidence" not in (workspace / "overlay/invest/theses/ai-compute-capex.md").read_text()
    env = json.loads((project / "env.json").read_text())
    assert "CLAUDECODE" not in env and "ANTIGRAVITY_CONVERSATION_ID" not in env and "CODEX_HOME" in env

    assert cli.main([*config, "verify"]) == 0
    assert cli.main([*config, "runs"]) == 0
    assert "fake-session-123" in capsys.readouterr().out

    # the evening edition gets its own packet and report, and its prompt points at the morning report
    assert cli.main([*config, "run", "--edition", "pm"]) == 0
    assert (project / f"reports/{day}-am.md").exists() and (project / f"reports/{day}-pm.md").exists()
    [pm_prompt] = (project / "data/runs").glob(f"{day}-pm-fake-*/prompt.md")
    text = pm_prompt.read_text()
    assert f"reports/{day}-am.md" in text and f"# 科技/AI + 宏观晚报 · {day}" in text


def test_unstaged_write_in_scheduled_run_fails(project, workspace, monkeypatch, capsys):
    monkeypatch.setattr(embed_cluster, "encode", fake_encode)
    monkeypatch.chdir(project)
    monkeypatch.setenv("FAKE_HARNESS_MODE", "direct")
    config = ["--config", str(project / "config.toml")]
    cli.main([*config, "init-memory"])
    assert cli.main([*config, "run"]) == 1
    out = capsys.readouterr().out
    assert "status: guardrail problems" in out and "未经 staging 的直写" in out
