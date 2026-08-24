from __future__ import annotations

import base64
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


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"README section not found: {heading}"
    return match.group(1)


def _fenced_blocks(text: str, language: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(
            rf"^```{re.escape(language)}\s*$\n(.*?)^```\s*$",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
    ]


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _local_config_bytes(vault_path: Path) -> bytes:
    return (
        f"vault_path = {json.dumps(str(vault_path))}\n"
        'arxiv_categories = ["cs.AI"]\n'
        "\n[keywords]\n"
        "robotics = 5\n"
    ).encode("utf-8")


def _write_command(fake_bin: Path, name: str, body: str = "@exit /b 0\r\n") -> None:
    (fake_bin / f"{name}.cmd").write_text(body, encoding="utf-8")


def _create_junction(powershell: str, path: Path, target: Path) -> None:
    result = subprocess.run(
        [
            powershell,
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


def _install_file_backed_path_fixture(
    powershell: str,
    installer: Path,
    path_file: Path,
    no_persist_file: Path,
    behavior_file: Path,
    setter_count_file: Path,
) -> None:
    command = (
        "$tokens = $null; $errors = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        + _powershell_literal(str(installer))
        + ", [ref]$tokens, [ref]$errors); "
        "$definitions = $ast.FindAll({ param($node) $node -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -in @('Get-PaperFlowUserPath', 'Set-PaperFlowUserPath') }, $true); "
        "if ($definitions.Count -ne 2) { throw 'PATH persistence functions not found' }; "
        "$setDefinition = $definitions | Where-Object Name -eq 'Set-PaperFlowUserPath'; "
        "@{ SetStart = $setDefinition.Extent.StartOffset; "
        "SetEnd = $setDefinition.Extent.EndOffset } | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    extent = json.loads(result.stdout)
    with installer.open("r", encoding="utf-8", newline="") as stream:
        text = stream.read()
    registry_write = "[Environment]::SetEnvironmentVariable('Path', $Value, 'User')"
    assert text.count(registry_write) == 1
    write_offset = text.index(registry_write)
    assert extent["SetStart"] <= write_offset < extent["SetEnd"]
    fixture = f"""

# Isolated test fixture: all user PATH persistence is file-backed.
function Get-PaperFlowUserPath {{
    if (-not (Test-Path -LiteralPath {_powershell_literal(str(path_file))})) {{
        return $null
    }}
    return [System.IO.File]::ReadAllText({_powershell_literal(str(path_file))})
}}

function Set-PaperFlowUserPath {{
    param([Parameter(Mandatory = $true)][AllowNull()][AllowEmptyString()]$Value)

    if (Test-Path -LiteralPath {_powershell_literal(str(no_persist_file))}) {{
        return
    }}
    $behavior = if (Test-Path -LiteralPath {_powershell_literal(str(behavior_file))}) {{
        [System.IO.File]::ReadAllText({_powershell_literal(str(behavior_file))})
    }} else {{ '' }}
    if ($behavior -eq 'always-corrupt') {{
        [System.IO.File]::WriteAllText({_powershell_literal(str(path_file))}, 'CORRUPTED')
        return
    }}
    if ($behavior -eq 'corrupt-once') {{
        $setterCount = if (Test-Path -LiteralPath {_powershell_literal(str(setter_count_file))}) {{
            [int][System.IO.File]::ReadAllText({_powershell_literal(str(setter_count_file))})
        }} else {{ 0 }}
        [System.IO.File]::WriteAllText({_powershell_literal(str(setter_count_file))}, [string]($setterCount + 1))
        if ($setterCount -eq 0) {{
            [System.IO.File]::WriteAllText({_powershell_literal(str(path_file))}, 'CORRUPTED')
            return
        }}
    }}
    if ($null -eq $Value) {{
        Remove-Item -LiteralPath {_powershell_literal(str(path_file))} -Force -ErrorAction SilentlyContinue
        return
    }}
    [System.IO.File]::WriteAllText({_powershell_literal(str(path_file))}, $Value)
}}
"""
    offset = extent["SetEnd"]
    with installer.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text[:offset] + fixture + text[offset:])


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
    path_file = project / "isolated-user-path.txt"
    no_persist_file = project / "isolated-user-path-no-persist"
    behavior_file = project / "isolated-user-path-behavior"
    setter_count_file = project / "isolated-user-path-setter-count"
    path_file.write_text("", encoding="utf-8")
    _install_file_backed_path_fixture(
        powershell,
        scripts / INSTALLER.name,
        path_file,
        no_persist_file,
        behavior_file,
        setter_count_file,
    )
    (project / "pyproject.toml").write_text(
        "[project]\nname = \"paperflow\"\nversion = \"0.1.0\"\n",
        encoding="utf-8",
    )
    (project / "requirements.lock").write_text(
        "httpx==0.28.1\n", encoding="utf-8"
    )
    shutil.copytree(ROOT / "src" / "paperflow", project / "src" / "paperflow")
    (project / "config").mkdir()
    shutil.copy2(ROOT / "config" / "topics.toml", project / "config" / "topics.toml")
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
        "command = Path(sys.executable).with_name('paperflow.cmd')\n"
        "command.write_text('@echo off\\r\\n"
        "if defined PAPERFLOW_DOCTOR_LOG echo %*>>\"%PAPERFLOW_DOCTOR_LOG%\"\\r\\n"
        "if defined PAPERFLOW_DOCTOR_ENV_LOG (\\r\\n"
        "echo PAPERFLOW_HOME=%PAPERFLOW_HOME%>>\"%PAPERFLOW_DOCTOR_ENV_LOG%\"\\r\\n"
        "echo PAPERFLOW_TOPICS_PATH=%PAPERFLOW_TOPICS_PATH%>>\"%PAPERFLOW_DOCTOR_ENV_LOG%\"\\r\\n"
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
        'if "%~1"=="-c" if "%~2"=="import platform; print(platform.python_version())" '
        "(echo 3.12.7& exit /b 0)\r\n"
        f'"{source_python}" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
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
        "user_path_file": path_file,
        "user_path_no_persist_file": no_persist_file,
        "user_path_behavior_file": behavior_file,
        "user_path_setter_count_file": setter_count_file,
    }


def _run_isolated(
    setup: dict[str, object], *arguments: str, input_text: str | None = "n\n"
) -> subprocess.CompletedProcess[str]:
    stdin_arguments = (
        {"input": input_text}
        if input_text is not None
        else {"stdin": subprocess.DEVNULL}
    )
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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **stdin_arguments,
    )


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run_dot_sourced_installer(
    setup: dict[str, object],
    *arguments: str,
    prologue: str = "",
    epilogue: str = "",
    input_text: str | None = "n\n",
) -> subprocess.CompletedProcess[str]:
    command_arguments = " ".join(
        argument if argument.startswith("-") else _powershell_literal(argument)
        for argument in arguments
    )
    invocation = ". {0} {1}".format(
        _powershell_literal(str(setup["installer"])), command_arguments
    ).rstrip()
    command = "$ErrorActionPreference = 'Stop'; " + prologue + invocation + epilogue
    stdin_arguments = (
        {"input": input_text}
        if input_text is not None
        else {"stdin": subprocess.DEVNULL}
    )
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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        **stdin_arguments,
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


def _invoke_installer_predicate(function_name: str, value: str) -> bool:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        "$ErrorActionPreference = 'Stop'; $tokens = $null; $errors = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        + _powershell_literal(str(INSTALLER))
        + ", [ref]$tokens, [ref]$errors); "
        "$definition = $ast.Find({ param($node) $node -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        f"$node.Name -eq {_powershell_literal(function_name)} }}, $true); "
        "if ($null -eq $definition) { throw 'installer function not found' }; "
        ". ([scriptblock]::Create($definition.Extent.Text)); "
        f"& {_powershell_literal(function_name)} -Path {_powershell_literal(value)} "
        "| ConvertTo-Json -Compress"
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


def _invoke_atomic_config_copy(source: Path, destination: Path):
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        "$ErrorActionPreference = 'Stop'; $tokens = $null; $errors = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        + _powershell_literal(str(INSTALLER))
        + ", [ref]$tokens, [ref]$errors); "
        "$definition = $ast.Find({ param($node) $node -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Copy-FileBytesAtomically' }, $true); "
        "if ($null -eq $definition) { throw 'installer function not found' }; "
        ". ([scriptblock]::Create($definition.Extent.Text)); "
        "Copy-FileBytesAtomically -Source "
        + _powershell_literal(str(source))
        + " -Destination "
        + _powershell_literal(str(destination))
    )
    return subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def _powershell_function_extent(script: Path, function_name: str) -> tuple[int, int]:
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    command = (
        "$tokens = $null; $errors = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        + _powershell_literal(str(script))
        + ", [ref]$tokens, [ref]$errors); "
        "$definition = $ast.Find({ param($node) $node -is "
        "[System.Management.Automation.Language.FunctionDefinitionAst] -and "
        f"$node.Name -eq {_powershell_literal(function_name)} }}, $true); "
        "if ($null -eq $definition) { throw 'installer function not found' }; "
        "@($definition.Extent.StartOffset, $definition.Extent.EndOffset) "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    start, end = json.loads(result.stdout)
    return start, end


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
    assert "PAPERFLOW_MAIL_TO" not in text


def test_installer_declares_data_root_runtime_and_no_cache_contract():
    text = INSTALLER.read_text(encoding="utf-8")

    for assignment in (
        'set "PAPERFLOW_HOME=$wrapperHome"',
        'set "PAPERFLOW_TOPICS_PATH=$wrapperTopics"',
        'set "PAPERFLOW_CACHE_DIR=$wrapperCache"',
        'set "TMP=$wrapperTemp"',
        'set "TEMP=$wrapperTemp"',
    ):
        assert assignment in text
    assert "setlocal DisableDelayedExpansion" in text
    assert "endlocal & exit /b %PAPERFLOW_EXIT_CODE%" in text
    assert "return $Path.Replace('%', '%%')" in text
    assert "$env:PIP_NO_CACHE_DIR = '1'" in text
    assert "Set-PaperFlowPathEntry" in text
    assert "$TopicsPath = Join-Path $ProjectRoot 'config\\topics.toml'" in text
    assert "PaperFlow topic file must not be a reparse point." in text


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_missing_project_topic_file_fails_before_persistent_writes(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    topics_path = Path(setup["project"]) / "config" / "topics.toml"
    topics_path.unlink()
    before = _snapshot(tmp_path)
    data_root = tmp_path / "PaperFlow Data"

    result = _run_isolated(setup, "-DataRoot", str(data_root))

    assert result.returncode != 0
    assert "PaperFlow topic file was not found." in result.stderr
    assert _snapshot(tmp_path) == before
    assert not data_root.exists()


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
    ("value", "expected"),
    [
        (r"C:\PaperFlow Data", True),
        (r"\\server\share\PaperFlow Data", False),
        (r"C:drive-relative", False),
        (r"\root-relative", False),
        ("relative", False),
    ],
)
def test_absolute_windows_path_predicate(value, expected):
    assert _invoke_installer_predicate("Test-AbsoluteWindowsPath", value) is expected


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_atomic_config_copy_collision_preserves_target_and_cleans_temp(tmp_path):
    source = tmp_path / "legacy-config.toml"
    destination = tmp_path / "config" / "config.toml"
    source_bytes = b'vault_path = "legacy"\r\n\x00legacy-bytes'
    target_bytes = b'vault_path = "concurrent"\n'
    source.write_bytes(source_bytes)
    destination.parent.mkdir()
    destination.write_bytes(target_bytes)

    result = _invoke_atomic_config_copy(source, destination)

    assert result.returncode != 0
    assert destination.read_bytes() == target_bytes
    assert source.read_bytes() == source_bytes
    assert not list(destination.parent.glob(".paperflow-config-*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("kind", "expected_message"),
    [
        ("relative", "DataRoot must be a drive-absolute local path"),
        ("drive-relative", "DataRoot must be a drive-absolute local path"),
        ("root-relative", "DataRoot must be a drive-absolute local path"),
        ("unc", "DataRoot must be a drive-absolute local path"),
        ("semicolon", "DataRoot cannot contain a semicolon"),
        ("file", "DataRoot must be a normal directory"),
        ("reparse", "DataRoot ancestor must not be a reparse point"),
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
    elif kind == "drive-relative":
        data_root = r"C:drive-relative-data-root"
    elif kind == "root-relative":
        data_root = r"\root-relative-data-root"
    elif kind == "unc":
        data_root = r"\\server\share\PaperFlowData"
    elif kind == "semicolon":
        data_root = str(tmp_path / "PaperFlow;Data")
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


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("mode", ["check-only", "formal"])
@pytest.mark.parametrize("relation", ["equal", "nested", "ancestor"])
@pytest.mark.parametrize(
    "target_kind", ["project-root", "skill-source", "skill-target", "vault"]
)
def test_data_root_overlap_is_rejected_before_mutation(
    tmp_path, target_kind, relation, mode
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    vault = tmp_path / "vault-container" / "Vault"
    vault.mkdir(parents=True)
    targets = {
        "project-root": Path(setup["project"]),
        "skill-source": Path(setup["project"])
        / ".agents"
        / "skills"
        / "paperflow",
        "skill-target": Path(setup["skill_target"]),
        "vault": vault,
    }
    target = targets[target_kind]
    sentinel = target / "overlap-sentinel.txt"
    sentinel_bytes = f"preserve {target_kind} {relation}".encode("utf-8")
    sentinel.write_bytes(sentinel_bytes)
    if relation == "equal":
        data_root = target
    elif relation == "nested":
        data_root = target / "nested-data-root"
    else:
        data_root = target.parent
    before = _snapshot(tmp_path)
    arguments = ["-DataRoot", str(data_root), "-VaultPath", str(vault)]
    if mode == "check-only":
        arguments.insert(0, "-CheckOnly")

    result = _run_isolated(setup, *arguments)

    assert result.returncode != 0
    assert "DataRoot must not overlap" in result.stderr
    assert sentinel.read_bytes() == sentinel_bytes
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("mode", ["check-only", "formal"])
@pytest.mark.parametrize("config_source", ["destination", "legacy"])
def test_config_derived_vault_overlap_is_rejected_before_mutation(
    tmp_path, config_source, mode
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    if config_source == "destination":
        data_root = tmp_path / "Existing DataRoot"
        config_path = data_root / "config" / "config.toml"
        config_path.parent.mkdir(parents=True)
        effective_vault = data_root
    else:
        effective_vault = tmp_path / "Vault Parent"
        effective_vault.mkdir()
        data_root = effective_vault / "Nested DataRoot"
        config_path = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
        config_path.parent.mkdir(parents=True)
    config_path.write_bytes(_local_config_bytes(effective_vault))
    sentinel = effective_vault / "config-overlap-sentinel.txt"
    sentinel_bytes = f"preserve {config_source} {mode}".encode("utf-8")
    sentinel.write_bytes(sentinel_bytes)
    before = _snapshot(tmp_path)
    arguments = ["-DataRoot", str(data_root)]
    if mode == "check-only":
        arguments.insert(0, "-CheckOnly")

    result = _run_isolated(setup, *arguments, input_text="n\n")

    assert result.returncode != 0
    assert "DataRoot must not overlap effective Vault" in result.stderr
    assert sentinel.read_bytes() == sentinel_bytes
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("mode", ["check-only", "formal"])
@pytest.mark.parametrize("config_source", ["destination", "legacy"])
def test_disjoint_config_derived_vault_is_accepted_without_vault_argument(
    tmp_path, config_source, mode
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    if config_source == "destination":
        config_path = data_root / "config" / "config.toml"
    else:
        config_path = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(_local_config_bytes(Path(setup["vault"])))
    before = _snapshot(tmp_path)
    arguments = ["-DataRoot", str(data_root)]
    if mode == "check-only":
        arguments.insert(0, "-CheckOnly")

    result = _run_isolated(setup, *arguments, input_text="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "check-only":
        assert _snapshot(tmp_path) == before
    else:
        assert (data_root / "bin" / "paperflow.cmd").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("mode", ["check-only", "formal"])
@pytest.mark.parametrize(
    ("config_source", "config_bytes"),
    [
        ("destination", b"vault_path = [\n"),
        (
            "legacy",
            b'vault_path = "relative-vault"\n\n[keywords]\nrobotics = 5\n',
        ),
    ],
)
def test_invalid_effective_config_fails_before_mutation(
    tmp_path, config_source, config_bytes, mode
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    if config_source == "destination":
        config_path = data_root / "config" / "config.toml"
    else:
        config_path = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(config_bytes)
    sentinel = tmp_path / "invalid-config-sentinel.txt"
    sentinel.write_bytes(b"preserve invalid config state")
    before = _snapshot(tmp_path)
    arguments = ["-DataRoot", str(data_root)]
    if mode == "check-only":
        arguments.insert(0, "-CheckOnly")

    result = _run_isolated(setup, *arguments, input_text="n\n")

    assert result.returncode != 0
    assert "Effective PaperFlow config is invalid" in result.stderr
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("mode", ["check-only", "formal"])
def test_existing_config_vault_takes_precedence_over_explicit_vault(tmp_path, mode):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    config_path = data_root / "config" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(_local_config_bytes(Path(setup["vault"])))
    before = _snapshot(tmp_path)
    arguments = ["-DataRoot", str(data_root), "-VaultPath", str(data_root)]
    if mode == "check-only":
        arguments.insert(0, "-CheckOnly")

    result = _run_isolated(setup, *arguments, input_text="n\n")

    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "check-only":
        assert _snapshot(tmp_path) == before
    else:
        assert (data_root / "bin" / "paperflow.cmd").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_existing_config_vault_takes_precedence_over_missing_explicit_vault(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    config_path = data_root / "config" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(_local_config_bytes(Path(setup["vault"])))
    missing_explicit_vault = tmp_path / "not-an-existing-vault"

    result = _run_isolated(
        setup,
        "-DataRoot",
        str(data_root),
        "-VaultPath",
        str(missing_explicit_vault),
        input_text="n\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data_root / "bin" / "paperflow.cmd").is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize(
    ("config_source", "expected_detail"),
    [
        ("destination", "destination config will be preserved"),
        ("legacy", "legacy config will be migrated"),
        ("explicit", "explicit Vault will generate config"),
        ("none", "no effective config; config will not be written"),
    ],
)
def test_check_only_previews_effective_config_source(
    tmp_path, config_source, expected_detail
):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    data_root = tmp_path / "PaperFlow Data"
    arguments = ["-CheckOnly", "-DataRoot", str(data_root)]
    if config_source == "destination":
        config_path = data_root / "config" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes(_local_config_bytes(Path(setup["vault"])))
    elif config_source == "legacy":
        config_path = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes(_local_config_bytes(Path(setup["vault"])))
    elif config_source == "explicit":
        arguments.extend(("-VaultPath", str(setup["vault"])))

    result = _run_isolated(setup, *arguments, input_text=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert expected_detail in result.stdout
    if config_source != "none":
        assert "not provided; config will not be written" not in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior test")
@pytest.mark.parametrize("mode", ["check-only", "formal"])
def test_data_root_reparse_ancestor_is_rejected_before_mutation(tmp_path, mode):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    unrelated = tmp_path / "unrelated-target"
    unrelated.mkdir()
    sentinel = unrelated / "sentinel.txt"
    sentinel_bytes = b"do not follow the DataRoot ancestor junction"
    sentinel.write_bytes(sentinel_bytes)
    junction = tmp_path / "data-root-junction"
    _create_junction(str(setup["powershell"]), junction, unrelated)
    data_root = junction / "missing-tail" / "PaperFlowData"
    before = _snapshot(tmp_path)
    arguments = ["-DataRoot", str(data_root)]
    if mode == "check-only":
        arguments.insert(0, "-CheckOnly")

    result = _run_isolated(setup, *arguments)

    assert result.returncode != 0
    assert "DataRoot ancestor must not be a reparse point" in result.stderr
    assert sentinel.read_bytes() == sentinel_bytes
    assert _snapshot(tmp_path) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_separate_sibling_data_root_is_not_a_false_overlap(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    project = Path(setup["project"])
    data_root = project.parent / "project-data"

    result = _run_isolated(
        setup, "-CheckOnly", "-DataRoot", str(data_root), input_text=None
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell structure test")
def test_effective_config_preflight_uses_child_sys_path_without_parent_env_write():
    text = INSTALLER.read_text(encoding="utf-8")
    start, end = _powershell_function_extent(
        INSTALLER, "Resolve-PaperFlowConfigVaultPath"
    )
    function = text[start:end]

    assert "from paperflow.config import load_local_config" in function
    assert "sys.path.insert(0, sys.argv[2])" in function
    assert "load_local_config(Path(sys.argv[1]))" in function
    assert "$Path $sourcePath" in function
    assert "$env:PYTHONPATH =" not in function
    assert "SetEnvironmentVariable('PYTHONPATH'" not in function
    assert "New-TemporaryFile" not in function
    assert "WriteAllText" not in function


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
@pytest.mark.parametrize("parent_state", ["absent", "present-empty", "non-empty"])
def test_effective_config_parse_preserves_exact_parent_pythonpath(
    tmp_path, parent_state
):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    data_root = tmp_path / "PaperFlow Data"
    config_path = data_root / "config" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(_local_config_bytes(Path(setup["vault"])))
    native_definition = (
        "using System.Runtime.InteropServices; "
        "public static class PaperFlowEnvProbe { "
        '[DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] '
        "public static extern bool SetEnvironmentVariable(string name, string value); }"
    )
    if parent_state == "absent":
        prologue = (
            "[Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'Process'); "
            "if ([Environment]::GetEnvironmentVariables('Process').Contains('PYTHONPATH')) "
            "{ throw 'failed to establish absent PYTHONPATH' }; "
        )
        expected_present = "False"
        expected_value = ""
    elif parent_state == "present-empty":
        prologue = (
            f"Add-Type -TypeDefinition {_powershell_literal(native_definition)}; "
            "[PaperFlowEnvProbe]::SetEnvironmentVariable('PYTHONPATH', '') | Out-Null; "
            "if (-not [Environment]::GetEnvironmentVariables('Process').Contains('PYTHONPATH')) "
            "{ throw 'failed to establish present-empty PYTHONPATH' }; "
        )
        expected_present = "True"
        expected_value = ""
    else:
        expected_value = "parent-python-path-value"
        prologue = (
            f"[Environment]::SetEnvironmentVariable('PYTHONPATH', {_powershell_literal(expected_value)}, 'Process'); "
            "if (-not [Environment]::GetEnvironmentVariables('Process').Contains('PYTHONPATH')) "
            "{ throw 'failed to establish non-empty PYTHONPATH' }; "
        )
        expected_present = "True"
    epilogue = (
        "; $paperflowParentEnv = [Environment]::GetEnvironmentVariables('Process'); "
        "$paperflowParentPresent = $paperflowParentEnv.Contains('PYTHONPATH'); "
        "$paperflowParentValue = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process'); "
        "Write-Output ('PARENT_PYTHONPATH=' + $paperflowParentPresent + ':' + "
        "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$paperflowParentValue)))"
    )

    result = _run_dot_sourced_installer(
        setup,
        "-WhatIf",
        "-DataRoot",
        str(data_root),
        prologue=prologue,
        epilogue=epilogue,
        input_text=None,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    encoded_value = base64.b64encode(expected_value.encode("utf-8")).decode("ascii")
    assert f"PARENT_PYTHONPATH={expected_present}:{encoded_value}" in result.stdout


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
    write_call = path_flow.index(
        "Set-PaperFlowUserPathTransaction -IntendedValue $pathUpdate.Value"
    )
    assert should_process < write_call


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell structure test")
def test_direct_user_path_access_is_confined_to_persistence_backend():
    with INSTALLER.open("r", encoding="utf-8", newline="") as stream:
        text = stream.read()
    direct_access = "[Environment]::GetEnvironmentVariable('Path', 'User')"
    occurrences = [match.start() for match in re.finditer(re.escape(direct_access), text)]
    get_start, get_end = _powershell_function_extent(
        INSTALLER, "Get-PaperFlowUserPath"
    )
    refresh_start, refresh_end = _powershell_function_extent(
        INSTALLER, "Refresh-ProcessPath"
    )

    assert len(occurrences) == 1
    assert get_start <= occurrences[0] < get_end
    assert "Get-PaperFlowUserPath" in text[refresh_start:refresh_end]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_isolated_installer_uses_file_backed_path_persistence_by_default(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, _, _, _ = _write_legacy_install(setup)
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"
    path_file = Path(setup["user_path_file"])
    path_file.write_text(initial_path, encoding="utf-8")

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert path_file.read_text(encoding="utf-8") == rf"C:\Other;{data_root}\bin"


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

    result = _run_isolated(
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
    setup = _isolated_installer(tmp_path, ("git", "codex"))

    result = _run_isolated(setup, "-CheckOnly", input_text=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Python\s+OK\s+version 3\.12\.7", result.stdout)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_check_only_runs_without_mutating_isolated_user_directories(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    before_path = Path(setup["user_path_file"]).read_bytes()
    before_files = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
    }
    result = _run_isolated(setup, "-CheckOnly", input_text=None)

    assert result.returncode == 0, result.stdout + result.stderr
    for component in ("Git", "Python", "Codex", "Zotero", "Obsidian", "Vault", "Sidebar"):
        assert component in result.stdout
    after_files = {
        path.relative_to(tmp_path)
        for path in tmp_path.rglob("*")
    }
    assert after_files == before_files
    assert Path(setup["user_path_file"]).read_bytes() == before_path


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
def test_data_root_whatif_is_unattended_previews_path_without_cleanup(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"
    path_file = _use_file_backed_user_path(setup, initial_path)
    before = _snapshot(tmp_path)

    result = _run_dot_sourced_installer(
        setup,
        "-WhatIf",
        "-DataRoot",
        str(data_root),
        prologue="function Read-Host { throw 'unexpected Read-Host under WhatIf' }; ",
        input_text=None,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "What if:" in result.stdout
    assert "User PATH" in result.stdout
    assert "unexpected Read-Host" not in result.stderr
    assert _snapshot(tmp_path) == before
    assert path_file.read_text(encoding="utf-8") == initial_path
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes
    assert not data_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_formal_install_writes_expected_files_cleans_skill_and_is_idempotent(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
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
    assert (project / ".venv" / "Scripts" / "paperflow.cmd").is_file()
    assert wrapper.is_file()
    assert "%*" in wrapper.read_text(encoding="utf-8")
    assert str(project / ".venv" / "Scripts" / "paperflow.cmd") in wrapper.read_text(
        encoding="utf-8"
    )
    assert "preserved" in second.stdout.casefold()
    assert config.read_bytes() == custom_config
    assert (skill_target / "SKILL.md").read_text(encoding="utf-8") == "current skill\n"
    assert not (skill_target / "stale.txt").exists()
    assert {path: path.read_bytes() for path in expected_files} == first_contents
    assert Path(setup["user_path_file"]).read_text(encoding="utf-8") == ""


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
    assert wrapper_lines[:7] == [
        "@echo off",
        "setlocal DisableDelayedExpansion",
        f'set "PAPERFLOW_HOME={data_root.resolve()}"',
        f'set "PAPERFLOW_TOPICS_PATH={Path(setup["project"]).resolve() / "config" / "topics.toml"}"',
        f'set "PAPERFLOW_CACHE_DIR={data_root.resolve() / "cache"}"',
        f'set "TMP={data_root.resolve() / "tmp"}"',
        f'set "TEMP={data_root.resolve() / "tmp"}"',
    ]
    assert wrapper_lines[-3].endswith('paperflow.cmd" %*')
    assert wrapper_lines[-2:] == [
        'set "PAPERFLOW_EXIT_CODE=%ERRORLEVEL%"',
        "endlocal & exit /b %PAPERFLOW_EXIT_CODE%",
    ]
    assert not (Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd").exists()
    assert not (Path(setup["appdata"]) / "PaperFlow" / "config.toml").exists()


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe wrapper behavior test")
def test_data_root_wrapper_isolates_environment_special_paths_and_exit_code(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    case_id = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    data_root = ROOT / f".tw-{case_id} Data %WRAPPER_TOKEN% !bang!"
    path_file = _use_file_backed_user_path(
        setup, str(data_root / "bin")
    )

    install = _run_isolated(
        setup, "-DataRoot", str(data_root), "-VaultPath", str(setup["vault"])
    )
    assert install.returncode == 0, install.stdout + install.stderr
    assert path_file.read_text(encoding="utf-8") == str(data_root / "bin")

    generated_wrapper = data_root / "bin" / "paperflow.cmd"
    safe_wrapper = tmp_path / "paperflow-wrapper-under-test.cmd"
    shutil.copy2(generated_wrapper, safe_wrapper)
    target = Path(setup["project"]) / ".venv" / "Scripts" / "paperflow.cmd"
    target.write_text(
        f'@echo off\r\n"{sys.executable}" %*\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="utf-8",
    )

    probe_log = tmp_path / "wrapper-probe.json"
    caller_log = tmp_path / "caller-probe.json"
    probe = tmp_path / "wrapper-probe.py"
    caller_probe = tmp_path / "caller-probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['PAPERFLOW_WRAPPER_PROBE']).write_text(json.dumps({\n"
        "    'args': sys.argv[1:],\n"
        "    'home': os.environ.get('PAPERFLOW_HOME'),\n"
        "    'topics': os.environ.get('PAPERFLOW_TOPICS_PATH'),\n"
        "    'cache': os.environ.get('PAPERFLOW_CACHE_DIR'),\n"
        "    'temp': os.environ.get('TEMP'),\n"
        "    'tmp': os.environ.get('TMP'),\n"
        "}), encoding='utf-8')\n"
        "raise SystemExit(37)\n",
        encoding="utf-8",
    )
    caller_probe.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['PAPERFLOW_CALLER_PROBE']).write_text(json.dumps({\n"
        "    name: os.environ.get(name) for name in "
        "['PAPERFLOW_HOME', 'PAPERFLOW_TOPICS_PATH', 'PAPERFLOW_CACHE_DIR', 'TEMP', 'TMP']\n"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "invoke-wrapper.cmd"
    launcher.write_text(
        "@echo off\r\n"
        "setlocal EnableDelayedExpansion\r\n"
        'set "PAPERFLOW_HOME=caller-home"\r\n'
        'set "PAPERFLOW_TOPICS_PATH=caller-topics"\r\n'
        'set "PAPERFLOW_CACHE_DIR=caller-cache"\r\n'
        'set "TEMP=caller-temp"\r\n'
        'set "TMP=caller-tmp"\r\n'
        f'call "{safe_wrapper}" "{probe}" "argument with spaces" plain\r\n'
        'set "WRAPPER_EXIT=%ERRORLEVEL%"\r\n'
        f'"{sys.executable}" "{caller_probe}"\r\n'
        "endlocal & exit /b %WRAPPER_EXIT%\r\n",
        encoding="utf-8",
    )
    env = {
        **dict(setup["env"]),
        "WRAPPER_TOKEN": "EXPANDED_PERCENT",
        "bang": "EXPANDED_BANG",
        "PAPERFLOW_WRAPPER_PROBE": str(probe_log),
        "PAPERFLOW_CALLER_PROBE": str(caller_log),
    }

    result = subprocess.run(
        ["cmd.exe", "/d", "/v:on", "/c", str(launcher)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 37, result.stdout + result.stderr
    observed = json.loads(probe_log.read_text(encoding="utf-8"))
    assert observed == {
        "args": ["argument with spaces", "plain"],
        "home": str(data_root.resolve()),
        "topics": str(Path(setup["project"]).resolve() / "config" / "topics.toml"),
        "cache": str((data_root / "cache").resolve()),
        "temp": str((data_root / "tmp").resolve()),
        "tmp": str((data_root / "tmp").resolve()),
    }
    assert json.loads(caller_log.read_text(encoding="utf-8")) == {
        "PAPERFLOW_HOME": "caller-home",
        "PAPERFLOW_TOPICS_PATH": "caller-topics",
        "PAPERFLOW_CACHE_DIR": "caller-cache",
        "TEMP": "caller-temp",
        "TMP": "caller-tmp",
    }
    shutil.rmtree(data_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_data_root_copies_legacy_config_bytes_atomically_without_overwriting(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    data_root = tmp_path / "PaperFlow Data"
    legacy_config = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    legacy_config.parent.mkdir(parents=True)
    legacy_bytes = (
        b'vault_path = "D:\\\\Legacy"\r\n'
        b'custom = 7\r\n\r\n[keywords]\r\nrobotics = 5\r\n'
        b'# preserve bytes: caf\xc3\xa9\r\n'
    )
    legacy_config.write_bytes(legacy_bytes)

    first = _run_isolated(setup, "-DataRoot", str(data_root))
    assert first.returncode == 0, first.stdout + first.stderr
    new_config = data_root / "config" / "config.toml"
    assert new_config.read_bytes() == legacy_bytes
    assert legacy_config.read_bytes() == legacy_bytes
    assert not list(new_config.parent.glob(".paperflow-config-*"))

    replacement = (
        b'vault_path = "D:\\\\KeepNew"\ncustom = true\n\n'
        b'[keywords]\nrobotics = 9\n'
    )
    new_config.write_bytes(replacement)
    legacy_config.write_bytes(b"changed legacy")
    second = _run_isolated(setup, "-DataRoot", str(data_root))
    assert second.returncode == 0, second.stdout + second.stderr
    assert new_config.read_bytes() == replacement
    assert legacy_config.read_bytes() == b"changed legacy"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior test")
@pytest.mark.parametrize(
    "junction_kind", ["local-paperflow", "legacy-bin", "roaming-paperflow"]
)
def test_legacy_parent_junction_fails_without_following_target_or_mutation(
    tmp_path, junction_kind
):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    target = tmp_path / "unrelated-target"
    target.mkdir()
    target_wrapper = target / "paperflow.cmd"
    target_config = target / "config.toml"
    wrapper_bytes = b"@echo off\r\necho unrelated wrapper\r\n"
    config_bytes = b'vault_path = "D:\\\\Unrelated"\r\n'

    local_paperflow = Path(setup["local_appdata"]) / "PaperFlow"
    legacy_bin = local_paperflow / "bin"
    roaming_paperflow = Path(setup["appdata"]) / "PaperFlow"
    if junction_kind == "local-paperflow":
        (target / "bin").mkdir()
        target_wrapper = target / "bin" / "paperflow.cmd"
        target_wrapper.write_bytes(wrapper_bytes)
        roaming_paperflow.mkdir()
        target_config = roaming_paperflow / "config.toml"
        target_config.write_bytes(config_bytes)
        _create_junction(str(setup["powershell"]), local_paperflow, target)
    elif junction_kind == "legacy-bin":
        local_paperflow.mkdir()
        target_wrapper.write_bytes(wrapper_bytes)
        roaming_paperflow.mkdir()
        target_config = roaming_paperflow / "config.toml"
        target_config.write_bytes(config_bytes)
        _create_junction(str(setup["powershell"]), legacy_bin, target)
    else:
        legacy_bin.mkdir(parents=True)
        target_wrapper = legacy_bin / "paperflow.cmd"
        target_wrapper.write_bytes(wrapper_bytes)
        target_config.write_bytes(config_bytes)
        _create_junction(str(setup["powershell"]), roaming_paperflow, target)
    initial_path = rf"C:\Other;{legacy_bin}"
    path_file = _use_file_backed_user_path(setup, initial_path)

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode != 0
    assert "Legacy PaperFlow directory must be a normal non-reparse directory" in result.stderr
    assert target_wrapper.read_bytes() == wrapper_bytes
    assert target_config.read_bytes() == config_bytes
    assert path_file.read_text(encoding="utf-8") == initial_path
    assert not data_root.exists()
    assert (Path(setup["skill_target"]) / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "old skill\n"


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
    config_bytes = (
        b'vault_path = "D:\\\\ExactLegacy"\r\ncustom = 7\r\n\r\n'
        b'[keywords]\r\nrobotics = 5\r\n'
    )
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


def _use_file_backed_user_path(
    setup: dict[str, object], value: str, *, persist_writes: bool = True
) -> Path:
    path_file = Path(setup["user_path_file"])
    no_persist_file = Path(setup["user_path_no_persist_file"])
    path_file.write_text(value, encoding="utf-8")
    if persist_writes:
        no_persist_file.unlink(missing_ok=True)
    else:
        no_persist_file.write_text("do not persist", encoding="utf-8")
    return path_file


def _set_path_persistence_behavior(setup: dict[str, object], behavior: str) -> None:
    Path(setup["user_path_behavior_file"]).write_text(behavior, encoding="utf-8")
    Path(setup["user_path_setter_count_file"]).unlink(missing_ok=True)


def test_data_root_doctor_precedes_path_commit_and_legacy_cleanup():
    text = INSTALLER.read_text(encoding="utf-8")
    doctor_position = text.index("& $WrapperPath --json doctor")
    assert "& $VenvPaperFlowDoctor --json doctor" not in text
    path_position = text.index("$userPath = Get-PaperFlowUserPath", doctor_position)
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
        f"PAPERFLOW_TOPICS_PATH={Path(setup['project']).resolve() / 'config' / 'topics.toml'}",
        f"PAPERFLOW_CACHE_DIR={data_root.resolve() / 'cache'}",
        f"TMP={data_root.resolve() / 'tmp'}",
        f"TEMP={data_root.resolve() / 'tmp'}",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_successful_path_migration_preserves_conflicting_legacy_config(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    new_config = data_root / "config" / "config.toml"
    new_config.parent.mkdir(parents=True)
    new_bytes = (
        b'vault_path = "D:\\\\NewVault"\nnew = true\n\n'
        b'[keywords]\nrobotics = 5\n'
    )
    new_config.write_bytes(new_bytes)
    legacy_wrapper, legacy_config, _, legacy_bytes = _write_legacy_install(setup)
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"
    path_file = _use_file_backed_user_path(setup, initial_path)

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert new_config.read_bytes() == new_bytes
    assert legacy_config.read_bytes() == legacy_bytes
    assert not legacy_wrapper.exists()
    assert path_file.read_text(encoding="utf-8") == rf"C:\Other;{data_root}\bin"
    assert "manual reconciliation" in result.stdout.casefold()


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
def test_broken_new_wrapper_prevents_path_commit_and_preserves_legacy(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    wrapper_path = data_root / "bin" / "paperflow.cmd"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"
    path_file = _use_file_backed_user_path(setup, initial_path)
    wrapper_literal = _powershell_literal(str(wrapper_path))
    prologue = (
        f"$paperflowTestWrapper = {wrapper_literal}; "
        "function Move-Item { param([string]$LiteralPath, [string]$Destination, [switch]$Force); "
        "Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters; "
        "if ([System.StringComparer]::OrdinalIgnoreCase.Equals($Destination, $paperflowTestWrapper)) "
        "{ [System.IO.File]::WriteAllText($Destination, \"@echo off`r`nexit /b 91`r`n\") } }; "
    )

    result = _run_dot_sourced_installer(
        setup,
        "-DataRoot",
        str(data_root),
        prologue=prologue,
        input_text="y\n",
    )

    assert result.returncode != 0
    assert "PaperFlow doctor exited with code 91" in result.stderr
    assert path_file.read_text(encoding="utf-8") == initial_path
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes


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
def test_data_root_path_persistence_mismatch_preserves_exact_legacy_files(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Other;{legacy_wrapper.parent}"
    path_file = _use_file_backed_user_path(
        setup, initial_path, persist_writes=False
    )

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode != 0
    assert "User PATH did not persist the intended PaperFlow migration" in result.stderr
    assert path_file.read_text(encoding="utf-8") == initial_path
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_corrupted_path_write_is_rolled_back_exactly_before_failure(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Exact Before;;{legacy_wrapper.parent};"
    path_file = _use_file_backed_user_path(setup, initial_path)
    _set_path_persistence_behavior(setup, "corrupt-once")

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode != 0
    assert "original user PATH was restored" in result.stderr
    assert path_file.read_text(encoding="utf-8") == initial_path
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_path_rollback_failure_requires_manual_repair_and_preserves_legacy(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    initial_path = rf"C:\Exact Before;{legacy_wrapper.parent}"
    path_file = _use_file_backed_user_path(setup, initial_path)
    _set_path_persistence_behavior(setup, "always-corrupt")

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode != 0
    assert "manual PATH repair is required" in result.stderr
    assert path_file.read_text(encoding="utf-8") == "CORRUPTED"
    assert legacy_wrapper.read_bytes() == wrapper_bytes
    assert legacy_config.read_bytes() == config_bytes


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_path_rollback_restores_an_originally_missing_user_path(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    _preprovision_fake_venv(setup)
    data_root = tmp_path / "PaperFlow Data"
    legacy_wrapper, legacy_config, wrapper_bytes, config_bytes = _write_legacy_install(
        setup
    )
    path_file = Path(setup["user_path_file"])
    path_file.unlink()
    _set_path_persistence_behavior(setup, "corrupt-once")

    result = _run_isolated(
        setup, "-DataRoot", str(data_root), input_text="y\n"
    )

    assert result.returncode != 0
    assert "original user PATH was restored" in result.stderr
    assert not path_file.exists()
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
def test_whatif_path_consent_changes_neither_tree_nor_file_backed_path(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    before = _snapshot(tmp_path)

    result = _run_isolated(
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
    refreshed_path_log = tmp_path / "refreshed-path.txt"
    file_backed_user_bin = tmp_path / "file-backed-user-bin"
    file_backed_user_bin.mkdir()
    Path(setup["user_path_file"]).write_text(
        str(file_backed_user_bin), encoding="utf-8"
    )
    env = dict(setup["env"])
    env["PAPERFLOW_WINGET_LOG"] = str(winget_log)
    env["PAPERFLOW_FAKE_BIN"] = str(file_backed_user_bin)
    setup["env"] = env
    _write_command(
        Path(setup["fake_bin"]),
        "winget",
        '@echo off\r\nif "%~3"=="Git.Git" echo @exit /b 0>"%PAPERFLOW_FAKE_BIN%\\git.cmd"\r\necho %*>>"%PAPERFLOW_WINGET_LOG%"\r\nexit /b 0\r\n',
    )

    epilogue = (
        "; [System.IO.File]::WriteAllText("
        + _powershell_literal(str(refreshed_path_log))
        + ", $env:Path)"
    )
    result = _run_dot_sourced_installer(
        setup, "-InstallMissing", epilogue=epilogue
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = [line.strip() for line in winget_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [
        "install --id Git.Git --exact",
        "install --id DigitalScholar.Zotero --exact",
        "install --id Obsidian.Obsidian --exact",
    ]
    assert all("Codex" not in call for call in calls)
    assert (file_backed_user_bin / "git.cmd").is_file()
    assert str(file_backed_user_bin) in refreshed_path_log.read_text(
        encoding="utf-8"
    ).split(";")
    assert Path(setup["user_path_file"]).read_text(encoding="utf-8") == str(
        file_backed_user_bin
    )


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


def test_readme_documents_data_root_install_layout_and_scoped_environment():
    text = _read("README.md")
    vault_argument = r'-VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"'

    assert (
        '.\\scripts\\install-windows.ps1 -CheckOnly -DataRoot "D:\\PaperFlowData" '
        f"{vault_argument}"
    ) in text
    assert (
        '.\\scripts\\install-windows.ps1 -DataRoot "D:\\PaperFlowData" '
        f"{vault_argument}"
    ) in text
    for path in (
        r"D:\PaperFlow\.venv",
        r"D:\PaperFlowData\bin\paperflow.cmd",
        r"D:\PaperFlowData\config\config.toml",
        r"D:\PaperFlowData\cache",
        r"D:\PaperFlowData\tmp",
        r"%USERPROFILE%\.agents\skills\paperflow",
    ):
        assert path in text
    assert "PAPERFLOW_HOME" in text
    assert "PaperFlow 专用" in text
    assert "不会全局迁移其他程序" in text
    assert "PIP_NO_CACHE_DIR=1" in text
    assert "恢复安装进程原有的 TEMP、TMP 和 PIP_NO_CACHE_DIR" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell parser test")
def test_readme_fenced_installer_commands_parse_with_windows_powershell():
    powershell = shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    commands = [
        line.strip()
        for block in _fenced_blocks(_read("README.md"), "powershell")
        for line in block.splitlines()
        if "install-windows.ps1" in line
    ]
    assert len(commands) == 5
    command_array = ", ".join(_powershell_literal(command) for command in commands)
    parser_script = (
        f"$commands = @({command_array}); "
        "foreach ($source in $commands) { "
        "$tokens = $null; $parseErrors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseInput("
        "$source, [ref]$tokens, [ref]$parseErrors) | Out-Null; "
        "if ($parseErrors.Count -ne 0) { "
        "$parseErrors | ForEach-Object { Write-Error $_.Message }; exit 1 } }"
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", parser_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_readme_checkonly_command_runs_in_isolation_without_mutation(tmp_path):
    setup = _isolated_installer(
        tmp_path, ("git", "codex", "zotero", "obsidian")
    )
    commands = [
        line.strip()
        for block in _fenced_blocks(_read("README.md"), "powershell")
        for line in block.splitlines()
        if "install-windows.ps1" in line and "-CheckOnly" in line
    ]
    documented = next(command for command in commands if command.startswith(".\\scripts"))
    isolated_data_root = tmp_path / "README DataRoot"
    executable = documented.replace(
        '"D:\\PaperFlowData"', _powershell_literal(str(isolated_data_root))
    ).replace(
        '"$env:USERPROFILE\\Documents\\Obsidian Vault"',
        _powershell_literal(str(setup["vault"])),
    )
    before = _snapshot(tmp_path)

    result = subprocess.run(
        [
            str(setup["powershell"]),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            executable,
        ],
        cwd=Path(setup["project"]),
        env=dict(setup["env"]),
        input="n\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "DataRoot:" in result.stdout
    assert _snapshot(tmp_path) == before
    assert not isolated_data_root.exists()


def test_readme_documents_existing_vault_and_safe_legacy_migration():
    text = _read("README.md")
    install = _markdown_section(text, "克隆与安装")
    config = _markdown_section(text, "本地配置")

    assert r'-VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"' in install
    assert "按当前用户分别展开" in install
    assert "Vault 位于其他位置的电脑请修改" in install
    assert "也可以提供另一个已存在的 Vault" in install
    assert "Vault 是用户内容" in install
    assert "不会作为缓存移动" in install
    assert "仅在新的 DataRoot config 不存在时" in install
    assert "逐字节复制" in install
    assert "已存在的目标 config 会原样保留" in install
    assert "PATH 替换成功并经写入后读回验证" in install
    assert "PATH 已经精确指向新的 bin 且经读回验证" in install
    assert "在迁移提交之前发生的任何失败" in install
    assert "包括配置复制、wrapper 创建、doctor、PATH 持久化或写入后读回核对" in install
    assert "都不会删除精确的旧版 wrapper/config" in install
    assert "拒绝 PATH 迁移也会保留它们" in install
    assert "只清理旧位置中精确匹配的 wrapper" in install
    assert "仅在本次安装已将其逐字节迁移到新位置时清理" in install
    assert r"%LOCALAPPDATA%\PaperFlow\bin\paperflow.cmd" in install
    assert r"%APPDATA%\PaperFlow\config.toml" in install
    assert "同时保留新旧 config.toml" in install
    assert "manual reconciliation" in install
    assert "drive-absolute local path" in install
    assert "不支持 UNC" in install
    assert "不能包含分号" in install
    assert "不得与项目目录" in install
    assert "现有或待迁移 config.toml 中的 vault_path" in install
    assert "paperflow.cmd --json doctor" in install
    assert "回滚并读回验证" in install
    assert "未知相邻文件始终保留" in install
    assert 'vault_path = "D:\\\\ObsidianVault"' in config
    assert "TOML 不会展开环境变量" in config
    assert "实际绝对 Vault 路径" in config


def test_readme_has_no_concrete_windows_user_profile_path():
    text = _read("README.md")

    concrete_profile = re.compile(
        r"(?i)(?<![A-Za-z0-9_])C:\\Users\\[^\\\s`\"']+"
    )
    assert concrete_profile.search(text) is None


def test_readme_separates_local_and_cloud_privacy_retention():
    text = _read("README.md")
    privacy = _markdown_section(text, "隐私边界")

    assert "本地模式" in privacy
    assert "本地元数据和报告" in privacy
    assert "保留在本机" in privacy
    assert "Hugging Face 和 arXiv" in privacy
    assert "仅在启用邮件时连接 Gmail SMTP" in privacy
    assert "PaperFlow 本身没有模型客户端" in privacy
    assert "AI Sidebar" in privacy
    assert "模型端点" in privacy
    assert "不属于 PaperFlow 自身的进程或配置" in privacy
    assert "GitHub Actions 云端模式" in privacy
    assert "GitHub 托管 runner" in privacy
    assert "JSON/stdout" in privacy
    assert "Actions 日志" in privacy
    assert "GitHub 的日志保留策略" in privacy
    assert "邮件内容" in privacy
    assert "发件人和收件人邮箱" in privacy
    assert "SMTP 凭据" in privacy
    assert "PAPERFLOW_PRIVATE_CONFIG_JSON" in privacy
    assert "私有运行时配置" in privacy
    assert "Secrets 不会被有意打印" in privacy
    assert "更强隐私" in privacy
    assert "本地调度并关闭邮件" in privacy
    assert "减少 workflow 输出" in privacy
    assert "用户自行配置的模型端点" not in privacy
    assert "GitHub Secrets 用于运行时认证" not in privacy
    assert "不提供 Web UI、向量检索或云端持久化" not in text
    assert "元数据和报告文件都保留在本地" not in text


def test_readme_documents_upgrade_uninstall_privacy_and_usage_contracts():
    text = _read("README.md")
    privacy = _markdown_section(text, "隐私边界")
    vault_argument = r'-VaultPath "$env:USERPROFILE\Documents\Obsidian Vault"'

    assert (
        '.\\scripts\\install-windows.ps1 -DataRoot "D:\\PaperFlowData" '
        f"{vault_argument}"
    ) in text
    assert "不要递归删除 Vault" in text
    assert "不要递归删除未知的旧版内容" in text
    assert "不使用 SQLite 或其他数据库" in text
    assert "本地元数据和报告" in privacy
    assert "保留在本机" in privacy
    assert "论文提供方" in text
    assert "PaperFlow 本身没有模型客户端" in privacy
    assert "AI Sidebar" in privacy
    assert "不属于 PaperFlow 自身的进程或配置" in privacy
    for command in (
        "paperflow doctor",
        'paperflow search "3d reconstruction"',
        "paperflow daily",
    ):
        assert command in text
    assert r"<Vault>\PaperFlow\Reports\YYYY-MM-DD.md" in text


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
    assert "`paperflow.cmd --json doctor` 运行只读诊断" in text
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
