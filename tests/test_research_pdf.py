from pathlib import Path
from types import SimpleNamespace

import pytest

from paperflow.errors import ConfigError
from paperflow.research_pdf import export_html_to_pdf, find_browser


def test_find_browser_uses_first_existing_candidate(tmp_path):
    missing = tmp_path / "missing.exe"
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"browser")

    assert find_browser([missing, browser]) == browser


def test_export_pdf_uses_isolated_profile_and_explicit_output(tmp_path):
    html = tmp_path / "report.html"
    pdf = tmp_path / "report.pdf"
    browser = tmp_path / "chrome.exe"
    temp_root = tmp_path / "temp"
    html.write_text("<html>report</html>", encoding="utf-8")
    browser.write_bytes(b"browser")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output = next(value.split("=", 1)[1] for value in command if value.startswith("--print-to-pdf="))
        Path(output).write_bytes(b"%PDF-1.4\nreport")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = export_html_to_pdf(html, pdf, temp_root=temp_root, browser=browser, runner=fake_run)

    assert result == pdf
    assert pdf.stat().st_size > 8
    assert "--headless=new" in captured["command"]
    assert "--no-pdf-header-footer" in captured["command"]
    assert any(value.startswith("--user-data-dir=") for value in captured["command"])
    assert str(html.resolve().as_uri()) == captured["command"][-1]
    assert not list(temp_root.glob("paperflow-pdf-*"))


def test_export_pdf_reports_browser_failure(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<html>report</html>", encoding="utf-8")
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"browser")

    def failed_run(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")

    with pytest.raises(ConfigError, match="PDF export failed"):
        export_html_to_pdf(
            html,
            tmp_path / "report.pdf",
            temp_root=tmp_path / "temp",
            browser=browser,
            runner=failed_run,
        )
