from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from paperflow.config import (
    ConfigError,
    PaperFlowConfig,
    load_local_config,
    resolve_paperflow_home,
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    required: bool
    message: str


def _first_existing(
    candidates: Sequence[Path],
    path_exists: Callable[[Path], bool],
    path_is_file: Callable[[Path], bool],
) -> bool:
    return any(
        path_exists(candidate) and path_is_file(candidate) for candidate in candidates
    )


def _unique_paths(candidates: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(dict.fromkeys(candidates))


def _cached_path_inspector(
    operation: Callable[[Path], bool],
) -> Callable[[Path], bool]:
    results: dict[Path, bool] = {}

    def inspect(path: Path) -> bool:
        if path not in results:
            results[path] = operation(path)
        return results[path]

    return inspect


def run_checks(
    *,
    config_path: Path | None = None,
    vault_path: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    path_exists: Callable[[Path], bool] = Path.exists,
    path_is_file: Callable[[Path], bool] = Path.is_file,
    path_is_dir: Callable[[Path], bool] = Path.is_dir,
    environ: Mapping[str, str] | None = None,
    python_version: Sequence[int] | None = None,
    skill_path: Path | None = None,
) -> tuple[Check, ...]:
    """Inspect PaperFlow prerequisites without changing system state."""
    env = os.environ if environ is None else environ
    path_exists = _cached_path_inspector(path_exists)
    path_is_file = _cached_path_inspector(path_is_file)
    path_is_dir = _cached_path_inspector(path_is_dir)
    version = tuple(sys.version_info[:3] if python_version is None else python_version)
    checks = [
        Check(
            "Python",
            version >= (3, 11),
            True,
            "Python 3.11+ is available"
            if version >= (3, 11)
            else "Python 3.11+ is required",
        )
    ]

    git_ok = which("git") is not None
    checks.append(
        Check("Git", git_ok, True, "Git is available" if git_ok else "Git was not found")
    )

    data_root: Path | None = None
    data_root_is_valid = True
    if "PAPERFLOW_HOME" in env:
        try:
            data_root = resolve_paperflow_home(
                env, path_exists=path_exists, path_is_dir=path_is_dir
            )
        except ConfigError:
            data_root_is_valid = False
        data_root_ok = (
            data_root is not None
            and path_exists(data_root)
            and path_is_dir(data_root)
        )
        checks.append(
            Check(
                "DataRoot",
                data_root_ok,
                True,
                "DataRoot is available" if data_root_ok else "DataRoot was not found",
            )
        )
        if data_root is not None:
            data_root_components = (
                ("PaperFlow wrapper", data_root / "bin" / "paperflow.cmd", path_is_file),
                ("PaperFlow cache", data_root / "cache", path_is_dir),
                ("PaperFlow temp", data_root / "tmp", path_is_dir),
            )
            component_results = tuple(
                (name, path_exists(path) and expected_type(path))
                for name, path, expected_type in data_root_components
            )
            checks.extend(
                Check(
                    name,
                    ok,
                    True,
                    f"{name} is available" if ok else f"{name} was not found",
                )
                for name, ok in component_results
            )

    actual_config_path = config_path
    if actual_config_path is None:
        if data_root is not None:
            actual_config_path = data_root / "config" / "config.toml"
        elif "PAPERFLOW_HOME" not in env and env.get("APPDATA"):
            actual_config_path = Path(env["APPDATA"]) / "PaperFlow" / "config.toml"

    loaded_config: PaperFlowConfig | None = None
    config_ok = False
    config_message = "Configuration was not found"
    if (
        (config_path is not None or data_root_is_valid)
        and actual_config_path is not None
        and path_exists(actual_config_path)
    ):
        if not path_is_file(actual_config_path):
            config_message = "Configuration is invalid"
        else:
            try:
                loaded_config = load_local_config(actual_config_path)
            except (ConfigError, OSError, ValueError):
                config_message = "Configuration is invalid"
            else:
                config_ok = True
                config_message = "Configuration is valid"
    checks.append(Check("Configuration", config_ok, True, config_message))

    actual_vault_path = vault_path
    if actual_vault_path is None and loaded_config is not None:
        actual_vault_path = loaded_config.vault_path
    vault_ok = (
        actual_vault_path is not None
        and path_exists(actual_vault_path)
        and path_is_dir(actual_vault_path)
    )
    checks.append(
        Check(
            "Vault",
            vault_ok,
            True,
            "Vault path is available" if vault_ok else "Vault path was not found",
        )
    )

    codex_ok = which("codex.cmd") is not None
    if not codex_ok:
        codex_ok = which("codex") is not None
    checks.append(
        Check(
            "Codex",
            codex_ok,
            True,
            "Codex is available" if codex_ok else "Codex was not found",
        )
    )

    if skill_path is not None:
        skill_candidates = (skill_path,)
    else:
        user_home = env.get("USERPROFILE") or env.get("HOME")
        user_candidates = (
            (Path(user_home) / ".agents" / "skills" / "paperflow" / "SKILL.md",)
            if user_home
            else ()
        )
        repo_skill = (
            Path(__file__).resolve().parents[2]
            / ".agents"
            / "skills"
            / "paperflow"
            / "SKILL.md"
        )
        skill_candidates = (*user_candidates, repo_skill)
    skill_ok = _first_existing(skill_candidates, path_exists, path_is_file)
    checks.append(
        Check(
            "PaperFlow Skill",
            skill_ok,
            True,
            "PaperFlow Skill is available"
            if skill_ok
            else "PaperFlow Skill was not found",
        )
    )

    program_files = [
        value
        for value in (
            env.get("PROGRAMFILES"),
            env.get("PROGRAMFILES(X86)"),
            env.get("PROGRAMW6432"),
        )
        if value
    ]
    local_app_data = env.get("LOCALAPPDATA")
    zotero_candidates = [Path(root) / "Zotero" / "zotero.exe" for root in program_files]
    obsidian_candidates = [
        Path(root) / "Obsidian" / "Obsidian.exe" for root in program_files
    ]
    if local_app_data:
        zotero_candidates.append(
            Path(local_app_data) / "Programs" / "Zotero" / "zotero.exe"
        )
        obsidian_candidates.append(
            Path(local_app_data) / "Programs" / "Obsidian" / "Obsidian.exe"
        )
        zotero_candidates.append(Path(local_app_data) / "Zotero" / "zotero.exe")
        obsidian_candidates.append(Path(local_app_data) / "Obsidian" / "Obsidian.exe")
    zotero_candidates = _unique_paths(zotero_candidates)
    obsidian_candidates = _unique_paths(obsidian_candidates)

    zotero_ok = _first_existing(zotero_candidates, path_exists, path_is_file)
    checks.append(
        Check(
            "Zotero",
            zotero_ok,
            False,
            "Zotero is available" if zotero_ok else "Zotero was not found",
        )
    )
    obsidian_ok = _first_existing(obsidian_candidates, path_exists, path_is_file)
    checks.append(
        Check(
            "Obsidian",
            obsidian_ok,
            False,
            "Obsidian is available" if obsidian_ok else "Obsidian was not found",
        )
    )
    checks.append(
        Check(
            "AI Sidebar",
            False,
            False,
            "Verify AI Sidebar manually in Zotero",
        )
    )
    return tuple(checks)
