from __future__ import annotations

import importlib
import importlib.util
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
        environ={
            "PROGRAMFILES": "C:/Program Files",
            "PROGRAMFILES(X86)": "C:/Program Files",
            "LOCALAPPDATA": "C:/Users/test/AppData/Local",
        },
        python_version=(3, 11, 0),
    )

    executable_candidates = [
        path for path in observed_paths if path.suffix.casefold() == ".exe"
    ]
    assert executable_candidates == [
        Path("C:/Program Files/Zotero/zotero.exe"),
        Path("C:/Users/test/AppData/Local/Programs/Zotero/zotero.exe"),
        Path("C:/Users/test/AppData/Local/Zotero/zotero.exe"),
        Path("C:/Program Files/Obsidian/Obsidian.exe"),
        Path("C:/Users/test/AppData/Local/Programs/Obsidian/Obsidian.exe"),
        Path("C:/Users/test/AppData/Local/Obsidian/Obsidian.exe"),
    ]
    assert len(executable_candidates) == len(set(executable_candidates))


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
        environ={"USERPROFILE": "C:/Users/researcher"},
        python_version=(3, 11, 0),
    )

    normalized = [path.replace("\\", "/").casefold() for path in observed]
    assert any(
        path.endswith("/users/researcher/.agents/skills/paperflow/skill.md")
        for path in normalized
    )
    assert not any("/.codex/skills/" in path for path in normalized)
