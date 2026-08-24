from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
from typing import Any
from urllib.parse import urlsplit

from paperflow.errors import ConfigError


_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PROVIDERS = {"arxiv", "huggingface", "crossref", "openalex", "feed"}
_FIELDS = {
    "display_name",
    "language",
    "lookback_hours",
    "candidate_limit",
    "deep_read_limit",
    "query_seeds",
    "include_concepts",
    "exclude_concepts",
    "providers",
    "arxiv_categories",
    "feeds",
    "rubric",
    "report_sections",
}
_OVERLAY_FIELDS = {
    "language",
    "lookback_hours",
    "candidate_limit",
    "deep_read_limit",
    "query_seeds",
    "include_concepts",
    "exclude_concepts",
    "rubric",
    "report_sections",
}


@dataclass(frozen=True)
class DomainProfile:
    slug: str
    display_name: str
    language: str
    lookback_hours: int
    candidate_limit: int
    deep_read_limit: int
    query_seeds: tuple[str, ...]
    include_concepts: tuple[str, ...]
    exclude_concepts: tuple[str, ...]
    providers: tuple[str, ...]
    arxiv_categories: tuple[str, ...]
    feeds: tuple[str, ...]
    rubric: tuple[str, ...]
    report_sections: tuple[str, ...]


def _read_toml(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ConfigError(f"{label} is unavailable")
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except ConfigError:
        raise
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{label} is invalid") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{label} is invalid")
    return data


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"domain {field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f"domain {field} must be between {minimum} and {maximum}")
    return value


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ConfigError(f"domain {field} must be a list of non-empty strings")
    result = tuple(item.strip() for item in value)
    if not result and not allow_empty:
        raise ConfigError(f"domain {field} must not be empty")
    if len(set(result)) != len(result):
        raise ConfigError(f"domain {field} must not contain duplicates")
    return result


def _build_profile(slug: str, data: dict[str, Any]) -> DomainProfile:
    unknown = set(data) - _FIELDS
    missing = _FIELDS - set(data)
    if unknown or missing:
        raise ConfigError("domain profile fields are invalid")

    providers = _strings(data["providers"], "providers")
    if any(provider not in _PROVIDERS for provider in providers):
        raise ConfigError("domain providers contain an unsupported provider")
    feeds = _strings(data["feeds"], "feeds", allow_empty=True)
    for feed in feeds:
        parsed = urlsplit(feed)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ConfigError("feed URLs must use HTTPS")
    if len(feeds) > 20:
        raise ConfigError("domain feeds must contain at most 20 URLs")

    return DomainProfile(
        slug=slug,
        display_name=_string(data["display_name"], "display_name"),
        language=_string(data["language"], "language"),
        lookback_hours=_integer(data["lookback_hours"], "lookback_hours", 1, 168),
        candidate_limit=_integer(data["candidate_limit"], "candidate_limit", 1, 500),
        deep_read_limit=_integer(data["deep_read_limit"], "deep_read_limit", 0, 10),
        query_seeds=_strings(data["query_seeds"], "query_seeds"),
        include_concepts=_strings(
            data["include_concepts"], "include_concepts", allow_empty=True
        ),
        exclude_concepts=_strings(
            data["exclude_concepts"], "exclude_concepts", allow_empty=True
        ),
        providers=providers,
        arxiv_categories=_strings(
            data["arxiv_categories"], "arxiv_categories", allow_empty=True
        ),
        feeds=feeds,
        rubric=_strings(data["rubric"], "rubric"),
        report_sections=_strings(data["report_sections"], "report_sections"),
    )


def load_domain_profile(
    slug: str,
    *,
    project_root: Path,
    overlay_path: Path | None = None,
) -> DomainProfile:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise ConfigError("invalid domain")
    root = Path(project_root)
    data = _read_toml(
        root / "config" / "domains" / f"{slug}.toml",
        label="domain profile",
    )
    if overlay_path is not None:
        overlay = _read_toml(Path(overlay_path), label="private domain overlay")
        if set(overlay) - _OVERLAY_FIELDS:
            raise ConfigError("private domain overlay contains forbidden fields")
        data = {**data, **overlay}
    return _build_profile(slug, data)
