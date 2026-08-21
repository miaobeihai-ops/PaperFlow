# Configurable PaperFlow Data Root Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe `-DataRoot` Windows installation path so PaperFlow configuration, wrapper, temporary files, and future caches can live under `D:\PaperFlowData`, then migrate this machine from the current C-drive layout.

**Architecture:** Introduce `PAPERFLOW_HOME` as the app-specific configuration root while retaining the current `%APPDATA%` fallback. Extend the PowerShell installer with validated DataRoot paths, a D-root wrapper, no-cache pip execution, transactional config migration, and exact PATH replacement. Keep the Codex Skill in `%USERPROFILE%\.agents`, and leave the configured Obsidian Vault unchanged.

**Tech Stack:** Python 3.11, pytest, Windows PowerShell 5.1, TOML configuration, batch wrapper, Git.

---

## File Structure

- Modify `src/paperflow/config.py`: resolve and validate `PAPERFLOW_HOME`.
- Modify `src/paperflow/doctor.py`: use the shared resolver and report DataRoot paths.
- Modify `scripts/install-windows.ps1`: validate DataRoot, install/migrate atomically, relocate temp/cache/wrapper/config.
- Modify `scripts/install-windows-path.ps1`: replace one exact legacy PATH entry while adding the new entry idempotently.
- Modify `tests/test_config.py`: unit tests for the environment-based resolver.
- Modify `tests/test_doctor.py`: read-only DataRoot diagnostic tests.
- Modify `tests/test_installer_contract.py`: isolated Windows installer and migration tests.
- Modify `README.md`: DataRoot install, upgrade, uninstall, and privacy instructions.

### Task 1: Resolve local configuration through `PAPERFLOW_HOME`

**Files:**
- Modify: `src/paperflow/config.py:24-30`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing resolver tests**

Add imports and tests:

```python
from paperflow.config import (
    ConfigError,
    _build,
    default_local_config_path,
    load_cloud_config,
    load_local_config,
)


def test_default_config_prefers_absolute_paperflow_home(monkeypatch, tmp_path):
    home = tmp_path / "PaperFlowData"
    monkeypatch.setenv("PAPERFLOW_HOME", str(home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    assert default_local_config_path() == home / "config" / "config.toml"


def test_default_config_keeps_appdata_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("PAPERFLOW_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    assert default_local_config_path() == tmp_path / "AppData" / "PaperFlow" / "config.toml"


@pytest.mark.parametrize("value", ["", "relative/path", "bad\npath"])
def test_invalid_paperflow_home_does_not_fall_back(monkeypatch, tmp_path, value):
    monkeypatch.setenv("PAPERFLOW_HOME", value)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        default_local_config_path()
```

- [ ] **Step 2: Run the focused tests and observe failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: the preference and invalid-value tests fail because the resolver ignores `PAPERFLOW_HOME`.

- [ ] **Step 3: Implement the minimal resolver**

Replace `default_local_config_path()` with:

```python
def default_local_config_path() -> Path:
    paperflow_home = os.environ.get("PAPERFLOW_HOME")
    if paperflow_home is not None:
        if not paperflow_home or "\n" in paperflow_home or "\r" in paperflow_home:
            raise ConfigError("PAPERFLOW_HOME must be an absolute path")
        home = Path(paperflow_home).expanduser()
        if not home.is_absolute():
            raise ConfigError("PAPERFLOW_HOME must be an absolute path")
        return home / "config" / "config.toml"

    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ConfigError("APPDATA is not set")
    return Path(appdata) / "PaperFlow" / "config.toml"
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_config.py -q
```

Expected: all `tests/test_config.py` tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- src/paperflow/config.py tests/test_config.py
git commit -m "feat: resolve config from PaperFlow home"
```

### Task 2: Add exact PATH migration behavior

**Files:**
- Modify: `scripts/install-windows-path.ps1`
- Test: `tests/test_installer_contract.py`

- [ ] **Step 1: Add failing pure-helper tests**

Add this test helper, then add the tests below:

```python
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
```

```python
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior test")
def test_set_path_entry_replaces_only_exact_legacy_entry():
    result = _invoke_set_path_function(
        r"C:\Tools;C:\Users\test\AppData\Local\PaperFlow\bin;C:\Other",
        r"D:\PaperFlowData\bin",
        r"C:\Users\test\AppData\Local\PaperFlow\bin",
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
```

The Python helper must dot-source `install-windows-path.ps1`, call `Set-PaperFlowPathEntry`, and decode `ConvertTo-Json -Compress` exactly as `_invoke_path_function` does.

- [ ] **Step 2: Run the focused tests and observe failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -k "set_path_entry" -q
```

Expected: failure because `Set-PaperFlowPathEntry` does not exist.

- [ ] **Step 3: Implement the pure replacement helper**

Append:

```powershell
function Set-PaperFlowPathEntry {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$CurrentPath,
        [Parameter(Mandatory = $true)][string]$BinDir,
        [AllowEmptyString()][string]$LegacyBinDir = ''
    )

    $normalize = {
        param([string]$Value)
        $Value.Trim().TrimEnd('\')
    }
    $normalizedNew = & $normalize $BinDir
    $normalizedLegacy = & $normalize $LegacyBinDir
    $entries = [System.Collections.Generic.List[string]]::new()
    $newPresent = $false
    $changed = $false
    foreach ($rawEntry in @($CurrentPath -split ';')) {
        if ([string]::IsNullOrWhiteSpace($rawEntry)) { continue }
        $normalizedEntry = & $normalize $rawEntry
        if ($normalizedLegacy -and $normalizedEntry -ieq $normalizedLegacy) {
            $changed = $true
            continue
        }
        if ($normalizedEntry -ieq $normalizedNew) { $newPresent = $true }
        $entries.Add($rawEntry)
    }
    if (-not $newPresent) {
        $entries.Add($BinDir)
        $changed = $true
    }
    [pscustomobject]@{ Changed = $changed; Value = ($entries -join ';') }
}
```

- [ ] **Step 4: Run all path-helper tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -k "path_entry" -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- scripts/install-windows-path.ps1 tests/test_installer_contract.py
git commit -m "feat: migrate PaperFlow PATH entry"
```

### Task 3: Install and migrate through `-DataRoot`

**Files:**
- Modify: `scripts/install-windows.ps1`
- Modify: `tests/test_installer_contract.py`

- [ ] **Step 1: Add failing installer interface and CheckOnly tests**

Add assertions and an isolated test:

```python
def test_installer_declares_data_root_interface_and_no_cache_contract():
    text = INSTALLER.read_text(encoding="utf-8")

    assert "[string]$DataRoot" in text
    assert "PAPERFLOW_HOME" in text
    assert "PAPERFLOW_CACHE_DIR" in text
    assert "PIP_NO_CACHE_DIR" in text


def test_check_only_data_root_is_non_mutating(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    data_root = tmp_path / "data-root"
    before = _snapshot(tmp_path)

    result = _run_isolated(
        setup,
        "-CheckOnly",
        "-VaultPath",
        str(setup["vault"]),
        "-DataRoot",
        str(data_root),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _snapshot(tmp_path) == before
    assert str(data_root) in result.stdout
```

- [ ] **Step 2: Run the new interface tests and observe failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -k "data_root or no_cache_contract" -q
```

Expected: failure because `-DataRoot` and the environment contract are absent.

- [ ] **Step 3: Add validated DataRoot path calculation**

Add `[string]$DataRoot` to `param`, preserve the existing defaults when omitted, and calculate:

```powershell
$LegacyPaperFlowHome = Join-Path $env:LOCALAPPDATA 'PaperFlow'
$LegacyBinDir = Join-Path $LegacyPaperFlowHome 'bin'
$LegacyWrapperPath = Join-Path $LegacyBinDir 'paperflow.cmd'
$LegacyConfigDir = Join-Path $env:APPDATA 'PaperFlow'
$LegacyConfigPath = Join-Path $LegacyConfigDir 'config.toml'

if ($DataRoot) {
    if (-not [System.IO.Path]::IsPathRooted($DataRoot) -or
        $DataRoot.Contains("`r") -or $DataRoot.Contains("`n")) {
        throw 'DataRoot must be an absolute path.'
    }
    $PaperFlowHome = [System.IO.Path]::GetFullPath($DataRoot)
    if (Test-Path -LiteralPath $PaperFlowHome) {
        $rootItem = Get-Item -LiteralPath $PaperFlowHome -Force
        if (-not $rootItem.PSIsContainer -or
            ($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'DataRoot must be a regular directory or not exist.'
        }
    }
    $BinDir = Join-Path $PaperFlowHome 'bin'
    $ConfigDir = Join-Path $PaperFlowHome 'config'
    $CacheDir = Join-Path $PaperFlowHome 'cache'
    $TempDir = Join-Path $PaperFlowHome 'tmp'
}
else {
    $PaperFlowHome = $LegacyPaperFlowHome
    $BinDir = $LegacyBinDir
    $ConfigDir = $LegacyConfigDir
    $CacheDir = Join-Path $PaperFlowHome 'cache'
    $TempDir = Join-Path $PaperFlowHome 'tmp'
}
$WrapperPath = Join-Path $BinDir 'paperflow.cmd'
$ConfigPath = Join-Path $ConfigDir 'config.toml'
```

Print the resolved DataRoot in the preview. Validate DataRoot before `Assert-InstallDestinationPreflight` or any persistent write.

- [ ] **Step 4: Add failing formal migration tests**

Add this isolated migration test:

```python
def test_data_root_migrates_legacy_config_wrapper_and_path(tmp_path):
    setup = _isolated_installer(tmp_path, ("git", "codex"))
    data_root = tmp_path / "data-root"
    legacy_config = Path(setup["appdata"]) / "PaperFlow" / "config.toml"
    legacy_wrapper = (
        Path(setup["local_appdata"]) / "PaperFlow" / "bin" / "paperflow.cmd"
    )
    legacy_config.parent.mkdir(parents=True)
    legacy_wrapper.parent.mkdir(parents=True)
    vault_text = Path(setup["vault"]).as_posix()
    legacy_bytes = (
        f'vault_path = "{vault_text}"\n\n[keywords]\nrobotics = 9\n'
    ).encode("utf-8")
    legacy_config.write_bytes(legacy_bytes)
    legacy_wrapper.write_text("legacy", encoding="utf-8")

    old_user_path = _read_user_path_registry()
    try:
        _write_user_path_registry(str(legacy_wrapper.parent))
        result = _run_isolated(
            setup,
            "-VaultPath",
            str(setup["vault"]),
            "-DataRoot",
            str(data_root),
            input_text="y\n",
        )
        migrated_path = _read_user_path_registry()
    finally:
        _restore_user_path_registry(old_user_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data_root / "config" / "config.toml").read_bytes() == legacy_bytes
    wrapper_text = (data_root / "bin" / "paperflow.cmd").read_text(encoding="utf-8")
    cache_dir = data_root / "cache"
    temp_dir = data_root / "tmp"
    assert f'set "PAPERFLOW_HOME={data_root}"' in wrapper_text
    assert f'set "PAPERFLOW_CACHE_DIR={cache_dir}"' in wrapper_text
    assert f'set "TMP={temp_dir}"' in wrapper_text
    assert f'set "TEMP={temp_dir}"' in wrapper_text
    assert migrated_path == str(data_root / "bin")
    assert not legacy_config.exists()
    assert not legacy_wrapper.exists()
```

Implement `_read_user_path_registry()`, `_write_user_path_registry()`, and
`_restore_user_path_registry()` with Python's `winreg`. The restore helper must
distinguish a missing original value from an empty value, and every test that
temporarily changes the registry must restore it in `finally`.

Add a doctor-failure case using the same valid legacy config, set
`PAPERFLOW_DOCTOR_EXIT=7` in the isolated environment, run with `input_text="y\n"`,
and assert the output contains `code 7`, the registry PATH retains the exact
legacy bin, and both legacy files remain. Add an unknown-file case by writing
`keep.txt` beside the legacy wrapper, running a successful migration with the
same registry save/restore pattern, and asserting `keep.txt` and its parent
directory remain while only `paperflow.cmd` is removed.

- [ ] **Step 5: Run migration tests and observe failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -k "data_root or legacy" -q
```

Expected: formal migration assertions fail because the installer still writes only the legacy layout.

- [ ] **Step 6: Implement D-root directories, wrapper, no-cache pip, and migration commit**

Before pip calls, create DataRoot directories under `ShouldProcess`, save the original process values, and use a `try/finally` block:

```powershell
$originalTemp = $env:TEMP
$originalTmp = $env:TMP
$originalPipNoCache = $env:PIP_NO_CACHE_DIR
try {
    $env:TEMP = $TempDir
    $env:TMP = $TempDir
    $env:PIP_NO_CACHE_DIR = '1'
    & $VenvPython -m pip install --requirement $RequirementsLock
    if ($LASTEXITCODE -ne 0) { throw 'Locked runtime dependency installation failed.' }
    & $VenvPython -m pip install --no-deps --no-build-isolation $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw 'PaperFlow package installation failed.' }
}
finally {
    $env:TEMP = $originalTemp
    $env:TMP = $originalTmp
    $env:PIP_NO_CACHE_DIR = $originalPipNoCache
}
```

When DataRoot is supplied and the new config is absent, atomically copy legacy bytes before falling back to generated config. Generate the wrapper as:

```powershell
$wrapper = @"
@echo off
set "PAPERFLOW_HOME=$PaperFlowHome"
set "PAPERFLOW_CACHE_DIR=$CacheDir"
set "TMP=$TempDir"
set "TEMP=$TempDir"
"$VenvPaperFlow" %*
"@
```

Run doctor through the new wrapper environment. Only after doctor exits zero, call `Set-PaperFlowPathEntry` with the exact legacy bin, persist the approved PATH update, then remove only the exact legacy wrapper/config regular files. Remove parent directories only when `Get-ChildItem -Force` is empty; never recurse through legacy directories.

- [ ] **Step 7: Run the complete installer contract tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -q
```

Expected: all installer contract tests pass.

- [ ] **Step 8: Commit**

```powershell
git add -- scripts/install-windows.ps1 tests/test_installer_contract.py
git commit -m "feat: install PaperFlow under configurable data root"
```

### Task 4: Report DataRoot health in doctor

**Files:**
- Modify: `src/paperflow/doctor.py:48-150`
- Modify: `tests/test_doctor.py`

- [ ] **Step 1: Add failing doctor tests**

Import `default_local_config_path` in doctor and add:

```python
def test_doctor_uses_paperflow_home_config_and_reports_data_paths(tmp_path):
    doctor = _doctor()
    home = tmp_path / "PaperFlowData"
    config_path = home / "config" / "config.toml"
    cache_path = home / "cache"
    temp_path = home / "tmp"
    wrapper_path = home / "bin" / "paperflow.cmd"
    vault = tmp_path / "Vault"
    for directory in (config_path.parent, cache_path, temp_path, wrapper_path.parent, vault):
        directory.mkdir(parents=True, exist_ok=True)
    _valid_config(config_path, vault)
    wrapper_path.write_text("@echo off", encoding="utf-8")

    checks = _by_name(
        doctor.run_checks(
            which=lambda command: f"C:/{command}.exe",
            path_exists=Path.exists,
            path_is_file=Path.is_file,
            path_is_dir=Path.is_dir,
            environ={"PAPERFLOW_HOME": str(home), "USERPROFILE": str(tmp_path)},
            python_version=(3, 11, 9),
            skill_path=tmp_path / "missing-skill.md",
        )
    )

    assert checks["Configuration"].ok is True
    assert checks["DataRoot"].ok is True
    assert checks["PaperFlow wrapper"].ok is True
    assert checks["PaperFlow cache"].ok is True
    assert checks["PaperFlow temp"].ok is True
```

- [ ] **Step 2: Run the doctor tests and observe failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_doctor.py -q
```

Expected: failure because DataRoot-specific checks do not exist and doctor derives only from APPDATA.

- [ ] **Step 3: Implement read-only DataRoot checks**

Add this read-only helper and use it when `config_path` is not injected:

```python
def _paperflow_home_paths(env: Mapping[str, str]) -> tuple[Path, Path, Path, Path, Path] | None:
    raw_home = env.get("PAPERFLOW_HOME")
    if raw_home is None:
        return None
    if not raw_home or "\n" in raw_home or "\r" in raw_home:
        return None
    home = Path(raw_home).expanduser()
    if not home.is_absolute():
        return None
    return (
        home,
        home / "config" / "config.toml",
        home / "bin" / "paperflow.cmd",
        home / "cache",
        home / "tmp",
    )
```

Inside `run_checks`, derive `home_paths = _paperflow_home_paths(env)`. If `PAPERFLOW_HOME` is present but `home_paths` is `None`, append a failed required `DataRoot` check and do not fall back to APPDATA. Otherwise use the derived config path and append these exact checks:

```python
checks.extend(
    (
        Check("DataRoot", path_exists(home) and path_is_dir(home), True,
              "DataRoot is available" if path_exists(home) and path_is_dir(home)
              else "DataRoot was not found"),
        Check("PaperFlow wrapper", path_exists(wrapper) and path_is_file(wrapper), True,
              "PaperFlow wrapper is available" if path_exists(wrapper) and path_is_file(wrapper)
              else "PaperFlow wrapper was not found"),
        Check("PaperFlow cache", path_exists(cache) and path_is_dir(cache), True,
              "PaperFlow cache is available" if path_exists(cache) and path_is_dir(cache)
              else "PaperFlow cache was not found"),
        Check("PaperFlow temp", path_exists(temp) and path_is_dir(temp), True,
              "PaperFlow temp is available" if path_exists(temp) and path_is_dir(temp)
              else "PaperFlow temp was not found"),
    )
)
```

Cache each `path_exists` / type result in local booleans before constructing checks so injected filesystem functions are called once per path and tests remain deterministic. In legacy mode, preserve the existing check set so current consumers remain backward compatible.

All messages must be generic (`"DataRoot is available"`, `"PaperFlow wrapper was not found"`) and must not echo untrusted environment values.

- [ ] **Step 4: Run doctor and CLI tests**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_doctor.py tests/test_cli.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- src/paperflow/doctor.py tests/test_doctor.py
git commit -m "feat: diagnose PaperFlow data root"
```

### Task 5: Document, fully verify, publish, and migrate this machine

**Files:**
- Modify: `README.md`
- Modify: `tests/test_installer_contract.py`

- [ ] **Step 1: Add failing README contract assertions**

Extend `test_readme_documents_executable_flow_in_required_order()` with:

```python
assert '-DataRoot "D:\\PaperFlowData"' in text
assert "PAPERFLOW_HOME" in text
assert "PIP_NO_CACHE_DIR" in text
assert "%USERPROFILE%\\.agents\\skills\\paperflow" in text
assert "Obsidian Vault" in text
```

- [ ] **Step 2: Run the README contract test and observe failure**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -k readme -q
```

Expected: failure because DataRoot usage is not documented.

- [ ] **Step 3: Update README**

Document these exact commands:

```powershell
.\scripts\install-windows.ps1 -CheckOnly -VaultPath "D:\ObsidianVault" -DataRoot "D:\PaperFlowData"
.\scripts\install-windows.ps1 -VaultPath "D:\ObsidianVault" -DataRoot "D:\PaperFlowData"
```

Update local configuration, privacy, upgrade, uninstall, and troubleshooting sections. State that `PAPERFLOW_HOME` is app-specific, `PIP_NO_CACHE_DIR=1` applies during installation, the Skill remains under `%USERPROFILE%\.agents`, and Vault reports are user documents rather than cache.

- [ ] **Step 4: Run the full test suite**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 5: Commit documentation**

```powershell
git add -- README.md tests/test_installer_contract.py
git commit -m "docs: explain PaperFlow data root layout"
```

- [ ] **Step 6: Merge the feature branch into the D-drive installation repository**

Verify both worktrees are clean, fetch the exact feature commits into `D:\PaperFlow`, and fast-forward `main` only when ancestry allows it. Confirm `git rev-parse HEAD` matches the feature tip. Do not use reset, clean, or checkout over unrelated changes.

- [ ] **Step 7: Run the real migration**

Run from `D:\PaperFlow`:

```powershell
$env:Path = 'C:\Users\admin\AppData\Local\Programs\Python\Python311;' + $env:Path
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1 `
  -VaultPath 'C:\Users\admin\Documents\Obsidian Vault' `
  -DataRoot 'D:\PaperFlowData'
```

Approve the PATH replacement only after the installer reports a passing doctor result.

- [ ] **Step 8: Verify the live installation**

Run:

```powershell
& 'D:\PaperFlowData\bin\paperflow.cmd' --version
& 'D:\PaperFlowData\bin\paperflow.cmd' --json doctor
& 'D:\PaperFlowData\bin\paperflow.cmd' --json search '3d reconstruction'
```

Verify:

- `D:\PaperFlowData\bin\paperflow.cmd` exists.
- `D:\PaperFlowData\config\config.toml` matches the former config bytes.
- `D:\PaperFlowData\cache` and `D:\PaperFlowData\tmp` exist.
- User PATH contains `D:\PaperFlowData\bin` and not the legacy C-drive bin.
- Legacy C-drive wrapper/config are absent.
- `%USERPROFILE%\.agents\skills\paperflow\SKILL.md` still exists.
- The existing Obsidian Vault and its PaperFlow report are unchanged.

- [ ] **Step 9: Push the feature and update remote main only after local verification**

Push the feature branch first. Fast-forward remote `main` only if local `main`, remote `main`, and the verified feature tip have the intended ancestry. Verify remote SHA parity with `git ls-remote origin refs/heads/main`.
