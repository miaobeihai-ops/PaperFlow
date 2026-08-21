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
PATH_HELPER = ROOT / "scripts" / "install-windows-path.ps1"


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
    shutil.copy2(PATH_HELPER, scripts / PATH_HELPER.name)
    (project / "pyproject.toml").write_text(
        "[project]\nname = \"paperflow\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (project / "requirements.lock").write_text(
        "httpx==0.28.1\n", encoding="utf-8"
    )
    (skill_source / "SKILL.md").write_text("current skill\n", encoding="utf-8")

    fake_bin = tmp_path / "fake-bin"
    fake_modules = tmp_path / "fake-modules"
    fake_bin.mkdir()
    fake_modules.mkdir()
    source_python = Path(sys.executable)
    (fake_modules / "pip.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "if os.environ.get('PAPERFLOW_PIP_LOG'):\n"
        "    with Path(os.environ['PAPERFLOW_PIP_LOG']).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if os.environ.get('PAPERFLOW_PIP_ENV_LOG'):\n"
        "    with Path(os.environ['PAPERFLOW_PIP_ENV_LOG']).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(json.dumps({name: os.environ.get(name) for name in "
        "['TEMP', 'TMP', 'PIP_NO_CACHE_DIR']}) + '\\n')\n"
        "if os.environ.get('PAPERFLOW_PIP_FAIL_LOCK') and '--requirement' in sys.argv:\n"
        "    raise SystemExit(8)\n"
        "if os.environ.get('PAPERFLOW_PIP_FAIL_PROJECT') and '--no-deps' in sys.argv:\n"
        "    raise SystemExit(9)\n"
        "Path(sys.executable).with_name('paperflow.exe').write_text('fake paperflow', encoding='utf-8')\n"
        "command = Path(sys.executable).with_name('paperflow.cmd')\n"
        "command.write_text('@echo off\\r\\n"
        "if defined PAPERFLOW_DOCTOR_LOG echo %*>>\"%PAPERFLOW_DOCTOR_LOG%\"\\r\\n"
        "if defined PAPERFLOW_DOCTOR_ENV_LOG (\\r\\n"
        "echo PAPERFLOW_HOME=%PAPERFLOW_HOME%>>\"%PAPERFLOW_DOCTOR_ENV_LOG%\"\\r\\n"
        "echo PAPERFLOW_CACHE_DIR=%PAPERFLOW_CACHE_DIR%>>\"%PAPERFLOW_DOCTOR_ENV_LOG%\"\\r\\n"
        "echo TMP=%TMP%>>\"%PAPERFLOW_DOCTOR_ENV_LOG%\"\\r\\n"
        "echo TEMP=%TEMP%>>\"%PAPERFLOW_DOCTOR_ENV_LOG%\"\\r\\n"
        ")\\r\\n"
        "echo {\"ok\":true}\\r\\n"
        "if defined PAPERFLOW_DOCTOR_EXIT exit /b %PAPERFLOW_DOCTOR_EXIT%\\r\\n"
        "exit /b 0\\r\\n', encoding='utf-8')\n",
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


def _run_isolated_without_registry_path_change(
    setup: dict[str, object], *arguments: str, input_text: str
) -> subprocess.CompletedProcess[str]:
    registry_path_before = _user_path_from_registry()
    result = _run_isolated(setup, *arguments, input_text=input_text)
    assert _user_path_from_registry() == registry_path_before
    return result


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_dot_sourced_installer(
    setup: dict[str, object],
    *arguments: str,
    prologue: str = "",
    epilogue: str = "",
    input_text: str = "n\n",
) -> subprocess.CompletedProcess[str]:
    command_arguments = " ".join(
        argument if argument.startswith("-") else _powershell_literal(argument)
        for argument in arguments
    )
    invocation = ". {0} {1}".format(
        _powershell_literal(str(setup["installer"])), command_arguments
    ).rstrip()
    command = "$ErrorActionPreference = 'Stop'; " + prologue + invocation + epilogue
    return subprocess.run(
        [
            str(setup["powershell"]),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=Path(setup["project"]),
        env=dict(setup["env"]),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _invoke_path_function(current_path: str, bin_dir: str) -> dict[str, object]:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        ". "
        + _powershell_literal(str(PATH_HELPER))
        + "; Add-PaperFlowPathEntry -CurrentPath "
        + _powershell_literal(current_path)
        + " -BinDir "
        + _powershell_literal(bin_dir)
        + " | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _invoke_set_path_function(
    current_path: str, bin_dir: str, legacy_bin_dir: str
) -> dict[str, object]:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        ". "
        + _powershell_literal(str(PATH_HELPER))
        + "; Set-PaperFlowPathEntry -CurrentPath "
        + _powershell_literal(current_path)
        + " -BinDir "
        + _powershell_literal(bin_dir)
        + " -LegacyBinDir "
        + _powershell_literal(legacy_bin_dir)
        + " | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_installer_declares_safe_preview_first_interface():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "[CmdletBinding(SupportsShouldProcess = $true)]" in text
    assert "[switch]$CheckOnly" in text
    assert "[switch]$InstallMissing" in text
    assert "[string]$VaultPath" in text
    assert "[string]$DataRoot" in text
    assert "Invoke-Expression" not in text
    assert "zotero.sqlite" not in text.casefold()
    assert "PAPERFLOW_GMAIL_APP_PASSWORD" not in text
    assert "PAPERFLOW_PRIVATE_CONFIG_JSON" not in text


def test_installer_declares_data_root_runtime_and_no_cache_contract():
    text = INSTALLER.read_text(encoding="utf-8")

    for assignment in (
        'set "PAPERFLOW_HOME=$ResolvedDataRoot"',
        'set "PAPERFLOW_CACHE_DIR=$CacheDir"',
        'set "TMP=$TempDir"',
        'set "TEMP=$TempDir"',
    ):
        assert assignment in text
    assert "$env:PIP_NO_CACHE_DIR = '1'" in text
    assert "Set-PaperFlowPathEntry" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_check_only_resolves_data_root_without_mutation(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    data_root = tmp_path / "data" / ".." / "PaperFlow Data"
    resolved = (tmp_path / "PaperFlow Data").resolve()
    before = _snapshot(tmp_path)

    result = _run_isolated(setup, "-CheckOnly", "-DataRoot", str(data_root))

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"DataRoot: {resolved}" in result.stdout
    assert _snapshot(tmp_path) == before
    assert not resolved.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("kind", "expected_message"),
    [
        ("relative", "DataRoot must be an absolute path"),
        ("file", "DataRoot must be a normal directory"),
        ("reparse", "DataRoot must not be a reparse point"),
    ],
)
def test_invalid_data_root_is_rejected_before_mutation(
    tmp_path, kind, expected_message
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    if kind == "relative":
        data_root = "relative-data-root"
    elif kind == "file":
        path = tmp_path / "data-root-file"
        path.write_text("not a directory", encoding="utf-8")
        data_root = str(path)
    else:
        target = tmp_path / "reparse-target"
        target.mkdir()
        path = tmp_path / "reparse-root"
        result = subprocess.run(
            [
                str(setup["powershell"]),
                "-NoProfile",
                "-Command",
                f"New-Item -ItemType Junction -Path {_powershell_literal(str(path))} "
                f"-Target {_powershell_literal(str(target))} | Out-Null",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("Could not create a junction for the reparse-point test")
        data_root = str(path)
    before = _snapshot(tmp_path)

    result = _run_isolated(setup, "-DataRoot", data_root)

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert _snapshot(tmp_path) == before
    assert not (Path(setup["project"]) / ".venv").exists()


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


def test_user_path_backend_has_no_test_seam_and_uses_user_scope_only():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "function Get-PaperFlowUserPath" in text
    assert "function Set-PaperFlowUserPath" in text
    assert "PAPERFLOW_INSTALLER_TEST_USER_PATH" not in text
    assert "GetEnvironmentVariables('Process')" not in text
    assert "[Environment]::GetEnvironmentVariable('Path', 'User')" in text
    assert "[Environment]::SetEnvironmentVariable('Path', $Value, 'User')" in text
    assert ". (Join-Path $PSScriptRoot 'install-windows-path.ps1')" in text
    path_flow = text[text.index("$userPath = Get-PaperFlowUserPath") :]
    should_process = path_flow.index("$PSCmdlet.ShouldProcess('User PATH'")
    write_call = path_flow.index("Set-PaperFlowUserPath -Value $pathUpdate.Value")
    assert should_process < write_call


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("current_path", "bin_dir", "changed", "expected"),
    [
        ("initial", r"C:\PaperFlow\bin", True, r"initial;C:\PaperFlow\bin"),
        ("", r"C:\PaperFlow\bin", True, r"C:\PaperFlow\bin"),
        (
            r"initial;c:\paperflow\BIN\\",
            r"C:\PaperFlow\bin",
            False,
            r"initial;c:\paperflow\BIN\\",
        ),
    ],
)
def test_add_path_entry_is_pure_normalized_and_idempotent(
    current_path, bin_dir, changed, expected
):
    result = _invoke_path_function(current_path, bin_dir)

    assert result == {"Changed": changed, "Value": expected}


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_set_path_entry_replaces_only_exact_legacy_entry():
    result = _invoke_set_path_function(
        r"C:\Tools;C:\Accounts\test\AppData\Local\PaperFlow\bin;C:\Other",
        r"D:\PaperFlowData\bin",
        r"C:\Accounts\test\AppData\Local\PaperFlow\bin",
    )

    assert result == {
        "Changed": True,
        "Value": r"C:\Tools;C:\Other;D:\PaperFlowData\bin",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_set_path_entry_is_case_insensitive_and_idempotent():
    result = _invoke_set_path_function(
        r"D:\paperflowdata\BIN\\;C:\Other",
        r"D:\PaperFlowData\bin",
        r"C:\Legacy\PaperFlow\bin",
    )

    assert result == {
        "Changed": False,
        "Value": r"D:\paperflowdata\BIN\\;C:\Other",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_set_path_entry_noop_preserves_blank_segments_exactly():
    current_path = r"D:\PaperFlowData\bin;;  ;C:\Other"

    result = _invoke_set_path_function(
        current_path,
        r"D:\PaperFlowData\bin",
        r"C:\Legacy\PaperFlow\bin",
    )

    assert result == {"Changed": False, "Value": current_path}


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_set_path_entry_migration_preserves_unrelated_blank_segments():
    result = _invoke_set_path_function(
        r"C:\Tools;;  ;C:\Legacy\PaperFlow\bin;;C:\Other",
        r"D:\PaperFlowData\bin",
        r"C:\Legacy\PaperFlow\bin",
    )

    assert result == {
        "Changed": True,
        "Value": r"C:\Tools;;  ;;C:\Other;D:\PaperFlowData\bin",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_set_path_entry_preserves_existing_entry_when_new_and_legacy_match():
    current_path = r"d:\paperflowdata\BIN\\"

    result = _invoke_set_path_function(
        current_path,
        r"D:\PaperFlowData\bin",
        r"D:\PAPERFLOWDATA\bin\\",
    )

    assert result == {"Changed": False, "Value": current_path}


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("current_path", "bin_dir", "legacy_bin_dir", "expected"),
    [
        ("", r"D:\PaperFlowData\bin", "", r"D:\PaperFlowData\bin"),
        (
            r" ; C:\Tools ;;;C:\Other; ",
            r"D:\PaperFlowData\bin",
            "",
            r" ; C:\Tools ;;;C:\Other; ;D:\PaperFlowData\bin",
        ),
        (
            r"C:\Legacy\bin;C:\Other;c:\legacy\BIN\\",
            r"D:\PaperFlowData\bin",
            r"C:\Legacy\bin",
            r"C:\Other;D:\PaperFlowData\bin",
        ),
    ],
)
def test_set_path_entry_handles_empty_and_legacy_edge_cases(
    current_path, bin_dir, legacy_bin_dir, expected
):
    result = _invoke_set_path_function(current_path, bin_dir, legacy_bin_dir)

    assert result == {"Changed": True, "Value": expected}


def test_installer_uses_current_agents_skill_paths_only():
    text = INSTALLER.read_text(encoding="utf-8")

    assert ".agents\\skills\\paperflow" in text
    assert ".codex\\skills" not in text.casefold()
    assert "CODEX_HOME" not in text
    assert "Join-Path $env:USERPROFILE '.agents\\skills\\paperflow'" in text


def test_skill_replacement_uses_validated_sibling_staging_and_backup():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "Assert-ValidSkillDirectory -Path $SkillSource" in text
    assert "paperflow-staging-" in text
    assert "paperflow-backup-" in text
    assert "Move-Item -LiteralPath $SkillTarget -Destination $backupPath" in text
    assert "Move-Item -LiteralPath $stagingPath -Destination $SkillTarget" in text
    assert "Remove-Item -LiteralPath $SkillTarget -Recurse -Force" not in text
    assert "$skillCommitted = $true" in text
    assert "Could not remove the previous PaperFlow Skill backup" in text
    assert "[System.IO.Path]::GetFullPath" in text
    assert "Join-Path $env:USERPROFILE '.agents\\skills\\paperflow'" in text


def test_wrapper_update_is_atomic_and_after_skill_and_config_steps():
    text = INSTALLER.read_text(encoding="utf-8")

    wrapper_position = text.index("'Create or update PaperFlow command wrapper'")
    assert wrapper_position > text.index("'Copy PaperFlow Skill for the current user'")
    assert wrapper_position > text.index("'Create local PaperFlow configuration'")
    wrapper_flow = text[wrapper_position:]
    assert "paperflow-wrapper-" in wrapper_flow
    assert "Move-Item -LiteralPath $wrapperTempPath -Destination $WrapperPath -Force" in wrapper_flow
    assert "Remove-Item -LiteralPath $wrapperTempPath -Force" in wrapper_flow


def test_new_config_write_is_atomic_and_existing_config_is_preserved():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "Local config preserved:" in text
    assert "'Create local PaperFlow configuration'" in text
    config_flow = text[text.index("if ($VaultPath)") :]
    assert "paperflow-config-" in config_flow
    assert "Move-Item -LiteralPath $configTempPath -Destination $ConfigPath -Force" in config_flow
    assert "Remove-Item -LiteralPath $configTempPath -Force" in config_flow


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("invalid_source", ["missing-directory", "missing-manifest", "empty-manifest"])
def test_invalid_skill_source_fails_before_mutation_and_preserves_old_install(
    tmp_path, invalid_source
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    skill_source = Path(setup["project"]) / ".agents" / "skills" / "paperflow"
    if invalid_source == "missing-directory":
        shutil.rmtree(skill_source)
    elif invalid_source == "missing-manifest":
        (skill_source / "SKILL.md").unlink()
    else:
        (skill_source / "SKILL.md").write_text("  \r\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = _run_isolated(setup)

    wrapper = Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    assert result.returncode != 0
    assert "valid non-empty SKILL.md" in result.stderr
    assert _snapshot(tmp_path) == before
    assert (Path(setup["skill_target"]) / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "old skill\n"
    assert not wrapper.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_staged_skill_replacement_failure_restores_old_skill_and_cleans_temps(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    skill_target = Path(setup["skill_target"])
    target_literal = _powershell_literal(str(skill_target))
    prologue = (
        f"$paperflowTestTarget = {target_literal}; "
        "function Move-Item { param([string]$LiteralPath, [string]$Destination, [switch]$Force); "
        "if (($LiteralPath -like '*.paperflow-staging-*') -and "
        "[System.StringComparer]::OrdinalIgnoreCase.Equals($Destination, $paperflowTestTarget)) "
        "{ throw 'controlled staged replacement failure' }; "
        "Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters }; "
    )

    result = _run_dot_sourced_installer(setup, prologue=prologue)

    wrapper = Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    assert result.returncode != 0
    assert "controlled staged replacement failure" in result.stderr
    assert (skill_target / "SKILL.md").read_text(encoding="utf-8") == "old skill\n"
    assert (skill_target / "stale.txt").is_file()
    assert not wrapper.exists()
    assert not list(skill_target.parent.glob(".paperflow-staging-*"))
    assert not list(skill_target.parent.glob(".paperflow-backup-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_partial_backup_cleanup_failure_keeps_committed_new_skill(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    skill_target = Path(setup["skill_target"])
    prologue = (
        "$paperflowBackupFailureInjected = $false; "
        "function Remove-Item { param([string]$LiteralPath, [switch]$Recurse, [switch]$Force); "
        "if ((-not $script:paperflowBackupFailureInjected) -and "
        "($LiteralPath -like '*.paperflow-backup-*')) "
        "{ $script:paperflowBackupFailureInjected = $true; "
        "Microsoft.PowerShell.Management\\Remove-Item -LiteralPath "
        "(Join-Path $LiteralPath 'stale.txt') -Force; "
        "throw 'controlled partial backup cleanup failure' }; "
        "Microsoft.PowerShell.Management\\Remove-Item @PSBoundParameters }; "
    )

    result = _run_dot_sourced_installer(setup, prologue=prologue)

    wrapper = Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Could not remove the previous PaperFlow Skill backup" in result.stdout
    assert "controlled partial backup cleanup failure" in " ".join(
        result.stdout.split()
    )
    assert (skill_target / "SKILL.md").read_text(encoding="utf-8") == "current skill\n"
    assert not (skill_target / "stale.txt").exists()
    assert wrapper.is_file()
    assert not list(skill_target.parent.glob(".paperflow-staging-*"))
    backups = list(skill_target.parent.glob(".paperflow-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "SKILL.md").read_text(encoding="utf-8") == "old skill\n"
    assert not (backups[0] / "stale.txt").exists()


def test_destination_preflight_runs_before_any_install_mutation():
    text = INSTALLER.read_text(encoding="utf-8")
    main = text[text.index("$state = Get-InstallationState") :]

    preflight = main.index("Assert-InstallDestinationPreflight")
    assert preflight < main.index("if ($InstallMissing)")
    assert preflight < main.index("'Create PaperFlow virtual environment'")
    assert "Assert-RegularFileOrMissing -Path $ConfigPath" in text
    assert "Assert-RegularFileOrMissing -Path $WrapperPath" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    "conflict_kind",
    [
        "config-target-directory",
        "wrapper-target-directory",
        "config-parent-file",
        "wrapper-parent-file",
    ],
)
def test_destination_conflict_fails_before_all_persistent_mutations(
    tmp_path, conflict_kind
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    config_dir = Path(setup["appdata"]) / "PaperFlow"
    config_path = config_dir / "config.toml"
    bin_dir = Path(setup["local_appdata"]) / "PaperFlow" / "bin"
    wrapper_path = bin_dir / "paperflow.cmd"
    if conflict_kind == "config-target-directory":
        config_path.mkdir(parents=True)
    elif conflict_kind == "wrapper-target-directory":
        wrapper_path.mkdir(parents=True)
    elif conflict_kind == "config-parent-file":
        config_dir.write_text("blocking file", encoding="utf-8")
    else:
        bin_dir.parent.mkdir(parents=True)
        bin_dir.write_text("blocking file", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = _run_isolated_without_registry_path_change(
        setup, "-VaultPath", str(setup["vault"]), input_text="n\n"
    )

    assert result.returncode != 0
    assert "must be a regular file or not exist" in result.stderr
    assert _snapshot(tmp_path) == before
    assert not (Path(setup["project"]) / ".venv").exists()
    assert (Path(setup["skill_target"]) / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "old skill\n"
    assert (Path(setup["skill_target"]) / "stale.txt").is_file()
    assert not list(Path(setup["skill_target"]).parent.glob(".paperflow-*-*"))


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
    assert tomllib.loads(config.read_text(encoding="utf-8"))["vault_path"] == str(
        Path(setup["vault"]).resolve()
    )
    custom_config = (
        b'vault_path = "D:\\\\CustomVault"\n'
        b'timezone = "Europe/London"\n\n'
        b'[keywords]\ncustom = 99\n'
    )
    config.write_bytes(custom_config)
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
    assert "preserved" in second.stdout.casefold()
    assert config.read_bytes() == custom_config
    assert (skill_target / "SKILL.md").read_text(encoding="utf-8") == "current skill\n"
    assert not (skill_target / "stale.txt").exists()
    assert {path: path.read_bytes() for path in expected_files} == first_contents
    assert _user_path_from_registry() == registry_path_before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_data_root_install_creates_layout_and_exact_wrapper_environment(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    data_root = tmp_path / "PaperFlow Data"

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), "-VaultPath", str(setup["vault"])
    )

    assert result.returncode == 0, result.stdout + result.stderr
    wrapper = data_root / "bin" / "paperflow.cmd"
    config = data_root / "config" / "config.toml"
    assert wrapper.is_file()
    assert config.is_file()
    assert (data_root / "cache").is_dir()
    assert (data_root / "tmp").is_dir()
    wrapper_lines = wrapper.read_text(encoding="utf-8").splitlines()
    assert wrapper_lines[:5] == [
        "@echo off",
        f'set "PAPERFLOW_HOME={data_root.resolve()}"',
        f'set "PAPERFLOW_CACHE_DIR={data_root.resolve() / "cache"}"',
        f'set "TMP={data_root.resolve() / "tmp"}"',
        f'set "TEMP={data_root.resolve() / "tmp"}"',
    ]
    assert wrapper_lines[-1].endswith('paperflow.exe" %*')
    assert not (Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd").exists()
    assert not (Path(setup["appdata"]) / "PaperFlow" / "config.toml").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_data_root_copies_legacy_config_bytes_atomically_without_overwriting(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    data_root = tmp_path / "PaperFlow Data"
    legacy_config = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    legacy_config.parent.mkdir(parents=True)
    legacy_bytes = b'vault_path = "D:\\\\Legacy"\r\n# preserve bytes: \xff\x00\r\n'
    legacy_config.write_bytes(legacy_bytes)

    first = _run_isolated(setup, "-DataRoot", str(data_root))
    assert first.returncode == 0, first.stdout + first.stderr
    new_config = data_root / "config" / "config.toml"
    assert new_config.read_bytes() == legacy_bytes
    assert legacy_config.read_bytes() == legacy_bytes
    assert not list(new_config.parent.glob(".paperflow-config-*"))

    replacement = b'vault_path = "D:\\\\KeepNew"\ncustom = true\n'
    new_config.write_bytes(replacement)
    legacy_config.write_bytes(b"changed legacy")
    second = _run_isolated(setup, "-DataRoot", str(data_root))
    assert second.returncode == 0, second.stdout + second.stderr
    assert new_config.read_bytes() == replacement
    assert legacy_config.read_bytes() == b"changed legacy"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("original_no_cache", [None, "keep-original"])
def test_data_root_pip_environment_is_scoped_and_restored(tmp_path, original_no_cache):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    data_root = tmp_path / "PaperFlow Data"
    pip_env_log = tmp_path / "pip-env.log"
    restored_log = tmp_path / "restored-env.json"
    setup["env"] = {
        **dict(setup["env"]),
        "PAPERFLOW_PIP_ENV_LOG": str(pip_env_log),
    }
    if original_no_cache is None:
        setup["env"].pop("PIP_NO_CACHE_DIR", None)
    else:
        setup["env"]["PIP_NO_CACHE_DIR"] = original_no_cache
    epilogue = (
        "; @{ TEMP = $env:TEMP; TMP = $env:TMP; "
        "PIP_NO_CACHE_DIR = $env:PIP_NO_CACHE_DIR; "
        "PipNoCachePresent = Test-Path Env:PIP_NO_CACHE_DIR } | ConvertTo-Json -Compress | "
        f"Set-Content -LiteralPath {_powershell_literal(str(restored_log))} -Encoding UTF8"
    )

    result = _run_dot_sourced_installer(
        setup, "-DataRoot", str(data_root), epilogue=epilogue
    )

    assert result.returncode == 0, result.stdout + result.stderr
    expected_tmp = str((data_root / "tmp").resolve())
    pip_environments = [
        json.loads(line) for line in pip_env_log.read_text(encoding="utf-8").splitlines()
    ]
    assert pip_environments == [
        {"TEMP": expected_tmp, "TMP": expected_tmp, "PIP_NO_CACHE_DIR": "1"},
        {"TEMP": expected_tmp, "TMP": expected_tmp, "PIP_NO_CACHE_DIR": "1"},
    ]
    restored = json.loads(restored_log.read_text(encoding="utf-8-sig"))
    assert restored["TEMP"] == str(Path(setup["env"]["TEMP"]))
    assert restored["TMP"] == str(Path(setup["env"]["TMP"]))
    assert restored["PipNoCachePresent"] is (original_no_cache is not None)
    if original_no_cache is not None:
        assert restored["PIP_NO_CACHE_DIR"] == original_no_cache


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_formal_install_consumes_runtime_lock_then_installs_project_without_deps(
    tmp_path,
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    pip_log = tmp_path / "pip.log"
    setup["env"] = {**dict(setup["env"]), "PAPERFLOW_PIP_LOG": str(pip_log)}

    result = _run_isolated(setup)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in pip_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        [
            "install",
            "--requirement",
            str(Path(setup["project"]) / "requirements.lock"),
        ],
        [
            "install",
            "--no-deps",
            "--no-build-isolation",
            str(Path(setup["project"])),
        ],
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("failure_flag", "expected_message"),
    [
        ("PAPERFLOW_PIP_FAIL_LOCK", "Locked runtime dependency installation failed"),
        ("PAPERFLOW_PIP_FAIL_PROJECT", "PaperFlow package installation failed"),
    ],
)
def test_each_locked_install_step_checks_failure_and_stops_before_doctor(
    tmp_path, failure_flag, expected_message
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    doctor_log = tmp_path / "doctor.log"
    setup["env"] = {
        **dict(setup["env"]),
        failure_flag: "1",
        "PAPERFLOW_DOCTOR_LOG": str(doctor_log),
    }

    result = _run_isolated(setup)

    assert result.returncode != 0
    assert expected_message in result.stderr
    assert not doctor_log.exists()


def _enable_fake_doctor(
    setup: dict[str, object], log_path: Path, *, exit_code: int = 0
) -> None:
    setup["env"] = {
        **dict(setup["env"]),
        "PAPERFLOW_DOCTOR_LOG": str(log_path),
        "PAPERFLOW_DOCTOR_EXIT": str(exit_code),
    }


def _write_legacy_install(
    setup: dict[str, object], *, keep_neighbor: bool = False
) -> tuple[Path, Path, bytes, bytes]:
    legacy_wrapper = (
        Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    )
    legacy_config = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    legacy_wrapper.parent.mkdir(parents=True)
    legacy_config.parent.mkdir(parents=True)
    wrapper_bytes = b"@echo off\r\necho exact legacy wrapper\r\n"
    config_bytes = b'vault_path = "D:\\\\ExactLegacy"\r\ncustom = 7\r\n'
    legacy_wrapper.write_bytes(wrapper_bytes)
    legacy_config.write_bytes(config_bytes)
    if keep_neighbor:
        (legacy_wrapper.parent.parent / "keep.txt").write_text(
            "preserve me", encoding="utf-8"
        )
    return legacy_wrapper, legacy_config, wrapper_bytes, config_bytes


def _preprovision_fake_venv(setup: dict[str, object]) -> None:
    venv = Path(setup["project"]) / ".venv"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(sys.executable, scripts / "python.exe")
    shutil.copy2(Path(sys.executable).parents[1] / "pyvenv.cfg", venv / "pyvenv.cfg")


def _use_file_backed_user_path(setup: dict[str, object], value: str) -> Path:
    path_file = Path(setup["project"]) / "isolated-user-path.txt"
    path_file.write_text(value, encoding="utf-8")
    installer = Path(setup["installer"])
    text = installer.read_text(encoding="utf-8")
    original = """function Get-PaperFlowUserPath {
    return [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Set-PaperFlowUserPath {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    [Environment]::SetEnvironmentVariable('Path', $Value, 'User')
}"""
    replacement = f"""function Get-PaperFlowUserPath {{
    return [System.IO.File]::ReadAllText({_powershell_literal(str(path_file))})
}}

function Set-PaperFlowUserPath {{
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    [System.IO.File]::WriteAllText({_powershell_literal(str(path_file))}, $Value)
}}"""
    assert original in text
    installer.write_text(text.replace(original, replacement), encoding="utf-8")
    return path_file


def test_data_root_doctor_precedes_path_commit_and_legacy_cleanup():
    text = INSTALLER.read_text(encoding="utf-8")
    doctor_position = text.index("& $VenvPaperFlowDoctor --json doctor")
    path_position = text.index("$userPath = Get-PaperFlowUserPath")
    cleanup_position = text.rindex("Remove-LegacyPaperFlowFiles")

    assert doctor_position < path_position < cleanup_position


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_formal_install_runs_read_only_doctor_after_wrapper_creation(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    doctor_log = tmp_path / "doctor.log"
    _enable_fake_doctor(setup, doctor_log)

    result = _run_isolated(setup, "-VaultPath", str(setup["vault"]))

    assert result.returncode == 0, result.stdout + result.stderr
    assert doctor_log.read_text(encoding="utf-8").splitlines() == ["--json doctor"]
    assert (Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_successful_data_root_migrates_config_wrapper_path_and_preserves_neighbor(
    tmp_path,
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    doctor_env_log = tmp_path / "doctor-env.log"
    setup["env"] = {
        **dict(setup["env"]),
        "PAPERFLOW_DOCTOR_ENV_LOG": str(doctor_env_log),
    }
    legacy_wrapper, legacy_config, _, config_bytes = _write_legacy_install(
        setup, keep_neighbor=True
    )
    legacy_bin = str(legacy_wrapper.parent)
    initial_path = rf"C:\Other;{legacy_bin}"

    path_file = _use_file_backed_user_path(setup, initial_path)
    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )
    migrated_path = path_file.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert migrated_path == rf"C:\Other;{data_root.resolve()}\bin"
    assert (data_root / "config" / "config.toml").read_bytes() == config_bytes
    assert (data_root / "bin" / "paperflow.cmd").is_file()
    assert not legacy_wrapper.exists()
    assert not legacy_config.exists()
    assert (legacy_wrapper.parent.parent / "keep.txt").read_text(encoding="utf-8") == "preserve me"
    assert doctor_env_log.read_text(encoding="utf-8").splitlines() == [
        f"PAPERFLOW_HOME={data_root.resolve()}",
        f"PAPERFLOW_CACHE_DIR={data_root.resolve() / 'cache'}",
        f"TMP={data_root.resolve() / 'tmp'}",
        f"TEMP={data_root.resolve() / 'tmp'}",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_data_root_doctor_failure_preserves_exact_legacy_files_and_path(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    doctor_log = tmp_path / "doctor.log"
    _enable_fake_doctor(setup, doctor_log, exit_code=23)
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"

    path_file = _use_file_backed_user_path(setup, initial_path)
    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )
    current_path = path_file.read_text(encoding="utf-8")

    assert result.returncode != 0
    assert "PaperFlow doctor exited with code 23" in result.stderr
    assert doctor_log.read_text(encoding="utf-8").splitlines() == ["--json doctor"]
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes
    assert current_path == initial_path


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_data_root_path_decline_preserves_exact_legacy_files_and_path(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"

    path_file = _use_file_backed_user_path(setup, initial_path)
    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="n\n"
    )
    current_path = path_file.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert current_path == initial_path
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_nonzero_doctor_warns_with_real_exit_code_without_failing_install(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    doctor_log = tmp_path / "doctor.log"
    _enable_fake_doctor(setup, doctor_log, exit_code=7)

    result = _run_isolated(setup)

    assert result.returncode == 0, result.stdout + result.stderr
    assert doctor_log.read_text(encoding="utf-8").splitlines() == ["--json doctor"]
    assert "doctor exited with code 7" in result.stdout
    assert "Review the doctor JSON output" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("mode", ["check-only", "what-if", "install-failure"])
def test_non_installing_or_failed_paths_do_not_run_doctor(tmp_path, mode):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    doctor_log = tmp_path / "doctor.log"
    _enable_fake_doctor(setup, doctor_log)
    arguments: tuple[str, ...]
    if mode == "check-only":
        arguments = ("-CheckOnly",)
    elif mode == "what-if":
        arguments = ("-WhatIf",)
    else:
        setup["env"] = {
            **dict(setup["env"]),
            "PAPERFLOW_PIP_FAIL_PROJECT": "1",
        }
        arguments = ()

    result = _run_isolated(setup, *arguments)

    if mode == "install-failure":
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stdout + result.stderr
    assert not doctor_log.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_config_replacement_failure_leaves_no_partial_file_or_temp(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    config_path = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    config_literal = _powershell_literal(str(config_path))
    prologue = (
        f"$paperflowTestConfig = {config_literal}; "
        "function Move-Item { param([string]$LiteralPath, [string]$Destination, [switch]$Force); "
        "if (($LiteralPath -like '*.paperflow-config-*') -and "
        "[System.StringComparer]::OrdinalIgnoreCase.Equals($Destination, $paperflowTestConfig)) "
        "{ throw 'controlled config replacement failure' }; "
        "Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters }; "
    )

    result = _run_dot_sourced_installer(
        setup, "-VaultPath", str(setup["vault"]), prologue=prologue
    )

    wrapper = Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    assert result.returncode != 0
    assert "controlled config replacement failure" in result.stderr
    assert not config_path.exists()
    assert not list(config_path.parent.glob(".paperflow-config-*"))
    assert not wrapper.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_whatif_path_consent_changes_neither_tree_nor_registry(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    before = _snapshot(tmp_path)

    result = _run_isolated_without_registry_path_change(
        setup,
        "-WhatIf",
        "-VaultPath",
        str(setup["vault"]),
        input_text="y\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "What if:" in result.stdout
    assert _snapshot(tmp_path) == before


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
    assert "python -m pip install -r requirements-dev.lock" in text
    assert "python -m pip install --no-deps --no-build-isolation -e ." in text
    assert "python -m pip install --no-deps -e ." not in text
    assert 'python -m pip install -e ".[dev]"' not in text
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
    assert "已有 config.toml 会逐字节保留" in text
    assert "不会覆盖 keywords、timezone" in text
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


def test_readme_documents_locked_installs_date_semantics_and_post_install_doctor():
    text = _read("README.md")

    assert "requirements.lock" in text
    assert "requirements-dev.lock" in text
    assert "--date 2026-08-20" in text
    assert "三个来源都按论文发布日期" in text
    assert "旧日期可能为空" in text
    assert "源的保留范围" in text
    assert "安装末尾" in text
    assert "只读 `paperflow --json doctor`" in text
    assert "warning" in text.casefold()
    assert "精确版本级可复现" in text
    assert "固定构建环境" in text
    assert "不包含制品哈希" in text
    assert "不声称 hermetic" in text
    assert "artifact-level 防篡改" in text


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


def test_license_proof_has_no_developer_machine_dependency():
    test_source = Path(__file__).read_text(encoding="utf-8")

    assert ("UPSTREAM" + "_LICENSE") not in test_source
    assert ("C:" + "\\Users\\") not in test_source


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
