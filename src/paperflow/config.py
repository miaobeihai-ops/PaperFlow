from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PaperFlowConfig:
    keywords: dict[str, int]
    arxiv_categories: tuple[str, ...]
    timezone: str = "Asia/Hong_Kong"
    top_n: int = 10
    history_reports: int = 30
    vault_path: Path | None = None
    mail_to: str | None = None


def resolve_paperflow_home(
    environ: Mapping[str, str],
    *,
    path_exists: Callable[[Path], bool] = Path.exists,
    path_is_dir: Callable[[Path], bool] = Path.is_dir,
) -> Path | None:
    """Resolve and validate PAPERFLOW_HOME using only supplied state."""
    if "PAPERFLOW_HOME" not in environ:
        return None

    raw_home = environ["PAPERFLOW_HOME"]
    if not raw_home or "\n" in raw_home or "\r" in raw_home:
        raise ConfigError("PAPERFLOW_HOME must be an absolute path")

    if raw_home == "~" or raw_home.startswith(("~/", "~\\")):
        injected_home = environ.get("USERPROFILE") or environ.get("HOME")
        if not injected_home:
            raise ConfigError("PAPERFLOW_HOME must be an absolute path")
        suffix = raw_home[2:] if len(raw_home) > 1 else ""
        home = Path(injected_home) / suffix
    else:
        home = Path(raw_home)

    if not home.is_absolute() or (path_exists(home) and not path_is_dir(home)):
        raise ConfigError("PAPERFLOW_HOME must be an absolute path")
    return home


def default_local_config_path() -> Path:
    home = resolve_paperflow_home(
        os.environ, path_exists=Path.exists, path_is_dir=Path.is_dir
    )
    if home is not None:
        return home / "config" / "config.toml"

    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ConfigError("APPDATA is not set")
    return Path(appdata) / "PaperFlow" / "config.toml"


def _validate_keywords(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or not value:
        raise ConfigError("keywords must be a non-empty table/object")
    if not all(isinstance(key, str) for key in value):
        raise ConfigError("keyword names must be strings")
    if not all(type(weight) is int for weight in value.values()):
        raise ConfigError("keyword weights must be integers")
    return {key.casefold(): weight for key, weight in value.items()}


def _validate_integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ConfigError(f"{field} must be an integer")
    return value


def _validate_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be a non-empty string")
    return value


def _validate_categories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(category, str) and category for category in value
    ):
        raise ConfigError("arxiv_categories must be a list of non-empty strings")
    return tuple(value)


def _build(data: dict[str, Any], *, require_vault: bool) -> PaperFlowConfig:
    keywords = _validate_keywords(data.get("keywords"))
    top_n = _validate_integer(data.get("top_n", 10), "top_n")
    if not 1 <= top_n <= 50:
        raise ConfigError("top_n must be between 1 and 50")
    history_reports = _validate_integer(data.get("history_reports", 30), "history_reports")
    if history_reports < 0:
        raise ConfigError("history_reports must be non-negative")
    arxiv_categories = _validate_categories(data.get("arxiv_categories", ["cs.AI"]))
    timezone = _validate_non_empty_string(data.get("timezone", "Asia/Hong_Kong"), "timezone")

    if require_vault and "vault_path" not in data:
        raise ConfigError("vault_path is required for local configuration")
    vault_raw = (
        _validate_non_empty_string(data["vault_path"], "vault_path")
        if "vault_path" in data
        else None
    )
    mail_to = (
        _validate_non_empty_string(data["mail_to"], "mail_to") if "mail_to" in data else None
    )
    vault_path = Path(vault_raw).expanduser() if vault_raw else None
    if require_vault and vault_path is not None and not vault_path.is_absolute():
        raise ConfigError("vault_path must be absolute")

    return PaperFlowConfig(
        keywords=keywords,
        arxiv_categories=arxiv_categories,
        timezone=timezone,
        top_n=top_n,
        history_reports=history_reports,
        vault_path=vault_path,
        mail_to=mail_to,
    )


def load_local_config(path: Path | None = None) -> PaperFlowConfig:
    config_path = path or default_local_config_path()
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("local configuration is invalid TOML") from exc
    return _build(data, require_vault=True)


def load_cloud_config(raw_json: str) -> PaperFlowConfig:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigError("PAPERFLOW_PRIVATE_CONFIG_JSON is invalid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigError("cloud configuration must be a JSON object")
    return _build(data, require_vault=False)
