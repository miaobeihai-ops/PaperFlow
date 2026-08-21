import json
import re
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from paperflow.config import ConfigError
from paperflow.cli import _target_date, main
from paperflow.daily import AllSourcesFailed
from paperflow.models import DailyResult, Paper, RankedPaper, SourceFailure


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
        "failures": [{"source": "hf-trending", "message": "slow"}],
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
            {"source": "hf-daily", "message": "down"},
            {"source": "hf-trending", "message": "slow"},
            {"source": "arxiv", "message": "offline"},
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
