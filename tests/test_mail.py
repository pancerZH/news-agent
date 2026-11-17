import smtplib
from datetime import date

from news_agent import cli, embed_cluster, mail

from conftest import fake_encode

REPORT = """# 科技/AI + 宏观日报 · 2026-11-16

## ① 核心信号提炼

| 期限 | 收益率 |
| :--- | ---: |
| 10Y | 5.12% |

---

## ② 长期投资主线边际评估

- **主线 1：云厂 AI 资本开支（`invest/theses/ai-compute-capex`）—— 中性观察**
- 宏观卡 (invest/macro/fed-rates-liquidity) 维持；另见 `invest/watchlist`。

---

## ③ 记忆库变更清单

1. `invest/theses/x`（replace）

---
_run `r1` · harness `agy` · 续聊: `agy --conversation abc`_
"""


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_public_text_drops_memory_section_footer_and_card_paths():
    text = mail.public_text(REPORT)
    assert "**主线 1：云厂 AI 资本开支—— 中性观察**" in text and "宏观卡 维持；另见 。" in text
    assert "invest/" not in text and "③" not in text
    assert "_run" not in text and "续聊" not in text
    assert text.endswith("另见 。\n")  # the separator before ③ is not left dangling
    assert mail.public_text("# no sections\n") == "# no sections\n"


def test_html_renders_tables():
    html = mail.render_html(mail.public_text(REPORT), "t")
    assert "<table>" in html and '<td style="text-align:right">5.12%</td>' in html


def test_send_builds_html_mail(tmp_path, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    report = tmp_path / "2026-11-16.md"
    report.write_text(REPORT)
    smtp = tmp_path / "smtp.toml"
    smtp.write_text('host = "smtp.example.com"\nport = 465\nusername = "me@example.com"\npassword = "x"\nto = "you@example.com"\n')
    assert mail.send(report, "科技/AI + 宏观早报 · 2026-11-16", smtp, status="guardrail problems") == ["you@example.com"]
    [msg] = FakeSMTP.sent
    assert msg["Subject"] == "科技/AI + 宏观早报 · 2026-11-16 [guardrail problems]"
    assert msg["From"] == "me@example.com" and msg["To"] == "you@example.com"
    html = msg.get_body(("html",)).get_content()
    assert "<table>" in html and "记忆库变更清单" not in html
    assert [p.get_filename() for p in msg.iter_attachments()] == ["2026-11-16.md"]


def test_run_mails_report_only_when_configured(project, workspace, monkeypatch, capsys):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(embed_cluster, "encode", fake_encode)
    monkeypatch.chdir(project)
    config = ["--config", str(project / "config.toml")]
    cli.main([*config, "init-memory"])
    assert cli.main([*config, "run", "--edition", "pm"]) == 0 and FakeSMTP.sent == []  # no smtp.toml: skipped
    assert "mail: skipped" in capsys.readouterr().err

    (project / "smtp.toml").write_text('host = "h"\nusername = "me@x"\npassword = "p"\nto = ["you@x"]\n')
    assert cli.main([*config, "run", "--edition", "pm"]) == 0
    [msg] = FakeSMTP.sent
    assert msg["Subject"] == f"科技/AI + 宏观晚报 · {date.today().isoformat()}"
    assert cli.main([*config, "mail", "--edition", "pm", "--html", str(project / "preview.html")]) == 0
    assert "<h1>" in (project / "preview.html").read_text()


def test_cluster_ids_are_dropped_from_reader_text():
    cases = {
        "- **NVIDIA 治理危机（C158, C159, C160）**": "- **NVIDIA 治理危机**",
        "[FT](u)（`C161`）报道": "[FT](u)报道",
        "[Wired](u)（`C171`，HN 87 分 / 87 评论）报道": "[Wired](u)（HN 87 分 / 87 评论）报道",
        "（`C209`，[摘要](u) `C216`）在过去": "（[摘要](u)）在过去",
        "DRAM 诉讼（`C320`, `C321`）、HF": "DRAM 诉讼、HF",
        "SC26 大会与 C2C 互连": "SC26 大会与 C2C 互连",
    }
    for raw, want in cases.items():
        assert mail.public_text(raw).rstrip() == want
