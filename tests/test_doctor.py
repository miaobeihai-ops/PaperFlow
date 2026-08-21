from __future__ import annotations

import importlib
import importlib.util
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def _doctor():
    assert importlib.util.find_spec("paperflow.doctor") is not None, (
        "paperflow.doctor must be implemented"
    )
    return importlib.import_module("paperflow.doctor")


def _by_name(checks):
    return {check.name: check for check in checks}


def _valid_config(config_path: Path, vault_path: Path) -> None:
    config_path.write_text(
        f'''vault_path = "{vault_path.as_posix()}"
keywords = {{ robotics = 5 }}
arxiv_categories = ["cs.RO"]
''',
        encoding="utf-8",
    )


def test_check_is_frozen():
    check = _doctor().Check("Python", True, True, "Python 3.11+ is available")

    with pytest.raises(FrozenInstanceError):
        check.ok = False


def test_run_checks_core_injection_and_windows_app_candidates(tmp_path):
    doctor = _doctor()
    config_path = tmp_path / "missing.toml"
    vault_path = tmp_path / "missing-vault"
    skill_path = tmp_path / "missing-skill" / "SKILL.md"
    which_calls = []
    observed_paths = []
    obsidian_programs = Path(
        "C:/Users/test/AppData/Local/Programs/Obsidian/Obsidian.exe"
    )
    zotero_programs = Path(
        "C:/Users/test/AppData/Local/Programs/Zotero/zotero.exe"
    )

    def which(command):
        which_calls.append(command)
        if command == "git":
            return "C:/Program Files/Git/cmd/git.exe"
        if command == "codex.cmd":
            return "C:/Users/test/AppData/Roaming/npm/codex.cmd"
        return None

    def path_exists(path):
        observed_paths.append(path)
        return path in {obsidian_programs, zotero_programs}

    checks = _by_name(
        doctor.run_checks(
            config_path=config_path,
            vault_path=vault_path,
            skill_path=skill_path,
            which=which,
            path_exists=path_exists,
            path_is_file=lambda path: path in {obsidian_programs, zotero_programs},
            path_is_dir=lambda _path: False,
            environ={
                "PROGRAMFILES": "C:/Program Files",
                "LOCALAPPDATA": "C:/Users/test/AppData/Local",
                "USERPROFILE": "C:/Users/test",
            },
            python_version=(3, 11, 9),
        )
    )

    assert checks["Git"].ok is True
    assert checks["Configuration"].ok is False
    assert checks["Vault"].ok is False
    assert checks["Codex"].ok is True
    assert checks["Zotero"].ok is True
    assert checks["Obsidian"].ok is True
    assert checks["AI Sidebar"].ok is False
    assert checks["AI Sidebar"].required is False
    assert which_calls == ["git", "codex.cmd"]
    assert obsidian_programs in observed_paths
    assert zotero_programs in observed_paths


@pytest.mark.parametrize(
    ("check_name", "other_name", "relative_path"),
    [
        ("Obsidian", "Zotero", "Programs/Obsidian/Obsidian.exe"),
        ("Zotero", "Obsidian", "Programs/Zotero/zotero.exe"),
    ],
)
def test_user_level_programs_app_candidate_is_detected(
    check_name, other_name, relative_path
):
    doctor = _doctor()
    local_app_data = Path("C:/Users/test/AppData/Local")
    expected = local_app_data / relative_path
    observed_paths = []

    def path_exists(path):
        observed_paths.append(path)
        return path == expected

    checks = _by_name(
        doctor.run_checks(
            config_path=Path("C:/missing.toml"),
            vault_path=Path("C:/missing-vault"),
            skill_path=Path("C:/missing-skill.md"),
            which=lambda _command: None,
            path_exists=path_exists,
            path_is_file=lambda path: path == expected,
            path_is_dir=lambda _path: False,
            environ={"LOCALAPPDATA": str(local_app_data)},
            python_version=(3, 11, 0),
        )
    )

    assert expected in observed_paths
    assert checks[check_name].ok is True
    assert checks[other_name].ok is False


def test_windows_app_candidates_are_stably_ordered_and_deduplicated():
    doctor = _doctor()
    observed_paths = []

    def path_exists(path):
        observed_paths.append(path)
        return False

    doctor.run_checks(
        config_path=Path("C:/missing.toml"),
        vault_path=Path("C:/missing-vault"),
        skill_path=Path("C:/missing-skill.md"),
        which=lambda _command: None,
        path_exists=path_exists,
        path_is_file=lambda _path: False,
        path_is_dir=lambda _path: False,
        environ={
            "PROGRAMFILES": "C:/Program Files",
            "PROGRAMFILES(X86)": "C:/Program Files",
            "PROGRAMW6432": "C:/ProgramW6432",
            "LOCALAPPDATA": "C:/Users/test/AppData/Local",
        },
        python_version=(3, 11, 0),
    )

    executable_candidates = [
        path for path in observed_paths if path.suffix.casefold() == ".exe"
    ]
    assert executable_candidates == [
        Path("C:/Program Files/Zotero/zotero.exe"),
        Path("C:/ProgramW6432/Zotero/zotero.exe"),
        Path("C:/Users/test/AppData/Local/Programs/Zotero/zotero.exe"),
        Path("C:/Users/test/AppData/Local/Zotero/zotero.exe"),
        Path("C:/Program Files/Obsidian/Obsidian.exe"),
        Path("C:/ProgramW6432/Obsidian/Obsidian.exe"),
        Path("C:/Users/test/AppData/Local/Programs/Obsidian/Obsidian.exe"),
        Path("C:/Users/test/AppData/Local/Obsidian/Obsidian.exe"),
    ]
    assert len(executable_candidates) == len(set(executable_candidates))


def test_injected_path_types_reject_wrong_types_and_accept_correct_types(tmp_path):
    doctor = _doctor()
    config_path = tmp_path / "config.toml"
    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    _valid_config(config_path, vault_dir)
    vault_file = tmp_path / "vault-file"
    vault_file.write_text("not a directory", encoding="utf-8")
    skill_dir = tmp_path / "skill-dir"
    skill_dir.mkdir()
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("skill", encoding="utf-8")
    local_app_data = tmp_path / "Local"
    obsidian_path = local_app_data / "Programs" / "Obsidian" / "Obsidian.exe"
    zotero_path = local_app_data / "Programs" / "Zotero" / "zotero.exe"
    existing = {
        config_path,
        vault_dir,
        vault_file,
        skill_dir,
        skill_file,
        obsidian_path,
        zotero_path,
    }

    wrong = _by_name(
        doctor.run_checks(
            config_path=skill_dir,
            vault_path=vault_file,
            skill_path=skill_dir,
            which=lambda _command: None,
            path_exists=lambda path: path in existing,
            path_is_file=lambda path: path == config_path,
            path_is_dir=lambda path: path in {skill_dir, obsidian_path, zotero_path},
            environ={"LOCALAPPDATA": str(local_app_data)},
            python_version=(3, 11, 0),
        )
    )

    assert wrong["Configuration"].ok is False
    assert wrong["Vault"].ok is False
    assert wrong["PaperFlow Skill"].ok is False
    assert wrong["Obsidian"].ok is False
    assert wrong["Zotero"].ok is False

    correct = _by_name(
        doctor.run_checks(
            config_path=config_path,
            vault_path=vault_dir,
            skill_path=skill_file,
            which=lambda _command: None,
            path_exists=lambda path: path in existing,
            path_is_file=lambda path: path
            in {config_path, skill_file, obsidian_path, zotero_path},
            path_is_dir=lambda path: path == vault_dir,
            environ={"LOCALAPPDATA": str(local_app_data)},
            python_version=(3, 11, 0),
        )
    )

    assert correct["Configuration"].ok is True
    assert correct["Vault"].ok is True
    assert correct["PaperFlow Skill"].ok is True
    assert correct["Obsidian"].ok is True
    assert correct["Zotero"].ok is True


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout


def test_path_type_checks_follow_valid_links_and_reject_broken_ones(tmp_path):
    doctor = _doctor()
    target_root = tmp_path / "TargetRoot"
    target_root.mkdir()
    vault_target = target_root / "Vault"
    vault_target.mkdir()
    config_target = target_root / "config.toml"
    _valid_config(config_target, vault_target)
    skill_target = target_root / "SKILL.md"
    skill_target.write_text("skill", encoding="utf-8")
    obsidian_target = (
        target_root / "Local" / "Programs" / "Obsidian" / "Obsidian.exe"
    )
    zotero_target = target_root / "Local" / "Programs" / "Zotero" / "zotero.exe"
    obsidian_target.parent.mkdir(parents=True)
    zotero_target.parent.mkdir(parents=True)
    obsidian_target.write_text("app", encoding="utf-8")
    zotero_target.write_text("app", encoding="utf-8")

    linked_root = tmp_path / "LinkedRoot"
    _make_directory_link(linked_root, target_root)

    valid = _by_name(
        doctor.run_checks(
            config_path=linked_root / "config.toml",
            vault_path=linked_root / "Vault",
            skill_path=linked_root / "SKILL.md",
            which=lambda _command: None,
            environ={"LOCALAPPDATA": str(linked_root / "Local")},
            python_version=(3, 11, 0),
        )
    )
    assert valid["Configuration"].ok is True
    assert valid["Vault"].ok is True
    assert valid["PaperFlow Skill"].ok is True
    assert valid["Obsidian"].ok is True
    assert valid["Zotero"].ok is True

    broken_target = tmp_path / "BrokenTarget"
    broken_target.mkdir()
    broken_root = tmp_path / "BrokenRoot"
    _make_directory_link(broken_root, broken_target)
    broken_target.rmdir()
    broken = _by_name(
        doctor.run_checks(
            config_path=linked_root / "config.toml",
            vault_path=broken_root / "Vault",
            skill_path=broken_root / "SKILL.md",
            which=lambda _command: None,
            environ={"LOCALAPPDATA": str(broken_root / "Local")},
            python_version=(3, 11, 0),
        )
    )
    assert broken["Vault"].ok is False
    assert broken["PaperFlow Skill"].ok is False
    assert broken["Obsidian"].ok is False
    assert broken["Zotero"].ok is False


def test_invalid_config_and_missing_vault_are_fixed_and_sanitized(tmp_path):
    doctor = _doctor()
    private = "PRIVATE_CONFIG_SENTINEL"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'vault_path = "{private}\n',
        encoding="utf-8",
    )

    checks = _by_name(
        doctor.run_checks(
            config_path=config_path,
            vault_path=tmp_path / "missing-vault",
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            path_exists=Path.exists,
            path_is_file=Path.is_file,
            path_is_dir=Path.is_dir,
            environ={},
            python_version=(3, 10, 14),
        )
    )

    assert checks["Python"].ok is False
    assert checks["Configuration"].ok is False
    assert checks["Configuration"].message == "Configuration is invalid"
    assert checks["Vault"].ok is False
    assert checks["Vault"].message == "Vault path was not found"
    assert private not in " ".join(check.message for check in checks.values())


def test_run_checks_is_read_only(tmp_path):
    doctor = _doctor()
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    config_path = tmp_path / "config.toml"
    _valid_config(config_path, vault_path)
    skill_path = tmp_path / ".agents" / "skills" / "paperflow" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("skill", encoding="utf-8")
    before = {
        path.relative_to(tmp_path): (path.is_dir(), path.stat().st_size)
        for path in tmp_path.rglob("*")
    }

    checks = _by_name(
        doctor.run_checks(
            config_path=config_path,
            skill_path=skill_path,
            which=lambda command: f"C:/{command}.exe",
            path_exists=Path.exists,
            path_is_file=Path.is_file,
            path_is_dir=Path.is_dir,
            environ={},
            python_version=(3, 12, 0),
        )
    )

    after = {
        path.relative_to(tmp_path): (path.is_dir(), path.stat().st_size)
        for path in tmp_path.rglob("*")
    }
    assert checks["Configuration"].ok is True
    assert checks["Vault"].ok is True
    assert checks["PaperFlow Skill"].ok is True
    assert after == before


def test_default_skill_path_uses_current_agents_location():
    doctor = _doctor()
    observed = []

    doctor.run_checks(
        config_path=Path("C:/missing.toml"),
        vault_path=Path("C:/missing-vault"),
        which=lambda _command: None,
        path_exists=lambda path: observed.append(str(path)) or False,
        path_is_file=lambda _path: False,
        path_is_dir=lambda _path: False,
        environ={"USERPROFILE": "C:/Users/researcher"},
        python_version=(3, 11, 0),
    )

    normalized = [path.replace("\\", "/").casefold() for path in observed]
    assert any(
        path.endswith("/users/researcher/.agents/skills/paperflow/skill.md")
        for path in normalized
    )
    assert not any("/.codex/skills/" in path for path in normalized)


def test_valid_data_root_adds_required_checks_and_uses_its_config(tmp_path):
    doctor = _doctor()
    home = tmp_path / "PaperFlowHome"
    config_path = home / "config" / "config.toml"
    vault_path = tmp_path / "Vault"
    wrapper_path = home / "bin" / "paperflow.cmd"
    cache_path = home / "cache"
    temp_path = home / "tmp"
    config_path.parent.mkdir(parents=True)
    wrapper_path.parent.mkdir()
    cache_path.mkdir()
    temp_path.mkdir()
    vault_path.mkdir()
    wrapper_path.write_text("@echo off\n", encoding="utf-8")
    _valid_config(config_path, vault_path)
    calls = {"exists": {}, "file": {}, "dir": {}}

    def counted(kind, operation):
        def inspect(path):
            calls[kind][path] = calls[kind].get(path, 0) + 1
            return operation(path)

        return inspect

    checks = _by_name(
        doctor.run_checks(
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            path_exists=counted("exists", Path.exists),
            path_is_file=counted("file", Path.is_file),
            path_is_dir=counted("dir", Path.is_dir),
            environ={
                "PAPERFLOW_HOME": str(home),
                "APPDATA": str(tmp_path / "FallbackAppData"),
            },
            python_version=(3, 11, 0),
        )
    )

    expected = {
        "DataRoot": "DataRoot is available",
        "PaperFlow wrapper": "PaperFlow wrapper is available",
        "PaperFlow cache": "PaperFlow cache is available",
        "PaperFlow temp": "PaperFlow temp is available",
    }
    for name, message in expected.items():
        assert checks[name] == doctor.Check(name, True, True, message)
    assert checks["Configuration"].ok is True
    assert checks["Vault"].ok is True
    for path in (home, wrapper_path, cache_path, temp_path, config_path, vault_path):
        assert calls["exists"][path] == 1
    assert calls["dir"][home] == 1
    assert calls["file"][wrapper_path] == 1
    assert calls["dir"][cache_path] == 1
    assert calls["dir"][temp_path] == 1


def test_data_root_tilde_uses_only_the_injected_user_profile(monkeypatch, tmp_path):
    doctor = _doctor()
    process_home = tmp_path / "ProcessHome"
    injected_home = tmp_path / "InjectedHome"
    home = injected_home / "PaperFlow"
    config_path = home / "config" / "config.toml"
    vault_path = tmp_path / "Vault"
    config_path.parent.mkdir(parents=True)
    vault_path.mkdir()
    _valid_config(config_path, vault_path)
    monkeypatch.setenv("USERPROFILE", str(process_home))
    monkeypatch.setenv("HOME", str(process_home))

    checks = _by_name(
        doctor.run_checks(
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            environ={
                "PAPERFLOW_HOME": "~/PaperFlow",
                "USERPROFILE": str(injected_home),
            },
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"].ok is True
    assert checks["Configuration"].ok is True
    assert checks["Vault"].ok is True


def test_data_root_tilde_without_injected_home_is_invalid_and_sanitized(tmp_path):
    doctor = _doctor()

    checks = _by_name(
        doctor.run_checks(
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            environ={"PAPERFLOW_HOME": "~/PRIVATE_HOME_SENTINEL"},
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"].ok is False
    assert checks["Configuration"].ok is False
    assert "PRIVATE_HOME_SENTINEL" not in " ".join(
        check.message for check in checks.values()
    )


def test_doctor_delegates_data_root_validation_to_shared_resolver(
    monkeypatch, tmp_path
):
    doctor = _doctor()
    home = tmp_path / "ResolvedHome"
    home.mkdir()
    environ = {"PAPERFLOW_HOME": "ignored-by-spy"}
    calls = []

    def resolve(injected_environ, *, path_exists, path_is_dir):
        calls.append((injected_environ, path_exists, path_is_dir))
        return home

    monkeypatch.setattr(doctor, "resolve_paperflow_home", resolve)

    checks = _by_name(
        doctor.run_checks(
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            environ=environ,
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"].ok is True
    assert len(calls) == 1
    assert calls[0][0] is environ


def test_invalid_present_data_root_does_not_fall_back_or_leak_path(tmp_path):
    doctor = _doctor()
    private_home = tmp_path / "PRIVATE_HOME_SENTINEL"
    private_home.write_text("not a directory", encoding="utf-8")
    fallback_config = tmp_path / "AppData" / "PaperFlow" / "config.toml"
    fallback_vault = tmp_path / "FallbackVault"
    fallback_config.parent.mkdir(parents=True)
    fallback_vault.mkdir()
    _valid_config(fallback_config, fallback_vault)
    observed_paths = []

    checks = _by_name(
        doctor.run_checks(
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            path_exists=lambda path: observed_paths.append(path) or path.exists(),
            path_is_file=Path.is_file,
            path_is_dir=Path.is_dir,
            environ={
                "PAPERFLOW_HOME": str(private_home),
                "APPDATA": str(fallback_config.parents[1]),
            },
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"] == doctor.Check(
        "DataRoot", False, True, "DataRoot was not found"
    )
    assert checks["Configuration"].ok is False
    assert fallback_config not in observed_paths
    messages = " ".join(check.message for check in checks.values())
    assert str(private_home) not in messages
    assert "PRIVATE_HOME_SENTINEL" not in messages


@pytest.mark.parametrize(
    "invalid_home", ["", "relative-private-home", "C:\\private\r\nhome"]
)
def test_syntactically_invalid_data_root_does_not_fall_back(
    tmp_path, invalid_home
):
    doctor = _doctor()
    fallback_config = tmp_path / "AppData" / "PaperFlow" / "config.toml"
    fallback_config.parent.mkdir(parents=True)
    fallback_config.write_text("private fallback", encoding="utf-8")
    observed_paths = []

    checks = _by_name(
        doctor.run_checks(
            config_path=None,
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            path_exists=lambda path: observed_paths.append(path) or path.exists(),
            environ={
                "PAPERFLOW_HOME": invalid_home,
                "APPDATA": str(fallback_config.parents[1]),
            },
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"].ok is False
    assert checks["DataRoot"].required is True
    assert checks["Configuration"].ok is False
    assert fallback_config not in observed_paths
    if invalid_home:
        assert invalid_home not in " ".join(check.message for check in checks.values())


def test_data_root_components_require_existence_and_correct_type(tmp_path):
    doctor = _doctor()
    home = tmp_path / "PaperFlowHome"
    wrapper_path = home / "bin" / "paperflow.cmd"
    cache_path = home / "cache"
    home.mkdir()
    wrapper_path.parent.mkdir()
    wrapper_path.mkdir()
    cache_path.write_text("not a directory", encoding="utf-8")

    checks = _by_name(
        doctor.run_checks(
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            environ={"PAPERFLOW_HOME": str(home)},
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"] == doctor.Check(
        "DataRoot", True, True, "DataRoot is available"
    )
    assert checks["PaperFlow wrapper"] == doctor.Check(
        "PaperFlow wrapper", False, True, "PaperFlow wrapper was not found"
    )
    assert checks["PaperFlow cache"] == doctor.Check(
        "PaperFlow cache", False, True, "PaperFlow cache was not found"
    )
    assert checks["PaperFlow temp"] == doctor.Check(
        "PaperFlow temp", False, True, "PaperFlow temp was not found"
    )


def test_explicit_config_path_wins_while_data_root_checks_remain(tmp_path):
    doctor = _doctor()
    home = tmp_path / "PaperFlowHome"
    home.write_text("not a directory", encoding="utf-8")
    explicit_config = tmp_path / "explicit.toml"
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    _valid_config(explicit_config, vault_path)

    checks = _by_name(
        doctor.run_checks(
            config_path=explicit_config,
            skill_path=tmp_path / "missing-skill.md",
            which=lambda _command: None,
            environ={"PAPERFLOW_HOME": str(home)},
            python_version=(3, 11, 0),
        )
    )

    assert checks["DataRoot"].ok is False
    assert checks["Configuration"].ok is True
    assert checks["Vault"].ok is True


def test_legacy_mode_check_contract_is_unchanged():
    doctor = _doctor()

    checks = doctor.run_checks(
        config_path=Path("C:/missing.toml"),
        vault_path=Path("C:/missing-vault"),
        skill_path=Path("C:/missing-skill.md"),
        which=lambda _command: None,
        path_exists=lambda _path: False,
        path_is_file=lambda _path: False,
        path_is_dir=lambda _path: False,
        environ={"APPDATA": "C:/Users/test/AppData/Roaming"},
        python_version=(3, 10, 0),
    )

    assert checks == (
        doctor.Check("Python", False, True, "Python 3.11+ is required"),
        doctor.Check("Git", False, True, "Git was not found"),
        doctor.Check("Configuration", False, True, "Configuration was not found"),
        doctor.Check("Vault", False, True, "Vault path was not found"),
        doctor.Check("Codex", False, True, "Codex was not found"),
        doctor.Check(
            "PaperFlow Skill", False, True, "PaperFlow Skill was not found"
        ),
        doctor.Check("Zotero", False, False, "Zotero was not found"),
        doctor.Check("Obsidian", False, False, "Obsidian was not found"),
        doctor.Check(
            "AI Sidebar", False, False, "Verify AI Sidebar manually in Zotero"
        ),
    )
