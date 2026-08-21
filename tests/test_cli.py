import json
import re
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
import httpx

from paperflow.config import ConfigError
from paperflow.cli import _target_date, main
from paperflow.daily import AllSourcesFailed
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
        lambda client, query: calls.append((client, query)) or [daily_result().papers[0].paper],
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
