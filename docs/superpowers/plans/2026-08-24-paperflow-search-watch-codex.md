# PaperFlow Search, Watch, and Codex Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a database-free workflow where one-off filtered search, persistent daily topics, local Obsidian reports, cloud email, and Codex orchestration use one versioned topic file.

**Architecture:** Add a focused `topics.py` boundary that owns the shared TOML schema and atomic mutations. Merge it into the existing runtime config without breaking legacy inline TOML or private JSON callers, then expose bounded search filters and `watch` commands through the existing JSON CLI. Keep provider URLs internal, local Vault data local, and GitHub Secrets limited to mail delivery.

**Tech Stack:** Python 3.11, standard-library `argparse`/`dataclasses`/`tomllib`/`tempfile`, `httpx`, pytest, PowerShell 5.1+, GitHub Actions, Markdown Codex Skill.

---

## File Map

- Create `config/topics.toml`: public, versioned source of categories, weights, timezone, top-N, and history limit.
- Create `src/paperflow/errors.py`: shared `ConfigError` definition used by topic and runtime configuration loaders.
- Create `src/paperflow/topics.py`: topic path resolution, validation, deterministic TOML rendering, and atomic add/remove.
- Create `tests/test_topics.py`: direct tests for the new topic boundary.
- Modify `src/paperflow/config.py`: merge local/cloud runtime fields with the shared topic file while preserving old formats.
- Modify `src/paperflow/arxiv_source.py`: safe tokenized search plus category/date/limit/sort filters.
- Modify `src/paperflow/cli.py`: search flags, normalized JSON filters, mail-only cloud loading, and `watch` dispatch.
- Modify `src/paperflow/doctor.py`: required topic-file health when explicitly configured.
- Modify `scripts/install-windows.ps1`: set `PAPERFLOW_TOPICS_PATH` in the generated wrapper.
- Modify `.github/workflows/daily.yml`: use `config/topics.toml` and `PAPERFLOW_MAIL_TO`.
- Modify `.agents/skills/paperflow/SKILL.md`: orchestrate multi-query search and approval-gated watch changes.
- Modify `README.md` and `config.example.toml`: document the split local/shared/mail configuration.
- Modify focused tests under `tests/`: preserve contracts and prove the new workflow.

### Task 1: Shared Topic File Boundary

**Files:**
- Create: `config/topics.toml`
- Create: `src/paperflow/errors.py`
- Create: `src/paperflow/topics.py`
- Create: `tests/test_topics.py`

- [ ] **Step 1: Write failing topic parsing and path tests**

Create `tests/test_topics.py` with the wished-for public API:

```python
from pathlib import Path

import pytest

from paperflow.errors import ConfigError
from paperflow.topics import TopicSettings, load_topic_settings, resolve_topics_path


def test_resolve_topics_path_returns_none_when_variable_is_absent():
    assert resolve_topics_path({}) is None


def test_explicit_topics_path_must_be_absolute(tmp_path):
    with pytest.raises(ConfigError, match="topics path must be an absolute file path"):
        resolve_topics_path({"PAPERFLOW_TOPICS_PATH": "config/topics.toml"})


def test_load_topic_settings_validates_and_casefolds_topics(tmp_path):
    path = tmp_path / "topics.toml"
    path.write_text(
        'top_n = 12\ntimezone = "Asia/Hong_Kong"\n'
        'history_reports = 20\narxiv_categories = ["cs.RO", "cs.CV"]\n\n'
        '[topics]\nRobotics = 5\n"3D Reconstruction" = 8\n',
        encoding="utf-8",
    )

    assert load_topic_settings(path) == TopicSettings(
        topics={"robotics": 5, "3d reconstruction": 8},
        arxiv_categories=("cs.RO", "cs.CV"),
        timezone="Asia/Hong_Kong",
        top_n=12,
        history_reports=20,
    )


def test_explicit_missing_topics_path_fails_without_fallback(tmp_path):
    with pytest.raises(ConfigError, match="topic file was not found"):
        load_topic_settings(tmp_path / "missing.toml")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_topics.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'paperflow.errors'` or `paperflow.topics`.

- [ ] **Step 3: Implement the minimal immutable topic model and loader**

Create `src/paperflow/errors.py`:

```python
class ConfigError(ValueError):
    pass
```

Change `src/paperflow/config.py` to import and therefore continue re-exporting the same type:

```python
from paperflow.errors import ConfigError
```

Delete the old local `ConfigError` class from `config.py`. Create `src/paperflow/topics.py`:

```python
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
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
```

Add `config/topics.toml` with the approved values from the design spec.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_topics.py -v
```

Expected: all four tests pass.

- [ ] **Step 5: Add failing deterministic rendering and atomic mutation tests**

Append tests that call:

```python
from paperflow.topics import add_topic, remove_topic, render_topic_settings


def settings():
    return TopicSettings(
        topics={"robotics": 5, "3d reconstruction": 8},
        arxiv_categories=("cs.RO", "cs.CV"),
        timezone="Asia/Hong_Kong",
        top_n=10,
        history_reports=30,
    )


def test_render_topic_settings_is_deterministic():
    assert render_topic_settings(settings()) == (
        'top_n = 10\ntimezone = "Asia/Hong_Kong"\nhistory_reports = 30\n'
        'arxiv_categories = ["cs.RO", "cs.CV"]\n\n[topics]\n'
        '"3d reconstruction" = 8\nrobotics = 5\n'
    )


def test_add_and_remove_topic_write_atomically(tmp_path):
    path = tmp_path / "topics.toml"
    path.write_text(render_topic_settings(settings()), encoding="utf-8")
    changed, updated = add_topic(path, "Vision Language Action", 9)
    assert changed is True
    assert updated.topics["vision language action"] == 9
    changed, updated = remove_topic(path, "ROBOTICS")
    assert changed is True
    assert "robotics" not in updated.topics
    assert list(tmp_path.glob("*.tmp")) == []


def test_remove_missing_topic_is_idempotent(tmp_path):
    path = tmp_path / "topics.toml"
    original = render_topic_settings(settings())
    path.write_text(original, encoding="utf-8")
    changed, updated = remove_topic(path, "missing")
    assert changed is False
    assert updated == settings()
    assert path.read_text(encoding="utf-8") == original
```

- [ ] **Step 6: Run the new mutation tests and verify RED**

Run the three test names with `pytest -v`. Expected: import fails because render/add/remove do not exist.

- [ ] **Step 7: Implement deterministic TOML rendering and `os.replace` mutation**

Add these imports and functions to `topics.py`:

```python
import json
import tempfile
from dataclasses import replace


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
```

- [ ] **Step 8: Run `tests/test_topics.py` and commit**

Expected: all topic tests pass.

```powershell
git add config/topics.toml src/paperflow/errors.py src/paperflow/topics.py src/paperflow/config.py tests/test_topics.py
git commit -m "feat: add shared PaperFlow topic file"
```

### Task 2: Merge Shared Topics Into Runtime Configuration

**Files:**
- Modify: `src/paperflow/config.py`
- Modify: `src/paperflow/cli.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing local precedence and legacy compatibility tests**

Add tests proving that `load_local_config(path, topics_path=...)` keeps `vault_path`, replaces topic fields from `TopicSettings`, and that `load_local_config(path)` still accepts the old inline fields unchanged. Add an explicit missing-topic-path test that returns `ConfigError` instead of silently using inline values.

Use an actual temporary TOML file for both local and topic data; assert the resulting `PaperFlowConfig` fields exactly.

- [ ] **Step 2: Run focused config tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -k "topics or legacy" -v
```

Expected: `TypeError` because `load_local_config` does not accept `topics_path`.

- [ ] **Step 3: Implement a single merge helper in `config.py`**

Add:

```python
def _with_topics(config: PaperFlowConfig, settings: TopicSettings) -> PaperFlowConfig:
    return PaperFlowConfig(
        keywords=dict(settings.topics),
        arxiv_categories=settings.arxiv_categories,
        timezone=settings.timezone,
        top_n=settings.top_n,
        history_reports=settings.history_reports,
        vault_path=config.vault_path,
        mail_to=config.mail_to,
    )
```

Change `load_local_config` and `load_cloud_config` to accept an optional `topics_path: Path | None`. After legacy parsing, merge only when the argument is not `None`. Keep `_build` and old JSON behavior intact.

Use these complete signatures and endings:

```python
from paperflow.errors import ConfigError
from paperflow.topics import TopicSettings, load_topic_settings


def load_local_config(
    path: Path | None = None,
    *,
    topics_path: Path | None = None,
) -> PaperFlowConfig:
    config_path = path or default_local_config_path()
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("local configuration is invalid TOML") from exc
    config = _build(data, require_vault=True)
    return _with_topics(config, load_topic_settings(topics_path)) if topics_path else config


def load_cloud_config(
    raw_json: str,
    *,
    topics_path: Path | None = None,
) -> PaperFlowConfig:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConfigError("PAPERFLOW_PRIVATE_CONFIG_JSON is invalid JSON") from exc
    if not isinstance(data, dict):
        raise ConfigError("cloud configuration must be a JSON object")
    config = _build(data, require_vault=False)
    return _with_topics(config, load_topic_settings(topics_path)) if topics_path else config


def config_from_topics(settings: TopicSettings, *, mail_to: str) -> PaperFlowConfig:
    if not mail_to:
        raise ConfigError("mail recipient must not be blank")
    return PaperFlowConfig(
        keywords=dict(settings.topics),
        arxiv_categories=settings.arxiv_categories,
        timezone=settings.timezone,
        top_n=settings.top_n,
        history_reports=settings.history_reports,
        vault_path=None,
        mail_to=mail_to,
    )
```

Change `_load_config` in `cli.py` to call `resolve_topics_path(os.environ)` once and pass the result to either loader.

- [ ] **Step 4: Run all config tests and verify GREEN**

Run `pytest tests/test_config.py tests/test_cli.py -q`. Expected: all existing and new tests pass.

- [ ] **Step 5: Write failing mail-only cloud tests**

Add CLI tests with `PAPERFLOW_TOPICS_PATH`, `PAPERFLOW_GMAIL_ADDRESS`, `PAPERFLOW_GMAIL_APP_PASSWORD`, and `PAPERFLOW_MAIL_TO`, while deleting `PAPERFLOW_PRIVATE_CONFIG_JSON`. Stub `load_local_config` to fail and assert `_load_email_config` loads topic settings without a Vault and creates `GmailSettings` using the three mail variables.

Retain a separate test proving old private JSON still works when present.

- [ ] **Step 6: Run the mail tests and verify RED**

Expected: exit code 2 and `email configuration is incomplete` because `PAPERFLOW_MAIL_TO` is not used yet.

- [ ] **Step 7: Implement mail-only loading with legacy fallback**

In `_load_email_config`:

Replace `_load_email_config` with:

```python
def _load_email_config() -> tuple[PaperFlowConfig, GmailSettings]:
    address = os.environ.get("PAPERFLOW_GMAIL_ADDRESS")
    app_password = os.environ.get("PAPERFLOW_GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise ConfigError("email configuration is incomplete")

    topics_path = resolve_topics_path(os.environ)
    private_config = os.environ.get("PAPERFLOW_PRIVATE_CONFIG_JSON")
    if private_config is not None:
        config = load_cloud_config(private_config, topics_path=topics_path)
    else:
        mail_to = os.environ.get("PAPERFLOW_MAIL_TO")
        if not mail_to or topics_path is None:
            raise ConfigError("email configuration is incomplete")
        config = config_from_topics(load_topic_settings(topics_path), mail_to=mail_to)

    if not config.mail_to:
        raise ConfigError("email configuration is incomplete")
    try:
        settings = GmailSettings(address, app_password, config.mail_to)
    except ValueError as exc:
        raise ConfigError("email configuration is invalid") from exc
    return config, settings
```

- [ ] **Step 8: Run config/CLI/email tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py tests\test_cli.py tests\test_email.py -q
git add src/paperflow/config.py src/paperflow/cli.py tests/test_config.py tests/test_cli.py
git commit -m "feat: share topic config across local and cloud runs"
```

### Task 3: Safe Filtered arXiv Search

**Files:**
- Modify: `src/paperflow/arxiv_source.py`
- Modify: `tests/test_sources.py`

- [ ] **Step 1: Replace exact-phrase expectations with failing token/filter tests**

Update the existing exact-phrase test to expect:

```python
assert parsed_query["search_query"] == [
    '(all:"vision" AND all:"language" AND all:"action") '
    'AND (cat:cs.RO OR cat:cs.AI) '
    'AND submittedDate:[202608010000 TO 999912312359]'
]
assert parsed_query["max_results"] == ["7"]
assert parsed_query["sortBy"] == ["submittedDate"]
```

Call:

```python
search_arxiv(
    client,
    "vision language action",
    categories=("cs.RO", "cs.AI"),
    since=date(2026, 8, 1),
    max_results=7,
    sort="newest",
)
```

Add parameterized tests rejecting invalid category strings, sort names, and limits without issuing a request. Retain an injection test using operator-like input and assert every token is escaped as a literal rather than passed through as an operator.

- [ ] **Step 2: Run focused source tests and verify RED**

Expected: `TypeError` for unsupported `categories`, `since`, and `sort` arguments.

- [ ] **Step 3: Implement the bounded query builder**

Use this public signature:

```python
def search_arxiv(
    client: httpx.Client,
    query: str,
    max_results: int = 20,
    *,
    categories: tuple[str, ...] = (),
    since: date | None = None,
    sort: str = "relevance",
) -> list[Paper]:
```

Split `query.strip()` with `.split()`, escape each token with the existing backslash/quote rules, and join `all:"..."` terms with `AND`. Validate categories with `re.fullmatch(r"[A-Za-z-]+(?:\.[A-Za-z-]+)?", value)`. Map sort values through `{"relevance": "relevance", "newest": "submittedDate"}` and always use descending order.

Implement the body as:

```python
import re


_CATEGORY = re.compile(r"[A-Za-z-]+(?:\.[A-Za-z-]+)?")
_SORTS = {"relevance": "relevance", "newest": "submittedDate"}


def _literal_term(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'all:"{escaped}"'


def _search_expression(
    query: str,
    categories: tuple[str, ...],
    since: date | None,
) -> str:
    terms = query.strip().split()
    if not terms:
        raise ValueError("query must not be blank")
    expression = " AND ".join(_literal_term(term) for term in terms)
    if categories:
        if not all(_CATEGORY.fullmatch(value) for value in categories):
            raise ValueError("category is invalid")
        expression = f"({expression}) AND (" + " OR ".join(
            f"cat:{value}" for value in categories
        ) + ")"
    if since is not None:
        expression += (
            f" AND submittedDate:[{since.strftime('%Y%m%d')}0000 TO 999912312359]"
        )
    return expression


def search_arxiv(
    client: httpx.Client,
    query: str,
    max_results: int = 20,
    *,
    categories: tuple[str, ...] = (),
    since: date | None = None,
    sort: str = "relevance",
) -> list[Paper]:
    _validate_max_results(max_results)
    if sort not in _SORTS:
        raise ValueError("sort must be relevance or newest")
    encoded = urllib.parse.urlencode(
        {
            "search_query": _search_expression(query, categories, since),
            "start": 0,
            "max_results": max_results,
            "sortBy": _SORTS[sort],
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{encoded}"
    return parse_arxiv_feed(request_with_retry(client, url).text)
```

- [ ] **Step 4: Run all source tests and verify GREEN**

Run `pytest tests/test_sources.py -q`. Expected: all tests pass.

- [ ] **Step 5: Commit the search provider change**

```powershell
git add src/paperflow/arxiv_source.py tests/test_sources.py
git commit -m "feat: add bounded filters to arXiv search"
```

### Task 4: Search Flags and Watch CLI

**Files:**
- Modify: `src/paperflow/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_search_notes.py`

- [ ] **Step 1: Write failing search option JSON tests**

Add a test that invokes:

```python
main([
    "--json", "search", "vision language action",
    "--category", "cs.RO", "--category", "cs.AI",
    "--since", "30d", "--limit", "7", "--sort", "newest",
])
```

Freeze the current date at `2026-08-24`, stub `search_arxiv`, and assert it receives `since=date(2026, 7, 25)`, the two categories, limit 7, and `newest`. Assert JSON contains:

```python
"filters": {
    "categories": ["cs.RO", "cs.AI"],
    "since": "2026-07-25",
    "limit": 7,
    "sort": "newest",
}
```

Add invalid `--since 0d`, bad ISO date, and out-of-range `--limit` cases that return exit code 2 with JSON errors.

- [ ] **Step 2: Run focused CLI search tests and verify RED**

Expected: argparse rejects the unknown flags.

- [ ] **Step 3: Implement search parser options and normalization helpers**

Add imports `date` and `timedelta`, then add these parser options and helper:

```python
    search.add_argument("--category", action="append", default=[])
    search.add_argument("--since")
    search.add_argument("--limit", default="20")
    search.add_argument("--sort", default="relevance")


def _parse_since(value: str | None, today: date | None = None) -> date | None:
    if value is None:
        return None
    reference = today or date.today()
    if value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
        if days < 1:
            raise ConfigError("since duration must be positive")
        return reference - timedelta(days=days)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError("since must be YYYY-MM-DD or Nd") from exc


def _parse_bounded_integer(value: str, name: str, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not lower <= parsed <= upper:
        raise ConfigError(f"{name} must be between {lower} and {upper}")
    return parsed
```

At the start of `_run_search`, normalize and validate:

```python
        limit = _parse_bounded_integer(args.limit, "limit", 1, 100)
        if args.sort not in ("relevance", "newest"):
            raise ConfigError("sort must be relevance or newest")
        since = _parse_since(args.since)
        categories = tuple(args.category)
```

Replace the online call with:

```python
                online = search_arxiv(
                    client,
                    args.query,
                    max_results=limit,
                    categories=categories,
                    since=since,
                    sort=args.sort,
                )
```

Add this field to the success payload:

```python
        "filters": {
            "categories": list(categories),
            "since": since.isoformat() if since else None,
            "limit": limit,
            "sort": args.sort,
        },
```

Include `ValueError` from search option validation in the existing bounded exit-2 path by converting it to `ConfigError` before making a request.

- [ ] **Step 4: Run CLI/search tests and verify GREEN**

Run `pytest tests/test_cli.py tests/test_search_notes.py tests/test_sources.py -q`.

- [ ] **Step 5: Write failing watch command tests**

Add parser/dispatch tests for global and subcommand JSON positions. Stub `resolve_topics_path`, `load_topic_settings`, `add_topic`, and `remove_topic` to assert:

- `watch list` returns sorted topics and settings;
- `watch add` returns `action: "added"` or `"updated"`, `changed`, `topic`, `weight`, and `topics_path`;
- `watch remove` returns `action: "removed"` or `"unchanged"`;
- missing explicit topic path and invalid weights return exit code 2.

- [ ] **Step 6: Run watch tests and verify RED**

Expected: argparse reports `watch` is not a valid command.

- [ ] **Step 7: Implement the minimal watch parser and `_run_watch`**

Add parser construction:

```python
    watch = subparsers.add_parser("watch")
    watch_commands = watch.add_subparsers(dest="watch_command", required=True)
    watch_list = watch_commands.add_parser("list")
    watch_add = watch_commands.add_parser("add")
    watch_add.add_argument("topic")
    watch_add.add_argument("--weight", required=True)
    watch_remove = watch_commands.add_parser("remove")
    watch_remove.add_argument("topic")
    for command in (watch_list, watch_add, watch_remove):
        command.add_argument(
            "--json", action="store_true", dest="json_output",
            default=argparse.SUPPRESS,
        )
```

Add imports for the topic API and implement:

```python
def _topic_payload(settings: TopicSettings) -> dict[str, object]:
    return {
        "topics": dict(sorted(settings.topics.items())),
        "arxiv_categories": list(settings.arxiv_categories),
        "timezone": settings.timezone,
        "top_n": settings.top_n,
        "history_reports": settings.history_reports,
    }


def _run_watch(args: argparse.Namespace) -> int:
    try:
        path = resolve_topics_path(os.environ)
        if path is None:
            raise ConfigError("topic file is not configured")
        before = load_topic_settings(path)
        action = "listed"
        changed = False
        topic = None
        weight = None
        settings = before
        if args.watch_command == "add":
            topic = args.topic.strip().casefold()
            existed = topic in before.topics
            weight = _parse_bounded_integer(args.weight, "weight", 1, 100)
            changed, settings = add_topic(path, topic, weight)
            action = "updated" if existed and changed else "added" if changed else "unchanged"
        elif args.watch_command == "remove":
            topic = args.topic.strip().casefold()
            changed, settings = remove_topic(path, topic)
            action = "removed" if changed else "unchanged"
    except ConfigError as exc:
        _print_error(args, str(exc))
        return 2
    payload = {
        "ok": True,
        "action": action,
        "changed": changed,
        "topic": topic,
        "weight": weight,
        "topics_path": str(path),
        **_topic_payload(settings),
    }
    if args.json_output:
        _print_json(payload)
    else:
        print(f"watch: {action}; topics: {len(settings.topics)}")
    return 0
```

Dispatch `watch` in `main` immediately before the final return:

```python
    if args.command == "watch":
        return _run_watch(args)
```

- [ ] **Step 8: Run CLI tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_cli.py tests\test_search_notes.py tests\test_topics.py -q
git add src/paperflow/cli.py tests/test_cli.py tests/test_search_notes.py
git commit -m "feat: add filtered search and watch commands"
```

### Task 5: Doctor and Windows Installer Integration

**Files:**
- Modify: `src/paperflow/doctor.py`
- Modify: `scripts/install-windows.ps1`
- Modify: `tests/test_doctor.py`
- Modify: `tests/test_installer_contract.py`

- [ ] **Step 1: Write failing doctor topic-file tests**

Add tests that inject an environment containing `PAPERFLOW_TOPICS_PATH` and filesystem functions. Assert a required `Topics` check is true for a regular file, false for missing/invalid paths, and that messages are generic (`"Topic file is available"` / `"Topic file was not found"`). Ensure explicit invalid paths do not fall back to local inline topics.

- [ ] **Step 2: Run doctor tests and verify RED**

Expected: no `Topics` check is returned.

- [ ] **Step 3: Implement the read-only Topics check**

Import `resolve_topics_path` and `load_topic_settings`, then insert this block after the Git check:

```python
    topics_path: Path | None = None
    topics_ok = True
    if "PAPERFLOW_TOPICS_PATH" in env:
        try:
            topics_path = resolve_topics_path(env)
            topics_ok = (
                topics_path is not None
                and path_exists(topics_path)
                and path_is_file(topics_path)
            )
            if topics_ok:
                load_topic_settings(topics_path)
        except ConfigError:
            topics_ok = False
        checks.append(
            Check(
                "Topics",
                topics_ok,
                True,
                "Topic file is available" if topics_ok else "Topic file was not found",
            )
        )
```

Change the configuration load to:

```python
                loaded_config = load_local_config(
                    actual_config_path,
                    topics_path=topics_path,
                )
```

Never mutate or echo the supplied topic path.

- [ ] **Step 4: Run doctor tests and verify GREEN**

Run `pytest tests/test_doctor.py -q`.

- [ ] **Step 5: Write failing installer wrapper contract tests**

Extend wrapper assertions to require exactly:

```text
set "PAPERFLOW_TOPICS_PATH=<ProjectRoot>\config\topics.toml"
```

Assert `-CheckOnly` still creates nothing, the topic file must exist before persistent writes, reinstall preserves local config bytes, and the installer does not collect `PAPERFLOW_MAIL_TO` or Gmail Secrets.

- [ ] **Step 6: Run focused installer tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_installer_contract.py -k "wrapper or topics or secret" -v
```

Expected: wrapper contract fails because the variable is absent.

- [ ] **Step 7: Add the wrapper variable and preflight validation**

Define near the other resolved paths:

```powershell
$TopicsPath = Join-Path $ProjectRoot 'config\topics.toml'
if (-not (Test-Path -LiteralPath $TopicsPath -PathType Leaf)) {
    throw 'PaperFlow topic file was not found.'
}
$topicsItem = Get-Item -LiteralPath $TopicsPath -Force
if (($topicsItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'PaperFlow topic file must not be a reparse point.'
}
```

Before constructing the D-root wrapper, add:

```powershell
$wrapperTopics = ConvertTo-CmdEmbeddedPath -Path $TopicsPath
```

Generate the wrapper with this exact environment block:

```powershell
set "PAPERFLOW_HOME=$wrapperHome"
set "PAPERFLOW_TOPICS_PATH=$wrapperTopics"
set "PAPERFLOW_CACHE_DIR=$wrapperCache"
set "TMP=$wrapperTemp"
set "TEMP=$wrapperTemp"
```

- [ ] **Step 8: Run doctor/installer tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_doctor.py tests\test_installer_contract.py -q
git add src/paperflow/doctor.py scripts/install-windows.ps1 tests/test_doctor.py tests/test_installer_contract.py
git commit -m "feat: install and diagnose shared topic config"
```

### Task 6: Workflow, Codex Skill, and Documentation

**Files:**
- Modify: `.github/workflows/daily.yml`
- Modify: `.agents/skills/paperflow/SKILL.md`
- Modify: `README.md`
- Modify: `config.example.toml`
- Modify: `tests/test_workflow.py`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_installer_contract.py`

- [ ] **Step 1: Write failing workflow contract tests**

Require the workflow to set `PAPERFLOW_TOPICS_PATH: ${{ github.workspace }}/config/topics.toml`, use `PAPERFLOW_MAIL_TO`, retain the two Gmail Secrets, and contain no `PAPERFLOW_PRIVATE_CONFIG_JSON`. Preserve pinned actions, `contents: read`, no artifacts, and the UTC 00:00 schedule.

- [ ] **Step 2: Run workflow tests and verify RED**

Expected: missing topic path/mail recipient and forbidden private JSON assertions fail.

- [ ] **Step 3: Update the workflow minimally**

Change only the environment block under the daily command:

```yaml
        env:
          PAPERFLOW_TOPICS_PATH: ${{ github.workspace }}/config/topics.toml
          PAPERFLOW_GMAIL_ADDRESS: ${{ secrets.PAPERFLOW_GMAIL_ADDRESS }}
          PAPERFLOW_GMAIL_APP_PASSWORD: ${{ secrets.PAPERFLOW_GMAIL_APP_PASSWORD }}
          PAPERFLOW_MAIL_TO: ${{ secrets.PAPERFLOW_MAIL_TO }}
```

- [ ] **Step 4: Run workflow tests and verify GREEN**

Run `pytest tests/test_workflow.py -q`.

- [ ] **Step 5: Write failing Skill contract tests**

Require real JSON commands for filtered search and `watch list/add/remove`. Require the Skill to:

- use one to three searches for complex questions;
- merge by arXiv ID;
- distinguish one-off search from persistent watch changes;
- ask explicit approval before watch mutations, note writes, overwrite, commit, or push;
- limit full-text claims to an available PDF-reading capability and otherwise use title/abstract only;
- retain all Zotero/Sidebar safety rules and stay below 650 words.

- [ ] **Step 6: Run Skill tests and verify RED**

Expected: missing watch orchestration and search-planning phrases.

- [ ] **Step 7: Rewrite the concise Skill contract**

Replace `.agents/skills/paperflow/SKILL.md` with this implemented-command contract:

```markdown
---
name: paperflow
description: Use when the user asks for today's papers, paper search, watched research topics, an Obsidian paper note, or PaperFlow diagnostics.
---

# PaperFlow

Use PaperFlow as the single entry point and always request JSON. Parse only returned fields.

## Quick Reference

- Daily: `paperflow --json daily` atomically writes or updates today's Obsidian report. Report `report_path`. Only `--no-write` prevents the write. Cloud email is `paperflow --json daily --email --no-write`.
- Search: `paperflow --json search "<user query>"`. Optional implemented filters are `--category <arxiv-category>` (repeatable), `--since <YYYY-MM-DD|Nd>`, `--limit <1-100>`, `--sort <relevance|newest>`, and `--history-only`. Show both `history` and `online`; online results are not saved.
- Watch: `paperflow --json watch list` is read-only. Before `paperflow --json watch add "<topic>" --weight <1-100>` or `paperflow --json watch remove "<topic>"`, show the proposed change and wait for explicit approval.
- Note: first show the selected paper and proposed `PaperFlow/Papers/<id>.md`, then wait for explicit save approval before `paperflow --json note <arxiv-id>`. Use `--force` only after separate replacement approval.
- Diagnostics: `paperflow --json doctor` is read-only. Explain required versus optional checks.

## Codex Research Flow

- Treat one-off search and watched topics as different intents. Never add a search query to the watchlist without approval.
- For a complex question, run one to three bounded searches, merge online results by `arxiv_id`, and explain why the strongest candidates match. Do not invent fields absent from JSON.
- Commit or push a topic-file change only when the user explicitly asks for Git synchronization.
- For full-text analysis, use an available PDF-reading capability. If none is available, state that analysis is limited to returned titles and abstracts.

## Safety and Exit Handling

- Exit 0 is success; 1 is a required doctor failure; 2 is configuration/input failure; 3 is source/arXiv failure; 4 means a note exists; 5 is email delivery failure. `partial=true` is valid; identify failed sources.
- Never print or read Gmail App Passwords or Sidebar API keys. Never write `zotero.sqlite`, YOLO, auto-configure WebDAV, or automatically write Zotero; suggest Zotero Connector.
- Never overwrite a note, mutate watch topics, commit, or push without the approval required above.
- If JSON parsing fails or the command is unavailable, report the real error and do not guess the schema.
```

- [ ] **Step 8: Run Skill tests and verify GREEN**

Run `pytest tests/test_skill_contract.py -q`.

- [ ] **Step 9: Write failing README/config documentation tests**

Replace the old three-Secret JSON example assertions with executable examples for:

- local `config.toml` containing only the Vault path;
- public `config/topics.toml` and its privacy warning;
- active filtered search;
- watch list/add/remove;
- local daily report;
- the three mail-only Secrets;
- manual workflow dispatch;
- legacy inline/private JSON compatibility and deprecation wording.

- [ ] **Step 10: Run documentation tests and verify RED**

Expected: old private JSON documentation conflicts with new assertions.

- [ ] **Step 11: Update README and `config.example.toml`**

Keep installation, DataRoot safety, privacy, upgrade, and uninstall sections intact. Make the user path explicit:

```text
one-off search -> shortlist -> optional note
                         -> optional watch add -> future local/cloud daily
```

State that provider addresses are not user configuration and that public topic files expose research interests.

Replace `config.example.toml` with the local-only executable example:

```toml
vault_path = "D:\\ObsidianVault"
```

In README, use exactly these command examples:

```powershell
paperflow --json search "vision language action" --category cs.RO --since 30d --limit 20 --sort newest
paperflow --json watch list
paperflow --json watch add "vision language action" --weight 8
paperflow --json watch remove "robotics"
paperflow --json daily
paperflow --json note 2401.01234
paperflow --json doctor
```

Replace the cloud JSON example with the three Secret names `PAPERFLOW_GMAIL_ADDRESS`, `PAPERFLOW_GMAIL_APP_PASSWORD`, and `PAPERFLOW_MAIL_TO`. Retain a compatibility paragraph stating that old inline local topic fields and `PAPERFLOW_PRIVATE_CONFIG_JSON` are accepted but no longer used by the bundled workflow.

- [ ] **Step 12: Run workflow/Skill/docs tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_workflow.py tests\test_skill_contract.py tests\test_installer_contract.py -q
git add .github/workflows/daily.yml .agents/skills/paperflow/SKILL.md README.md config.example.toml tests/test_workflow.py tests/test_skill_contract.py tests/test_installer_contract.py
git commit -m "docs: connect Codex search watch and cloud workflow"
```

### Task 7: Full Verification, Local Migration, and Remote Deployment

**Files:**
- Verify all changed files
- Update only if verification exposes a tested defect

- [ ] **Step 1: Run the full offline suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Expected: zero failures and zero errors. Record the exact passed count.

- [ ] **Step 2: Run static repository checks**

```powershell
git diff --check
git status --short
git grep -n -I -E "gmail|app-password|PAPERFLOW_(PRIVATE_CONFIG_JSON|GMAIL_APP_PASSWORD)"
```

Expected: no whitespace errors, only intended changes, and no real credentials.

- [ ] **Step 3: Re-run the Windows installer check-only path**

From the final branch, run the installer with `-CheckOnly -DataRoot "D:\PaperFlowData"` and the existing Vault. Expected: exit 0, no DataRoot/source/PATH mutations.

- [ ] **Step 4: Fast-forward the canonical `D:\PaperFlow` only after checks pass**

Verify it is clean and still points to `miaobeihai-ops/PaperFlow`. Fetch the feature branch by local path and use `git merge --ff-only FETCH_HEAD`. Do not set global `safe.directory`.

- [ ] **Step 5: Re-run the installer against the existing D-root**

Approve exact PATH preservation/replacement when prompted. Verify the wrapper now sets `PAPERFLOW_TOPICS_PATH`, local config bytes and Vault stay unchanged, and all required doctor checks pass.

- [ ] **Step 6: Run live, non-writing smoke tests**

```powershell
paperflow --json watch list
paperflow --json search "vision language action" --category cs.RO --since 30d --limit 5 --sort newest
paperflow --json doctor
```

Expected: valid JSON; watch list reflects `config/topics.toml`; search returns `history`, `online`, and normalized `filters`; doctor required checks pass; no Vault files are added by search/list/doctor.

- [ ] **Step 7: Verify topic mutation round-trip without changing final defaults**

Copy `config/topics.toml` to an isolated temporary directory, point `PAPERFLOW_TOPICS_PATH` there, run watch add/list/remove, and byte-compare the final rendered settings with the expected defaults. Never mutate the canonical topic file for this smoke.

- [ ] **Step 8: Push the verified main branch**

Push without force to `https://github.com/miaobeihai-ops/PaperFlow.git`, then verify `git ls-remote` main equals local HEAD and both workflows remain active.

- [ ] **Step 9: Stop before mail Secret configuration**

Report that the workflow is deployed but cannot send email until the user separately authorizes storing the three mail Secrets. Do not request, read, or set Gmail credentials as part of source deployment.
