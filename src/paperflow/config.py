from __future__ import annotations

import json
import os
import tomllib
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


def default_local_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ConfigError("APPDATA is not set")
    return Path(appdata) / "PaperFlow" / "config.toml"


def _build(data: dict[str, Any], *, require_vault: bool) -> PaperFlowConfig:
    keywords = data.get("keywords")
    if not isinstance(keywords, dict) or not keywords:
        raise ConfigError("keywords must be a non-empty table/object")
    normalized_keywords = {str(key).casefold(): int(value) for key, value in keywords.items()}
    top_n = int(data.get("top_n", 10))
    if not 1 <= top_n <= 50:
        raise ConfigError("top_n must be between 1 and 50")
    vault_raw = data.get("vault_path")
    if require_vault and not vault_raw:
        raise ConfigError("vault_path is required for local configuration")
    return PaperFlowConfig(
        keywords=normalized_keywords,
        arxiv_categories=tuple(str(value) for value in data.get("arxiv_categories", ["cs.AI"])),
        timezone=str(data.get("timezone", "Asia/Hong_Kong")),
        top_n=top_n,
        history_reports=int(data.get("history_reports", 30)),
        vault_path=Path(vault_raw).expanduser() if vault_raw else None,
        mail_to=str(data["mail_to"]) if data.get("mail_to") else None,
    )


def load_local_config(path: Path | None = None) -> PaperFlowConfig:
    config_path = path or default_local_config_path()
    with config_path.open("rb") as handle:
        return _build(tomllib.load(handle), require_vault=True)


def load_cloud_config(raw_json: str) -> PaperFlowConfig:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigError("PAPERFLOW_PRIVATE_CONFIG_JSON is invalid JSON") from exc
    return _build(data, require_vault=False)
