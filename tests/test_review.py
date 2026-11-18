import json
import shutil
import subprocess

from news_agent import cli, embed_cluster

from conftest import fake_encode


def ms(ws, *args):
    return subprocess.run([shutil.which("mindslice"), "-w", str(ws), *args], check=True, capture_output=True, text=True)


def pending(ws):
    return [i["address"] for i in json.loads(ms(ws, "--json", "diff").stdout)["items"]]


def setup(project, workspace, monkeypatch):
    config = project / "config.toml"
    config.write_text(config.read_text().replace("[review]\nenabled = false", "[review]\nenabled = true"))
    monkeypatch.setattr(embed_cluster, "encode", fake_encode)
    monkeypatch.chdir(project)
    args = ["--config", str(config)]
    cli.main([*args, "init-memory"])
    # someone else's staged always-loaded change: the review must never touch it
    ms(workspace, "write", "prefs/p", "--title", "p", "--summary", "s", "--content", "- reply in Chinese",
       "--load", "always", "--reason", "r")
    return args


def test_run_reviews_staged_changes_and_leaves_others_alone(project, workspace, monkeypatch, capsys):
    args = setup(project, workspace, monkeypatch)
    ms(workspace, "write", "invest/theses/bad", "--title", "b", "--summary", "s", "--content", "- BAD claim",
       "--tag", "invest", "--stage", "--reason", "r")
    assert cli.main([*args, "run", "--edition", "am"]) == 0
    out = capsys.readouterr().out
    assert "review: ok · applied 1, discarded 1, pending 0, failed 0" in out
    assert "fake evidence" in (workspace / "overlay/invest/theses/ai-compute-capex.md").read_text()
    assert not (workspace / "overlay/invest/theses/bad.md").exists()
    assert pending(workspace) == ["@overlay/prefs/p"]
    [log] = (project / "data/runs").glob("*-review-fake-*/review.md")
    assert "@overlay/invest/theses/bad | discard | fake" in log.read_text()


def test_reviewer_that_changes_memory_gets_nothing_executed(project, workspace, monkeypatch, capsys):
    args = setup(project, workspace, monkeypatch)
    monkeypatch.setenv("FAKE_REVIEW_MODE", "tamper")
    assert cli.main([*args, "run", "--edition", "am"]) == 1
    assert "审阅期间 invest/ 下有改动，未执行任何决定: applied @overlay/invest/theses/sneaky" in capsys.readouterr().out
    assert "@overlay/invest/theses/ai-compute-capex" in pending(workspace)


def test_other_sessions_writing_elsewhere_during_review_do_not_block_it(project, workspace, monkeypatch, capsys):
    args = setup(project, workspace, monkeypatch)
    monkeypatch.setenv("FAKE_REVIEW_MODE", "bystander")
    assert cli.main([*args, "run", "--edition", "am"]) == 0
    out = capsys.readouterr().out
    assert "review: ok · applied 1, discarded 0, pending 0, failed 0" in out
    assert "~ 其他位置有写入（多半是其他会话）: applied @overlay/projects/elsewhere" in out
    assert pending(workspace) == ["@overlay/prefs/p"]


def test_blocked_review_notifies_the_owner_only(project, workspace, monkeypatch, capsys):
    from test_mail import FakeSMTP
    import smtplib
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    args = setup(project, workspace, monkeypatch)
    (project / "smtp.toml").write_text('host = "h"\nusername = "me@x"\npassword = "p"\nto = ["me@x", "reader@x"]\n')
    monkeypatch.setenv("FAKE_REVIEW_MODE", "tamper")
    assert cli.main([*args, "run", "--edition", "am"]) == 1
    report, note = FakeSMTP.sent
    assert report["To"] == "me@x, reader@x" and note["To"] == "me@x"
    assert note["Subject"].startswith("news-agent 记忆审阅未完成")
    body = note.get_content()
    assert "pending 1" in body and "invest/theses/sneaky" in body and "uv run news-agent review" in body

    FakeSMTP.sent = []
    monkeypatch.setenv("FAKE_REVIEW_MODE", "")
    assert cli.main([*args, "review"]) == 0 and FakeSMTP.sent == []  # a review that completes sends nothing


def test_review_command_ignores_foreign_decisions_and_keeps_undecided_pending(project, workspace, monkeypatch, capsys):
    args = setup(project, workspace, monkeypatch)
    assert cli.main([*args, "run", "--edition", "am", "--no-stage"]) == 0  # a report, no review
    ms(workspace, "write", "invest/theses/x", "--title", "x", "--summary", "s", "--content", "- x",
       "--tag", "invest", "--stage", "--reason", "r")
    monkeypatch.setenv("FAKE_REVIEW_MODE", "outsider")
    assert cli.main([*args, "review"]) == 1
    out = capsys.readouterr().out
    assert "审阅未给出决定，保留待审: @overlay/invest/theses/x" in out
    assert "审阅结果含非待审地址，已忽略: @overlay/prefs/p" in out
    assert "审阅未给出决定，保留待审: @overlay/invest/theses/ai-compute-capex" in out
    assert sorted(pending(workspace)) == ["@overlay/invest/theses/ai-compute-capex", "@overlay/invest/theses/x",
                                          "@overlay/prefs/p"]
