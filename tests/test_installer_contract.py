from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-windows.ps1"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_installer_declares_safe_preview_first_interface():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "[CmdletBinding(SupportsShouldProcess = $true)]" in text
    assert "[switch]$CheckOnly" in text
    assert "[switch]$InstallMissing" in text
    assert "[string]$VaultPath" in text
    assert "Invoke-Expression" not in text
    assert "zotero.sqlite" not in text.casefold()
    assert "PAPERFLOW_GMAIL_APP_PASSWORD" not in text
    assert "PAPERFLOW_PRIVATE_CONFIG_JSON" not in text


def test_installer_uses_only_allowlisted_exact_winget_packages():
    text = INSTALLER.read_text(encoding="utf-8")
    allowed = {
        "Git.Git",
        "Python.Python.3.11",
        "DigitalScholar.Zotero",
        "Obsidian.Obsidian",
    }

    package_ids = set(re.findall(r"(?:Git\.Git|Python\.Python\.3\.11|DigitalScholar\.Zotero|Obsidian\.Obsidian)", text))
    assert package_ids == allowed
    assert "winget install --id $PackageId --exact" in text
    assert "Install-WingetPackage -PackageId" in text
    assert "[ValidateSet('Git.Git', 'Python.Python.3.11', 'DigitalScholar.Zotero', 'Obsidian.Obsidian')]" in text


def test_installer_refreshes_process_path_before_rechecking_installs():
    text = INSTALLER.read_text(encoding="utf-8")
    install_branch = text[text.index("if ($InstallMissing)") :]

    refresh_position = install_branch.index("Refresh-ProcessPath")
    recheck_position = install_branch.index("$state = Get-InstallationState")
    assert refresh_position < recheck_position
    assert "SetEnvironmentVariable('Path'" not in install_branch[:refresh_position]


def test_installer_uses_current_agents_skill_paths_only():
    text = INSTALLER.read_text(encoding="utf-8")

    assert ".agents\\skills\\paperflow" in text
    assert ".codex\\skills" not in text.casefold()
    assert "CODEX_HOME" not in text
    assert "Join-Path $env:USERPROFILE '.agents\\skills\\paperflow'" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_check_only_accepts_default_py_newer_than_3_11(tmp_path):
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "py.cmd").write_text(
        '@echo off\r\nif "%1"=="-3.11" exit /b 1\r\necho 3.12.7\r\n',
        encoding="utf-8",
    )
    user_profile = tmp_path / "profile"
    appdata = tmp_path / "appdata"
    local_appdata = tmp_path / "localappdata"
    for directory in (user_profile, appdata, local_appdata):
        directory.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(user_profile),
            "USERPROFILE": str(user_profile),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "PATH": str(fake_bin),
        }
    )

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-CheckOnly",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Python\s+OK\s+version 3\.12\.7", result.stdout)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_check_only_runs_without_mutating_isolated_user_directories(tmp_path):
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")

    user_profile = tmp_path / "profile"
    appdata = tmp_path / "appdata"
    local_appdata = tmp_path / "localappdata"
    for directory in (user_profile, appdata, local_appdata):
        directory.mkdir()

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(user_profile),
            "USERPROFILE": str(user_profile),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
        }
    )
    before_path = env.get("PATH", "")
    subprocess.run(
        [powershell, "-NoProfile", "-Command", "exit 0"],
        env=env,
        capture_output=True,
        timeout=30,
        check=True,
    )
    before_files = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
    }
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-CheckOnly",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    for component in ("Git", "Python", "Codex", "Zotero", "Obsidian", "Vault", "Sidebar"):
        assert component in result.stdout
    after_files = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
    }
    assert after_files == before_files
    assert env.get("PATH", "") == before_path


def test_ci_is_least_privilege_and_pinned_for_windows_and_linux():
    text = _read(".github/workflows/ci.yml")

    assert "permissions:\n  contents: read" in text
    assert "os: [windows-latest, ubuntu-latest]" in text
    assert "python-version: \"3.11\"" in text
    assert 'python -m pip install -e ".[dev]"' in text
    assert "python -m pytest -v" in text
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in text
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in text
    assert "actions/checkout@v" not in text
    assert "actions/setup-python@v" not in text


def test_readme_documents_executable_flow_in_required_order():
    text = _read("README.md")
    headings = [
        "用途与非目标",
        "Windows 前置条件",
        "克隆与安装",
        "本地配置",
        "命令",
        "Codex Skill",
        "Zotero 协作流程",
        "GitHub Actions 云端邮件",
        "隐私边界",
        "升级与卸载",
        "故障排查",
        "费用说明",
    ]
    positions = [text.index(f"## {heading}") for heading in headings]
    assert positions == sorted(positions)

    for command in (
        ".\\scripts\\install-windows.ps1 -CheckOnly",
        ".\\scripts\\install-windows.ps1",
        "paperflow --json daily",
        'paperflow --json search "robotics"',
        "paperflow --json note 2401.01234",
        "paperflow --json doctor",
        "paperflow --json daily --email --no-write",
    ):
        assert command in text
    assert "%USERPROFILE%\\.agents\\skills\\paperflow" in text
    assert "Zotero Connector" in text
    assert "AI Sidebar" in text
    assert "workflow_dispatch" in text
    assert "arXiv 429" in text
    assert "App Password" in text
    assert "ExecutionPolicy" in text
    assert "zotero.sqlite" in text
    assert "SQLite" in text
    assert "免费开源" in text
    assert "绝对永久免费" in text


def test_readme_has_three_secret_names_and_valid_compact_cloud_json():
    text = _read("README.md")
    for name in (
        "PAPERFLOW_GMAIL_ADDRESS",
        "PAPERFLOW_GMAIL_APP_PASSWORD",
        "PAPERFLOW_PRIVATE_CONFIG_JSON",
    ):
        assert name in text

    match = re.search(r"<!-- cloud-config-example -->\s*```json\s*(\{[^\n]+\})\s*```", text)
    assert match is not None
    cloud_config = json.loads(match.group(1))
    assert cloud_config["keywords"]
    assert cloud_config["mail_to"] == "you@example.com"
    assert "vault_path" not in cloud_config


def test_notice_and_license_have_required_release_attribution():
    notice = _read("NOTICE")
    assert notice == (
        "PaperFlow\n"
        "Copyright 2026 PaperFlow contributors\n\n"
        "This product includes adapted ideas and portions from\n"
        "huangkiki/dailypaper-skills, licensed under the Apache License 2.0.\n\n"
        "huangkiki/zotero-ai-sidebar is not included in this distribution.\n"
        "It is an optional, separately installed AGPL-3.0-or-later project.\n"
    )

    license_text = _read("LICENSE")
    lines = license_text.splitlines()
    assert lines[0] == "Apache License"
    assert lines[1] == "Version 2.0, January 2004"
    assert "END OF TERMS AND CONDITIONS" in license_text


def test_pilot_checklist_has_seven_consecutive_uncompleted_dates():
    text = _read("docs/pilot/2026-08-20-checklist.md")
    header = "scheduled email | source status | duplicate | secret/log audit | notes"
    assert header in text.casefold()

    rows = re.findall(r"^\| (\d{4}-\d{2}-\d{2}) \|([^\n]+)$", text, flags=re.MULTILINE)
    assert len(rows) == 7
    dates = [date.fromisoformat(value) for value, _ in rows]
    assert all((later - earlier).days == 1 for earlier, later in zip(dates, dates[1:]))
    assert all("pending" in cells.casefold() for _, cells in rows)
    assert "七日验收已完成" not in text
