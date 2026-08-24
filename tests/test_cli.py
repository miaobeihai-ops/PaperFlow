import json
import re
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
import httpx

from paperflow.config import ConfigError
from paperflow.cli import _load_email_config, _public_failures, _target_date, main
from paperflow.daily import AllSourcesFailed
from paperflow.email import EmailDeliveryError
from paperflow.models import DailyResult, Paper, RankedPaper, SourceFailure
from paperflow.notes import NoteExists


def test_version_text(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "paperflow 0.1.0"


def test_version_json(capsys):
    assert main(["--json", "--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "version": "0.1.0",
    }


def daily_result(*, failures=(), report_path=None):
    paper = Paper(
        arxiv_id="2608.12345",
        title="Robotics",
        authors=("Ada",),
        abstract="robotics",
        primary_category="cs.RO",
        published="2026-08-20",
        sources=("arxiv",),
        hf_upvotes=0,
        url="https://arxiv.org/abs/2608.12345",
        pdf_url="https://arxiv.org/pdf/2608.12345",
    )
    ranked = RankedPaper(paper, 55, ("robotics",))
    return DailyResult("2026-08-20", (ranked,), failures, report_path)


def test_no_command_prints_help_and_returns_zero(capsys):
    assert main([]) == 0
    assert "usage: paperflow" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "doctor"],
        ["doctor", "--json"],
    ],
)
def test_doctor_json_runs_all_checks_without_generic_config_load(
    monkeypatch, capsys, argv
):
    from paperflow.doctor import Check

    checks = (
        Check("Git", True, True, "Git is available"),
        Check("Configuration", False, True, "Configuration was not found"),
        Check("AI Sidebar", False, False, "Verify AI Sidebar manually in Zotero"),
    )
    monkeypatch.setattr("paperflow.cli.run_checks", lambda: checks)
    monkeypatch.setattr(
        "paperflow.cli._load_config",
        lambda: pytest.fail("doctor must not use the generic config loader"),
    )

    assert main(argv) == 1
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "required": check.required,
                "message": check.message,
            }
            for check in checks
        ],
    }


def test_doctor_optional_failure_is_human_readable_and_returns_zero(
    monkeypatch, capsys
):
    from paperflow.doctor import Check

    checks = (
        Check("Python", True, True, "Python 3.11+ is available"),
        Check("AI Sidebar", False, False, "Verify AI Sidebar manually in Zotero"),
    )
    monkeypatch.setattr("paperflow.cli.run_checks", lambda: checks)

    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "[OK] Python (required): Python 3.11+ is available" in output
    assert "[WARN] AI Sidebar (optional): Verify AI Sidebar manually in Zotero" in output


def test_main_dispatches_daily_to_run_daily(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "paperflow.cli._run_daily",
        lambda args: commands.append(args.command) or 7,
    )

    assert main(["daily", "--date", "2026-08-20", "--no-write"]) == 7
    assert commands == ["daily"]


def test_daily_json_uses_global_flag_and_stable_schema(
    config, monkeypatch, capsys
):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: daily_result(
            failures=(SourceFailure("hf-trending", "slow"),),
            report_path=Path("C:/Vault/PaperFlow/Reports/2026-08-20.md"),
        ),
    )

    assert main(["--json", "daily", "--date", "2026-08-20"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "ok": True,
        "date": "2026-08-20",
        "partial": True,
        "papers": [
            {
                "arxiv_id": "2608.12345",
                "title": "Robotics",
                "score": 55,
                "matched_keywords": ["robotics"],
                "sources": ["arxiv"],
                "url": "https://arxiv.org/abs/2608.12345",
                "pdf_url": "https://arxiv.org/pdf/2608.12345",
            }
        ],
        "failures": [
            {"source": "hf-trending", "message": "source request failed"}
        ],
        "report_path": "C:\\Vault\\PaperFlow\\Reports\\2026-08-20.md",
    }


def test_daily_uses_cloud_config_and_no_write(config, monkeypatch, capsys):
    cloud = replace(config, vault_path=None)
    calls = []
    monkeypatch.setenv("PAPERFLOW_PRIVATE_CONFIG_JSON", '{"private":"value"}')
    monkeypatch.setattr(
        "paperflow.cli.load_cloud_config",
        lambda raw: calls.append(("cloud", raw)) or cloud,
    )
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda: pytest.fail("local config must not be loaded"),
    )
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda actual, target_date, write_report: calls.append(
            ("daily", actual, target_date, write_report)
        )
        or DailyResult(target_date, (), (), None),
    )

    assert main(["daily", "--date", "2026-08-20", "--no-write"]) == 0

    assert calls == [
        ("cloud", '{"private":"value"}'),
        ("daily", cloud, "2026-08-20", False),
    ]
    assert "2026-08-20: 0 papers" in capsys.readouterr().out


def test_empty_private_config_uses_cloud_loader_and_returns_two(monkeypatch, capsys):
    calls = []

    def reject_empty_cloud_config(raw):
        calls.append(raw)
        raise ConfigError("PAPERFLOW_PRIVATE_CONFIG_JSON is invalid JSON")

    monkeypatch.setenv("PAPERFLOW_PRIVATE_CONFIG_JSON", "")
    monkeypatch.setattr(
        "paperflow.cli.load_cloud_config",
        reject_empty_cloud_config,
    )
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda: pytest.fail("local config must not be loaded when env var is present"),
    )

    assert main(["--json", "daily", "--no-write"]) == 2

    assert calls == [""]
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "PAPERFLOW_PRIVATE_CONFIG_JSON is invalid JSON",
    }


def test_daily_defaults_to_today_in_config_timezone(config, monkeypatch):
    observed = {}

    class NamedTimezone:
        def __init__(self, key):
            self.key = key

    class FrozenDateTime:
        @classmethod
        def now(cls, timezone):
            observed["timezone"] = timezone
            return datetime(2026, 8, 21, 1, 2)

    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr("paperflow.cli.ZoneInfo", NamedTimezone)
    monkeypatch.setattr("paperflow.cli.datetime", FrozenDateTime)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda _config, target_date, write_report: observed.update(
            target_date=target_date, write_report=write_report
        )
        or DailyResult(target_date, (), (), None),
    )

    assert main(["daily", "--no-write"]) == 0

    assert observed["timezone"].key == "Asia/Hong_Kong"
    assert observed["target_date"] == "2026-08-21"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows tzdata contract")
def test_default_hong_kong_date_uses_real_zoneinfo_on_windows(config):
    target_date = _target_date(config, None)

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date)
    assert datetime.strptime(target_date, "%Y-%m-%d").date().isoformat() == target_date


@pytest.mark.parametrize(
    ("json_mode", "expected_output"),
    [
        (False, "date must use YYYY-MM-DD"),
        (True, '"ok": false'),
    ],
)
def test_daily_config_errors_return_two(
    config, monkeypatch, capsys, json_mode, expected_output
):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ConfigError("date must use YYYY-MM-DD")
        ),
    )
    argv = (["--json"] if json_mode else []) + [
        "daily",
        "--date",
        "invalid",
    ]

    assert main(argv) == 2

    assert expected_output in capsys.readouterr().out


def test_daily_config_error_does_not_expose_private_config(monkeypatch, capsys):
    monkeypatch.setenv(
        "PAPERFLOW_PRIVATE_CONFIG_JSON", '{"secret":"PRIVATE_SENTINEL"}'
    )
    monkeypatch.setattr(
        "paperflow.cli.load_cloud_config",
        lambda *_: (_ for _ in ()).throw(ConfigError("invalid private config")),
    )

    assert main(["--json", "daily", "--no-write"]) == 2

    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    assert json.loads(output) == {
        "ok": False,
        "error": "invalid private config",
    }


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (FileNotFoundError("PRIVATE_SENTINEL"), "local configuration file was not found"),
        (PermissionError("PRIVATE_SENTINEL"), "local configuration file could not be read"),
    ],
)
def test_daily_local_config_path_errors_return_two_without_leaking_path(
    monkeypatch, capsys, error, expected_message
):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda: (_ for _ in ()).throw(error),
    )

    assert main(["--json", "daily", "--no-write"]) == 2

    output = capsys.readouterr().out
    assert "PRIVATE_SENTINEL" not in output
    assert json.loads(output) == {"ok": False, "error": expected_message}


def test_daily_invalid_timezone_returns_two(config, monkeypatch, capsys):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda: replace(config, timezone="Invalid/PRIVATE_SENTINEL"),
    )

    assert main(["--json", "daily", "--no-write"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "PRIVATE_SENTINEL" not in payload["error"]


def test_daily_default_write_requires_cloud_vault(config, monkeypatch, capsys):
    monkeypatch.setenv("PAPERFLOW_PRIVATE_CONFIG_JSON", "{}")
    monkeypatch.setattr(
        "paperflow.cli.load_cloud_config", lambda *_: replace(config, vault_path=None)
    )

    assert main(["--json", "daily", "--date", "2026-08-20"]) == 2

    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_daily_all_sources_failed_returns_three(config, monkeypatch, capsys):
    failures = (
        SourceFailure("hf-daily", "down"),
        SourceFailure("hf-trending", "slow"),
        SourceFailure("arxiv", "offline"),
    )
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AllSourcesFailed(failures)),
    )

    assert main(["--json", "daily", "--date", "2026-08-20"]) == 3

    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "all paper sources failed",
        "failures": [
            {"source": "hf-daily", "message": "source request failed"},
            {"source": "hf-trending", "message": "source request failed"},
            {"source": "arxiv", "message": "source request failed"},
        ],
    }


def test_daily_does_not_mask_unexpected_errors(config, monkeypatch):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bug")),
    )

    with pytest.raises(RuntimeError, match="bug"):
        main(["daily", "--date", "2026-08-20"])


def _email_environment(monkeypatch):
    monkeypatch.setenv("PAPERFLOW_GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("PAPERFLOW_GMAIL_APP_PASSWORD", "PRIVATE_PASSWORD")
    monkeypatch.setenv("PAPERFLOW_PRIVATE_CONFIG_JSON", '{"private":"value"}')


def test_email_config_uses_shared_topics_and_mail_only_environment(
    monkeypatch, tmp_path
):
    topics_path = tmp_path / "topics.toml"
    topics_path.write_text(
        'arxiv_categories = ["cs.RO"]\n\n[topics]\nrobotics = 5\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PAPERFLOW_TOPICS_PATH", str(topics_path))
    monkeypatch.setenv("PAPERFLOW_GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("PAPERFLOW_GMAIL_APP_PASSWORD", "PRIVATE_PASSWORD")
    monkeypatch.setenv("PAPERFLOW_MAIL_TO", "reader@example.com")
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda *_args, **_kwargs: pytest.fail("email mode must not load local config"),
    )

    config, gmail = _load_email_config()

    assert config.keywords == {"robotics": 5}
    assert config.vault_path is None
    assert config.mail_to == "reader@example.com"
    assert gmail.address == "sender@example.com"
    assert gmail.mail_to == "reader@example.com"


def test_daily_without_email_never_sends(config, monkeypatch, capsys):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr("paperflow.cli.run_daily", lambda *_args, **_kwargs: daily_result())
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda *_args: pytest.fail("daily without --email must not send SMTP"),
    )

    assert main(["daily", "--date", "2026-08-20", "--no-write"]) == 0
    capsys.readouterr()


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "daily", "--email", "--no-write", "--date", "2026-08-20"],
        ["daily", "--email", "--no-write", "--date", "2026-08-20", "--json"],
    ],
)
@pytest.mark.parametrize(
    "failures",
    [(), (SourceFailure("hf-trending", "request timed out"),)],
)
def test_daily_email_success_renders_result_once_in_order(
    config, monkeypatch, capsys, argv, failures
):
    _email_environment(monkeypatch)
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    result = daily_result(failures=failures)
    calls = []
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda: pytest.fail("email mode must not load local config"),
    )
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda actual, target_date, write_report: calls.append(
            ("daily", actual, target_date, write_report)
        )
        or result,
    )
    monkeypatch.setattr(
        "paperflow.cli.render_email_text",
        lambda date, papers, actual_failures: calls.append(
            ("text", date, papers, actual_failures)
        )
        or "plain payload",
    )
    monkeypatch.setattr(
        "paperflow.cli.render_email_html",
        lambda date, papers, actual_failures: calls.append(
            ("html", date, papers, actual_failures)
        )
        or "<p>html payload</p>",
    )
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda settings, subject, plain, html: calls.append(
            ("send", settings, subject, plain, html)
        ),
    )

    assert main(argv) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["email_sent"] is True
    assert calls[0] == ("daily", cloud, "2026-08-20", False)
    assert calls[1] == ("text", result.date, result.papers, result.failures)
    assert calls[2] == ("html", result.date, result.papers, result.failures)
    assert calls[3][0] == "send"
    assert calls[3][1].address == "sender@example.com"
    assert calls[3][1].mail_to == "reader@example.com"
    assert calls[3][2:] == (
        "PaperFlow 2026-08-20",
        "plain payload",
        "<p>html payload</p>",
    )


@pytest.mark.parametrize(
    ("missing_name", "missing_value"),
    [
        ("PAPERFLOW_GMAIL_ADDRESS", None),
        ("PAPERFLOW_GMAIL_ADDRESS", ""),
        ("PAPERFLOW_GMAIL_APP_PASSWORD", None),
        ("PAPERFLOW_GMAIL_APP_PASSWORD", ""),
        ("PAPERFLOW_PRIVATE_CONFIG_JSON", None),
        ("PAPERFLOW_PRIVATE_CONFIG_JSON", ""),
        ("mail_to", None),
        ("mail_to", ""),
    ],
)
def test_daily_email_missing_configuration_returns_two_before_fetch_or_smtp(
    config, monkeypatch, capsys, missing_name, missing_value
):
    _email_environment(monkeypatch)
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    if missing_name == "mail_to":
        cloud = replace(cloud, mail_to=missing_value)
    elif missing_value is None:
        monkeypatch.delenv(missing_name)
    else:
        monkeypatch.setenv(missing_name, missing_value)
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr(
        "paperflow.cli.load_local_config",
        lambda: pytest.fail("email mode must never fall back to local config"),
    )
    monkeypatch.setattr(
        "paperflow.cli.run_daily", lambda *_args, **_kwargs: pytest.fail("must not fetch")
    )
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email", lambda *_args: pytest.fail("must not send")
    )

    assert main(["--json", "daily", "--email", "--no-write"]) == 2

    output = capsys.readouterr().out
    assert "PRIVATE_PASSWORD" not in output
    assert json.loads(output)["ok"] is False


def test_daily_email_all_sources_failed_attempts_failure_email_and_returns_three(
    config, monkeypatch, capsys
):
    _email_environment(monkeypatch)
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    failures = (
        SourceFailure("hf-daily", "network error"),
        SourceFailure("arxiv", "HTTP 503"),
    )
    sent = []
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AllSourcesFailed(failures)),
    )
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda settings, subject, plain, html: sent.append(
            (settings, subject, plain, html)
        ),
    )

    assert main(
        ["daily", "--email", "--no-write", "--date", "2026-08-20", "--json"]
    ) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["failure_email_sent"] is True
    assert payload["failures"] == [
        {"source": "hf-daily", "message": "network error"},
        {"source": "arxiv", "message": "HTTP 503"},
    ]
    assert len(sent) == 1
    assert sent[0][1] == "PaperFlow 2026-08-20"
    assert "2026-08-20" in sent[0][2]
    assert "network error" in sent[0][2]
    assert "HTTP 503" in sent[0][3]
    assert "PRIVATE_PASSWORD" not in sent[0][2] + sent[0][3]


def test_daily_email_all_sources_failed_smtp_failure_stays_three_and_is_sanitized(
    config, monkeypatch, capsys
):
    _email_environment(monkeypatch)
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    failures = (SourceFailure("arxiv", "network error"),)
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AllSourcesFailed(failures)),
    )
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda *_args: (_ for _ in ()).throw(
            EmailDeliveryError("email delivery failed")
        ),
    )

    assert main(["--json", "daily", "--email", "--no-write"]) == 3

    output = capsys.readouterr().out
    assert "PRIVATE_PASSWORD" not in output
    assert "private.example" not in output
    payload = json.loads(output)
    assert payload["failure_email_sent"] is False
    assert payload["error"] == "all paper sources failed"


def test_daily_email_normal_smtp_failure_returns_five_and_is_sanitized(
    config, monkeypatch, capsys
):
    _email_environment(monkeypatch)
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr("paperflow.cli.run_daily", lambda *_args, **_kwargs: daily_result())
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda *_args: (_ for _ in ()).throw(
            EmailDeliveryError("email delivery failed")
        ),
    )

    assert main(["--json", "daily", "--email", "--no-write"]) == 5

    output = capsys.readouterr().out
    assert "PRIVATE_PASSWORD" not in output
    assert "private.example" not in output
    assert json.loads(output) == {
        "ok": False,
        "error": "email delivery failed",
        "email_sent": False,
    }


@pytest.mark.parametrize("all_failed", [False, True])
def test_daily_email_programming_errors_are_not_swallowed(
    config, monkeypatch, all_failed
):
    _email_environment(monkeypatch)
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    if all_failed:
        failures = (SourceFailure("arxiv", "network error"),)
        monkeypatch.setattr(
            "paperflow.cli.run_daily",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AllSourcesFailed(failures)
            ),
        )
    else:
        monkeypatch.setattr(
            "paperflow.cli.run_daily", lambda *_args, **_kwargs: daily_result()
        )
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("PRIVATE_PROGRAMMING_SENTINEL")
        ),
    )

    with pytest.raises(RuntimeError, match="PRIVATE_PROGRAMMING_SENTINEL"):
        main(["--json", "daily", "--email", "--no-write"])


def test_public_failures_allowlists_sources_and_safe_message_categories():
    private = "PRIVATE_FAILURE_SENTINEL"
    failures = (
        SourceFailure("hf-daily", "request timed out"),
        SourceFailure("hf-trending", "network error"),
        SourceFailure("arxiv", "HTTP 100"),
        SourceFailure("arxiv", "HTTP 599"),
        SourceFailure("arxiv", "ValueError"),
        SourceFailure("arxiv", "RuntimeError"),
        SourceFailure("arxiv", "_PrivateError2"),
        SourceFailure(f"arxiv\r\nBcc: {private}", "network error"),
        SourceFailure("hf-daily", f"network error https://private.test/{private}"),
        SourceFailure("hf-trending", "HTTP 600"),
        SourceFailure("arxiv", "X" * 101),
    )

    public = _public_failures(failures)

    assert public == (
        SourceFailure("hf-daily", "request timed out"),
        SourceFailure("hf-trending", "network error"),
        SourceFailure("arxiv", "HTTP 100"),
        SourceFailure("arxiv", "HTTP 599"),
        SourceFailure("arxiv", "source request failed"),
        SourceFailure("arxiv", "source request failed"),
        SourceFailure("arxiv", "source request failed"),
        SourceFailure("unknown", "network error"),
        SourceFailure("hf-daily", "source request failed"),
        SourceFailure("hf-trending", "source request failed"),
        SourceFailure("arxiv", "source request failed"),
    )
    assert public is not failures
    assert all(actual is not original for actual, original in zip(public, failures))
    assert private not in repr(public)


def test_daily_partial_redacts_failures_in_json_and_normal_email(
    config, monkeypatch, capsys
):
    private = "PRIVATE_PARTIAL_SENTINEL"
    monkeypatch.setenv("PAPERFLOW_GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("PAPERFLOW_GMAIL_APP_PASSWORD", f"APP_{private}")
    monkeypatch.setenv(
        "PAPERFLOW_PRIVATE_CONFIG_JSON", f'{{"private":"JSON_{private}"}}'
    )
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    result = daily_result(
        failures=(
            SourceFailure(f"hf-daily\r\nBcc: {private}", "network error"),
            SourceFailure(
                "arxiv", f"RuntimeError https://private.test/{private}"
            ),
        )
    )
    sent = []
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr("paperflow.cli.run_daily", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda _settings, _subject, plain, html: sent.append((plain, html)),
    )

    assert main(["--json", "daily", "--email", "--no-write"]) == 0

    output = capsys.readouterr().out
    assert private not in output
    payload = json.loads(output)
    assert payload["failures"] == [
        {"source": "unknown", "message": "network error"},
        {"source": "arxiv", "message": "source request failed"},
    ]
    assert len(sent) == 1
    assert private not in sent[0][0]
    assert private not in sent[0][1]
    assert "unknown" in sent[0][0]
    assert "source request failed" in sent[0][1]


@pytest.mark.parametrize("json_mode", [False, True])
def test_daily_all_failed_redacts_json_human_and_failure_email_boundaries(
    config, monkeypatch, capsys, json_mode
):
    private = "PRIVATE_FAILURE_SENTINEL"
    monkeypatch.setenv("PAPERFLOW_GMAIL_ADDRESS", "sender@example.com")
    monkeypatch.setenv("PAPERFLOW_GMAIL_APP_PASSWORD", f"APP_{private}")
    monkeypatch.setenv(
        "PAPERFLOW_PRIVATE_CONFIG_JSON", f'{{"private":"JSON_{private}"}}'
    )
    cloud = replace(config, vault_path=None, mail_to="reader@example.com")
    failures = (
        SourceFailure(private, private),
        SourceFailure(f"arxiv\r\nX-Private: {private}", "network error"),
        SourceFailure("hf-daily", f"HTTP 503 https://private.test/{private}"),
    )
    sent = []
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr(
        "paperflow.cli.run_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AllSourcesFailed(failures)),
    )
    monkeypatch.setattr(
        "paperflow.cli.send_daily_email",
        lambda _settings, _subject, plain, html: sent.append((plain, html)),
    )
    argv = ["daily", "--email", "--no-write", "--date", "2026-08-20"]
    if json_mode:
        argv.append("--json")

    assert main(argv) == 3

    output = capsys.readouterr().out
    assert private not in output
    assert len(sent) == 1
    assert private not in sent[0][0]
    assert private not in sent[0][1]
    assert "unknown" in sent[0][0]
    assert "source request failed" in sent[0][1]
    if json_mode:
        assert json.loads(output)["failures"] == [
            {"source": "unknown", "message": "source request failed"},
            {"source": "unknown", "message": "network error"},
            {"source": "hf-daily", "message": "source request failed"},
        ]
    else:
        assert "unknown: source request failed" in output
        assert "unknown: network error" in output
        assert "hf-daily: source request failed" in output


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "search", "3d reconstruction", "--history-only"],
        ["search", "3d reconstruction", "--history-only", "--json"],
    ],
)
def test_search_json_works_before_or_after_subcommand_and_history_only_is_offline(
    config, monkeypatch, capsys, argv
):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.search_history",
        lambda vault, query: [
            {"title": "Robotic 3D Reconstruction", "arxiv_id": "2608.12345", "path": "report.md"}
        ],
    )
    monkeypatch.setattr(
        "paperflow.cli.httpx.Client",
        lambda: pytest.fail("history-only must not create a client"),
    )

    assert main(argv) == 0

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "query": "3d reconstruction",
        "history": [
            {"title": "Robotic 3D Reconstruction", "arxiv_id": "2608.12345", "path": "report.md"}
        ],
        "online": [],
        "filters": {
            "categories": [],
            "since": None,
            "limit": 20,
            "sort": "relevance",
        },
    }


def test_search_online_cloud_without_vault_uses_one_client_and_writes_nothing(
    config, monkeypatch, capsys, tmp_path
):
    cloud = replace(config, vault_path=None)
    calls = []

    class Client:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *_args):
            calls.append("exit")

    monkeypatch.setenv("PAPERFLOW_PRIVATE_CONFIG_JSON", "{}")
    monkeypatch.setattr("paperflow.cli.load_cloud_config", lambda _raw: cloud)
    monkeypatch.setattr("paperflow.cli.httpx.Client", Client)
    monkeypatch.setattr(
        "paperflow.cli.search_arxiv",
        lambda client, query, **filters: calls.append((client, query, filters))
        or [daily_result().papers[0].paper],
    )
    monkeypatch.chdir(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    assert main(["search", "robotics", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["history"] == []
    assert payload["online"][0]["arxiv_id"] == "2608.12345"
    assert calls[0] == "enter"
    assert calls[1][1] == "robotics"
    assert calls[2] == "exit"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_search_forwards_normalized_filters_and_returns_them_in_json(
    config, monkeypatch, capsys
):
    observed = []
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.search_arxiv",
        lambda client, query, **filters: observed.append((query, filters)) or [],
    )

    assert main(
        [
            "--json",
            "search",
            "vision language action",
            "--category",
            "cs.RO",
            "--category",
            "cs.AI",
            "--since",
            "2026-07-25",
            "--limit",
            "7",
            "--sort",
            "newest",
        ]
    ) == 0

    assert observed[0][0] == "vision language action"
    assert observed[0][1] == {
        "max_results": 7,
        "categories": ("cs.RO", "cs.AI"),
        "since": date(2026, 7, 25),
        "sort": "newest",
    }
    assert json.loads(capsys.readouterr().out)["filters"] == {
        "categories": ["cs.RO", "cs.AI"],
        "since": "2026-07-25",
        "limit": 7,
        "sort": "newest",
    }


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--since", "0d", "since duration must be positive"),
        ("--since", "not-a-date", "since must be YYYY-MM-DD or Nd"),
        ("--limit", "0", "limit must be between 1 and 100"),
        ("--limit", "101", "limit must be between 1 and 100"),
        ("--limit", "many", "limit must be an integer"),
        ("--sort", "oldest", "sort must be relevance or newest"),
    ],
)
def test_search_invalid_filters_return_json_exit_two_before_loading_config(
    monkeypatch, capsys, option, value, message
):
    monkeypatch.setattr(
        "paperflow.cli._load_config",
        lambda: pytest.fail("invalid filters must fail before loading config"),
    )

    assert main(["--json", "search", "robotics", option, value]) == 2
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": message}


def test_parse_since_duration_uses_supplied_reference_date():
    from paperflow.cli import _parse_since

    assert _parse_since("30d", today=date(2026, 8, 24)) == date(2026, 7, 25)


def _write_watch_topics(path):
    path.write_text(
        'top_n = 10\ntimezone = "Asia/Hong_Kong"\nhistory_reports = 30\n'
        'arxiv_categories = ["cs.RO", "cs.CV"]\n\n'
        '[topics]\nrobotics = 5\n"3d reconstruction" = 8\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "watch", "list"],
        ["watch", "list", "--json"],
    ],
)
def test_watch_list_supports_both_json_positions(monkeypatch, tmp_path, capsys, argv):
    topics_path = tmp_path / "topics.toml"
    _write_watch_topics(topics_path)
    monkeypatch.setenv("PAPERFLOW_TOPICS_PATH", str(topics_path))

    assert main(argv) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "action": "listed",
        "changed": False,
        "topic": None,
        "weight": None,
        "topics_path": str(topics_path),
        "topics": {"3d reconstruction": 8, "robotics": 5},
        "arxiv_categories": ["cs.RO", "cs.CV"],
        "timezone": "Asia/Hong_Kong",
        "top_n": 10,
        "history_reports": 30,
    }


def test_watch_add_update_remove_and_missing_remove_are_typed(
    monkeypatch, tmp_path, capsys
):
    topics_path = tmp_path / "topics.toml"
    _write_watch_topics(topics_path)
    monkeypatch.setenv("PAPERFLOW_TOPICS_PATH", str(topics_path))

    assert main(["--json", "watch", "add", "Vision Language Action", "--weight", "9"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert (added["action"], added["changed"], added["topic"], added["weight"]) == (
        "added",
        True,
        "vision language action",
        9,
    )

    assert main(["--json", "watch", "add", "vision language action", "--weight", "7"]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert (updated["action"], updated["changed"], updated["weight"]) == (
        "updated",
        True,
        7,
    )

    assert main(["--json", "watch", "remove", "VISION LANGUAGE ACTION"]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert (removed["action"], removed["changed"]) == ("removed", True)

    assert main(["--json", "watch", "remove", "missing"]) == 0
    unchanged = json.loads(capsys.readouterr().out)
    assert (unchanged["action"], unchanged["changed"]) == ("unchanged", False)


def test_watch_requires_explicit_topic_path(monkeypatch, capsys):
    monkeypatch.delenv("PAPERFLOW_TOPICS_PATH", raising=False)

    assert main(["--json", "watch", "list"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "topic file is not configured",
    }


@pytest.mark.parametrize("weight", ["0", "101", "heavy"])
def test_watch_add_invalid_weight_returns_json_exit_two(
    monkeypatch, tmp_path, capsys, weight
):
    topics_path = tmp_path / "topics.toml"
    _write_watch_topics(topics_path)
    before = topics_path.read_bytes()
    monkeypatch.setenv("PAPERFLOW_TOPICS_PATH", str(topics_path))

    assert main(["--json", "watch", "add", "vision", "--weight", weight]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert topics_path.read_bytes() == before


def test_search_history_only_without_cloud_vault_is_config_error(
    config, monkeypatch, capsys
):
    monkeypatch.setenv("PAPERFLOW_PRIVATE_CONFIG_JSON", "{}")
    monkeypatch.setattr(
        "paperflow.cli.load_cloud_config",
        lambda _raw: replace(config, vault_path=None),
    )

    assert main(["search", "robotics", "--history-only", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_search_malformed_arxiv_xml_is_typed_and_sanitized(
    config, monkeypatch, capsys
):
    private_xml = '<not-feed xmlns="http://www.w3.org/2005/Atom">PRIVATE_XML</not-feed>'
    real_client = httpx.Client

    def client_factory():
        return real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=private_xml)
            )
        )

    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr("paperflow.cli.httpx.Client", client_factory)

    assert main(["search", "robotics", "--json"]) == 3

    output = capsys.readouterr().out
    assert "PRIVATE_XML" not in output
    assert json.loads(output) == {
        "ok": False,
        "error": "arXiv response was invalid",
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["--json", "note", "2608.12345"],
        ["note", "2608.12345", "--json"],
    ],
)
def test_note_json_positions_use_one_id_fetch_and_write(config, monkeypatch, capsys, argv):
    calls = []

    class Client:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, *_args):
            calls.append("exit")

    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr("paperflow.cli.httpx.Client", Client)
    monkeypatch.setattr(
        "paperflow.cli.fetch_arxiv_by_id",
        lambda client, arxiv_id: calls.append((client, arxiv_id)) or daily_result().papers[0].paper,
    )
    monkeypatch.setattr(
        "paperflow.cli.write_paper_note",
        lambda vault, paper, force=False: calls.append((vault, paper, force))
        or vault / "PaperFlow" / "Papers" / "2608.12345.md",
    )

    assert main(argv) == 0

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "arxiv_id": "2608.12345",
        "note_path": str(config.vault_path / "PaperFlow" / "Papers" / "2608.12345.md"),
    }
    assert calls[1][1] == "2608.12345"
    assert calls[2] == "exit"
    assert calls[3][2] is False


def test_note_exists_returns_four_and_force_is_forwarded(config, monkeypatch, capsys):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr("paperflow.cli.fetch_arxiv_by_id", lambda *_: daily_result().papers[0].paper)
    observed = []

    def write(_vault, _paper, force=False):
        observed.append(force)
        if not force:
            raise NoteExists("paper note already exists")
        return config.vault_path / "PaperFlow" / "Papers" / "2608.12345.md"

    monkeypatch.setattr("paperflow.cli.write_paper_note", write)

    assert main(["note", "2608.12345", "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["note", "2608.12345", "--force", "--json"]) == 0
    capsys.readouterr()
    assert observed == [False, True]


def test_note_network_error_is_sanitized(config, monkeypatch, capsys):
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)

    def fail(*_args):
        request = httpx.Request("GET", "https://export.arxiv.org/api/query?secret=PRIVATE")
        raise httpx.ConnectError("PRIVATE_URL_FAILURE", request=request)

    monkeypatch.setattr("paperflow.cli.fetch_arxiv_by_id", fail)

    assert main(["note", "2608.12345", "--json"]) == 3
    output = capsys.readouterr().out
    assert "PRIVATE" not in output
    assert json.loads(output) == {"ok": False, "error": "arXiv request failed"}


def test_note_existing_canonical_path_skips_client_and_fetch(
    config, monkeypatch, capsys
):
    note = config.vault_path / "PaperFlow" / "Papers" / "2608.12345.md"
    note.parent.mkdir(parents=True)
    note.write_text("existing", encoding="utf-8")
    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr(
        "paperflow.cli.httpx.Client",
        lambda: pytest.fail("existing note must not create a client"),
    )
    monkeypatch.setattr(
        "paperflow.cli.fetch_arxiv_by_id",
        lambda *_args: pytest.fail("existing note must not fetch"),
    )

    assert main(["note", "https://arxiv.org/abs/2608.12345v2", "--json"]) == 4

    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "paper note already exists",
    }


def test_note_malformed_arxiv_xml_is_sanitized(config, monkeypatch, capsys):
    private_xml = "<feed>PRIVATE_MALFORMED_XML"
    real_client = httpx.Client

    def client_factory():
        return real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=private_xml)
            )
        )

    monkeypatch.delenv("PAPERFLOW_PRIVATE_CONFIG_JSON", raising=False)
    monkeypatch.setattr("paperflow.cli.load_local_config", lambda: config)
    monkeypatch.setattr("paperflow.cli.httpx.Client", client_factory)

    assert main(["note", "2608.12345", "--json"]) == 3

    output = capsys.readouterr().out
    assert "PRIVATE_MALFORMED_XML" not in output
    assert json.loads(output) == {
        "ok": False,
        "error": "arXiv response was invalid",
    }
