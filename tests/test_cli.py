import json

from paperflow.cli import main


def test_version_text(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "paperflow 0.1.0"


def test_version_json(capsys):
    assert main(["--json", "--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "version": "0.1.0",
    }
