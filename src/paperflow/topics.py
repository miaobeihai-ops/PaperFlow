from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from paperflow.errors import ConfigError


@dataclass(frozen=True)
class TopicSettings:
    topics: dict[str, int]
    arxiv_categories: tuple[str, ...]
    timezone: str
    top_n: int
    history_reports: int


def resolve_topics_path(environ: Mapping[str, str] = os.environ) -> Path | None:
    raw = environ.get("PAPERFLOW_TOPICS_PATH")
    if raw is None:
        return None
    if not raw or "\n" in raw or "\r" in raw:
        raise ConfigError("topics path must be an absolute file path")
    path = Path(raw)
    if not path.is_absolute():
        raise ConfigError("topics path must be an absolute file path")
    return path


def _integer(data: dict[str, Any], name: str, default: int) -> int:
    value = data.get(name, default)
    if type(value) is not int:
        raise ConfigError(f"{name} must be an integer")
    return value


def load_topic_settings(path: Path) -> TopicSettings:
    if path.is_symlink():
        raise ConfigError("topic file is invalid")
    if not path.exists():
        raise ConfigError("topic file was not found")
    if not path.is_file():
        raise ConfigError("topic file is invalid")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("topic file is invalid") from exc

    topics = data.get("topics")
    if not isinstance(topics, dict) or not topics:
        raise ConfigError("topics must be a non-empty table")
    if not all(isinstance(key, str) and key.strip() for key in topics):
        raise ConfigError("topic names must be non-empty strings")
    if not all(type(value) is int and 1 <= value <= 100 for value in topics.values()):
        raise ConfigError("topic weights must be integers from 1 through 100")

    categories = data.get("arxiv_categories", ["cs.AI"])
    if not isinstance(categories, list) or not all(
        isinstance(value, str) and value for value in categories
    ):
        raise ConfigError("arxiv_categories must be a list of non-empty strings")
    timezone = data.get("timezone", "Asia/Hong_Kong")
    if not isinstance(timezone, str) or not timezone:
        raise ConfigError("timezone must be a non-empty string")
    top_n = _integer(data, "top_n", 10)
    history_reports = _integer(data, "history_reports", 30)
    if not 1 <= top_n <= 50:
        raise ConfigError("top_n must be between 1 and 50")
    if history_reports < 0:
        raise ConfigError("history_reports must be non-negative")

    return TopicSettings(
        topics={key.casefold(): value for key, value in topics.items()},
        arxiv_categories=tuple(categories),
        timezone=timezone,
        top_n=top_n,
        history_reports=history_reports,
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_topic_settings(settings: TopicSettings) -> str:
    categories = ", ".join(_toml_string(value) for value in settings.arxiv_categories)
    lines = [
        f"top_n = {settings.top_n}",
        f"timezone = {_toml_string(settings.timezone)}",
        f"history_reports = {settings.history_reports}",
        f"arxiv_categories = [{categories}]",
        "",
        "[topics]",
    ]
    lines.extend(
        f"{_toml_string(name)} = {weight}"
        for name, weight in sorted(settings.topics.items())
    )
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, settings: TopicSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(render_topic_settings(settings))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def add_topic(path: Path, name: str, weight: int) -> tuple[bool, TopicSettings]:
    cleaned = name.strip().casefold()
    if not cleaned:
        raise ConfigError("topic must not be blank")
    if type(weight) is not int or not 1 <= weight <= 100:
        raise ConfigError("topic weight must be from 1 through 100")
    current = load_topic_settings(path)
    changed = current.topics.get(cleaned) != weight
    updated = replace(current, topics={**current.topics, cleaned: weight})
    if changed:
        _write_atomic(path, updated)
    return changed, updated


def remove_topic(path: Path, name: str) -> tuple[bool, TopicSettings]:
    cleaned = name.strip().casefold()
    if not cleaned:
        raise ConfigError("topic must not be blank")
    current = load_topic_settings(path)
    if cleaned not in current.topics:
        return False, current
    topics = dict(current.topics)
    del topics[cleaned]
    if not topics:
        raise ConfigError("at least one topic is required")
    updated = replace(current, topics=topics)
    _write_atomic(path, updated)
    return True, updated
