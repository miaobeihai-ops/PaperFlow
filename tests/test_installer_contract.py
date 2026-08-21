from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-windows.ps1"
UPSTREAM_LICENSE = Path(
    r"C:\Users\admin\Documents\Codex\2026-08-12\huangkiki-dailypaper-skills-https-github-com\work\dailypaper-skills\LICENSE"
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _user_path_from_registry() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "Path")[0]
    except FileNotFoundError:
        return None


def _write_command(fake_bin: Path, name: str, body: str = "@exit /b 0\r\n") -> None:
    (fake_bin / f"{name}.cmd").write_text(body, encoding="utf-8")


def _isolated_installer(tmp_path: Path, commands: tuple[str, ...]) -> dict[str, object]:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")

    project = tmp_path / "project"
    scripts = project / "scripts"
    skill_source = project / ".agents" / "skills" / "paperflow"
    scripts.mkdir(parents=True)
    skill_source.mkdir(parents=True)
    shutil.copy2(INSTALLER, scripts / INSTALLER.name)
    (project / "pyproject.toml").write_text(
        "[project]\nname = \"paperflow\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (skill_source / "SKILL.md").write_text("current skill\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_modules = tmp_path / "fake-modules"
    fake_bin.mkdir()
    fake_modules.mkdir()
    source_python = Path(sys.executable)
    (fake_modules / "pip.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.executable).with_name('paperflow.exe').write_text('fake paperflow', encoding='utf-8')\n",
        encoding="utf-8",
    )
    py_body = (
        "@echo off\r\n"
        'if "%~1"=="-c" (echo 3.12.7& exit /b 0)\r\n'
        f'if "%~1"=="-m" if "%~2"=="venv" ("{source_python}" -m venv "%~3"& exit /b %ERRORLEVEL%)\r\n'
        "exit /b 1\r\n"
    )
    _write_command(fake_bin, "py", py_body)
    for command in commands:
        _write_command(fake_bin, command)

    user_profile = tmp_path / "profile"
    appdata = tmp_path / "appdata"
    local_appdata = tmp_path / "localappdata"
    program_files = tmp_path / "program-files"
    program_files_x86 = tmp_path / "program-files-x86"
    runtime_temp = tmp_path / "runtime-temp"
    vault = tmp_path / "Vault"
    for directory in (
        user_profile,
        appdata,
        local_appdata,
        program_files,
        program_files_x86,
        runtime_temp,
        vault,
    ):
        directory.mkdir()

    skill_target = user_profile / ".agents" / "skills" / "paperflow"
    skill_target.mkdir(parents=True)
    (skill_target / "SKILL.md").write_text("old skill\n", encoding="utf-8")
    (skill_target / "stale.txt").write_text("remove me\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(user_profile),
            "USERPROFILE": str(user_profile),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local_appdata),
            "ProgramFiles": str(program_files),
            "ProgramFiles(x86)": str(program_files_x86),
            "ProgramW6432": str(program_files),
            "TEMP": str(runtime_temp),
            "TMP": str(runtime_temp),
            "PATH": str(fake_bin),
            "PYTHONPATH": str(fake_modules),
        }
    )
    subprocess.run(
        [powershell, "-NoProfile", "-Command", "exit 0"],
        env=env,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return {
        "powershell": powershell,
        "project": project,
        "installer": scripts / INSTALLER.name,
        "env": env,
        "vault": vault,
        "user_profile": user_profile,
        "appdata": appdata,
        "local_appdata": local_appdata,
        "skill_target": skill_target,
        "fake_bin": fake_bin,
    }


def _run_isolated(
    setup: dict[str, object], *arguments: str, input_text: str = "n\n"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(setup["powershell"]),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(setup["installer"]),
            *arguments,
        ],
        cwd=Path(setup["project"]),
        env=dict(setup["env"]),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


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


def test_skill_cleanup_validates_exact_target_before_recursive_delete():
    text = INSTALLER.read_text(encoding="utf-8")
    cleanup = text[text.index("if ($PSCmdlet.ShouldProcess($SkillTarget") :]

    validation_position = cleanup.index("Assert-SafeSkillTarget")
    delete_position = cleanup.index(
        "Remove-Item -LiteralPath $SkillTarget -Recurse -Force"
    )
    assert validation_position < delete_position
    assert "[System.IO.Path]::GetFullPath" in text
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


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_whatif_valid_install_is_fully_non_mutating(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    before = _snapshot(tmp_path)

    result = _run_isolated(
        setup,
        "-WhatIf",
        "-VaultPath",
        str(setup["vault"]),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "What if:" in result.stdout
    assert _snapshot(tmp_path) == before
    assert not (Path(setup["project"]) / ".venv").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_formal_install_writes_expected_files_cleans_skill_and_is_idempotent(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    registry_path_before = _user_path_from_registry()

    first = _run_isolated(setup, "-VaultPath", str(setup["vault"]))
    assert first.returncode == 0, first.stdout + first.stderr
    project = Path(setup["project"])
    wrapper = Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    config = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    skill_target = Path(setup["skill_target"])
    expected_files = (wrapper, config, skill_target / "SKILL.md")
    first_contents = {path: path.read_bytes() for path in expected_files}

    second = _run_isolated(setup, "-VaultPath", str(setup["vault"]))
    assert second.returncode == 0, second.stdout + second.stderr

    assert (project / ".venv" / "Scripts" / "python.exe").is_file()
    assert (project / ".venv" / "Scripts" / "paperflow.exe").is_file()
    assert wrapper.is_file()
    assert "%*" in wrapper.read_text(encoding="utf-8")
    assert str(project / ".venv" / "Scripts" / "paperflow.exe") in wrapper.read_text(
        encoding="utf-8"
    )
    assert tomllib.loads(config.read_text(encoding="utf-8"))["vault_path"] == str(
        Path(setup["vault"]).resolve()
    )
    assert (skill_target / "SKILL.md").read_text(encoding="utf-8") == "current skill\n"
    assert not (skill_target / "stale.txt").exists()
    assert {path: path.read_bytes() for path in expected_files} == first_contents
    assert _user_path_from_registry() == registry_path_before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_invalid_vault_fails_before_any_persistent_install_write(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    before = _snapshot(tmp_path)
    invalid_vault = tmp_path / "missing-vault"

    result = _run_isolated(setup, "-VaultPath", str(invalid_vault))

    assert result.returncode != 0
    assert "VaultPath must be an existing directory" in result.stderr
    assert _snapshot(tmp_path) == before
    assert not (Path(setup["project"]) / ".venv").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_install_missing_executes_only_missing_allowlisted_exact_ids(tmp_path):
    setup = _isolated_installer(tmp_path, ())
    winget_log = tmp_path / "winget.log"
    env = dict(setup["env"])
    env["PAPERFLOW_WINGET_LOG"] = str(winget_log)
    env["PAPERFLOW_FAKE_BIN"] = str(setup["fake_bin"])
    setup["env"] = env
    _write_command(
        Path(setup["fake_bin"]),
        "winget",
        '@echo off\r\nif "%~3"=="Git.Git" echo @exit /b 0>"%PAPERFLOW_FAKE_BIN%\\git.cmd"\r\necho %*>>"%PAPERFLOW_WINGET_LOG%"\r\nexit /b 0\r\n',
    )

    result = _run_isolated(setup, "-InstallMissing")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [line.strip() for line in winget_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        "install --id Git.Git --exact",
        "install --id DigitalScholar.Zotero --exact",
        "install --id Obsidian.Obsidian --exact",
    ]
    assert all("Codex" not in call for call in calls)


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

    license_text = _read("LICENSE").replace("\r\n", "\n").replace("\r", "\n")
    lines = license_text.splitlines()
    assert lines[0] == "Apache License"
    assert lines[1] == "Version 2.0, January 2004"
    assert "END OF TERMS AND CONDITIONS" in license_text
    assert hashlib.sha256(license_text.encode()).hexdigest() == (
        "498c6c5c534d36610b100637379845f67bd516b54b7d4aaf8a8dd6766aaef467"
    )


@pytest.mark.skipif(
    not UPSTREAM_LICENSE.exists(), reason="reviewed upstream checkout is unavailable"
)
def test_license_matches_reviewed_upstream_full_text():
    license_text = _read("LICENSE").replace("\r\n", "\n").replace("\r", "\n")
    upstream_text = (
        UPSTREAM_LICENSE.read_text(encoding="utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    assert license_text == upstream_text


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
