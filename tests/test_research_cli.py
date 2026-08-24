import json
from pathlib import Path

import pytest

from paperflow.cli import build_parser, main
from paperflow.research_context import PreparedResearch


@pytest.mark.parametrize("argv", [
    ["--json", "research", "prepare", "--domain", "chemical-energy"],
    ["research", "prepare", "--domain", "chemical-energy", "--json"],
])
def test_research_prepare_json_supports_global_flag_positions(monkeypatch, capsys, tmp_path, argv):
    monkeypatch.setenv("PAPERFLOW_HOME", str(tmp_path))
    fake = PreparedResearch("run-1", "chemical-energy", "2026-08-24", tmp_path / "context.json", b"{}", 3, False)
    monkeypatch.setattr("paperflow.cli.prepare_research", lambda *args, **kwargs: fake)
    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "research.prepare"
    assert payload["domain"] == "chemical-energy"
    assert payload["candidate_count"] == 3


@pytest.mark.parametrize("forbidden", ["--date", "--catch-up", "--backfill"])
def test_research_prepare_rejects_catch_up_style_flags(forbidden):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["research", "prepare", "--domain", "robotics", forbidden, "1"])


def test_research_requires_explicit_paperflow_home(monkeypatch, capsys):
    monkeypatch.delenv("PAPERFLOW_HOME", raising=False)
    assert main(["--json", "research", "prepare", "--domain", "robotics"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "PAPERFLOW_HOME is required for research commands"
