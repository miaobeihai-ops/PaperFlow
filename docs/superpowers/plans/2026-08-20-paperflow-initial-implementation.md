# PaperFlow Initial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved database-free PaperFlow MVP that sends a deterministic daily paper email from GitHub Actions and lets Codex generate/search Markdown reports and paper notes on Windows.

**Architecture:** A single Python 3.11 package owns source fetching, normalization, deterministic ranking, report rendering, SMTP delivery, and Obsidian writes. GitHub Actions and a local Codex Skill call the same CLI; all durable user data remains Markdown in Obsidian, while Zotero collection stays manual through Zotero Connector.

**Tech Stack:** Python 3.11, standard-library `argparse`/`dataclasses`/`tomllib`/`smtplib`, `httpx`, `pytest`, PowerShell 5.1+, GitHub Actions, Gmail SMTP.

---

## File map

```text
paperflow/
├─ pyproject.toml
├─ .gitignore
├─ config.example.toml
├─ src/paperflow/
│  ├─ __init__.py        # version
│  ├─ cli.py             # public command surface and JSON output
│  ├─ config.py          # local TOML and cloud JSON configuration
│  ├─ models.py          # immutable paper/source/report values
│  ├─ normalize.py       # arXiv ID canonicalization and deduplication
│  ├─ rank.py            # deterministic scoring and stable ordering
│  ├─ fetch.py           # retries and source orchestration
│  ├─ hf_source.py       # Hugging Face API parsing
│  ├─ arxiv_source.py    # arXiv Atom parsing and queries
│  ├─ report.py          # Markdown, text email, HTML email
│  ├─ obsidian.py        # history scan and atomic Markdown writes
│  ├─ daily.py           # daily orchestration
│  ├─ search.py          # online and local history search
│  ├─ notes.py           # single-paper note creation
│  ├─ email.py           # Gmail SMTP adapter
│  └─ doctor.py          # read-only installation checks
├─ codex/skills/paperflow/SKILL.md
├─ scripts/install-windows.ps1
├─ .github/workflows/ci.yml
├─ .github/workflows/daily.yml
├─ tests/fixtures/hf_daily.json
├─ tests/fixtures/arxiv_feed.xml
├─ tests/test_cli.py
├─ tests/test_config.py
├─ tests/test_rank.py
├─ tests/test_sources.py
├─ tests/test_report.py
├─ tests/test_daily.py
├─ tests/test_search_notes.py
├─ tests/test_email.py
├─ tests/test_doctor.py
├─ tests/test_installer_contract.py
├─ README.md
├─ NOTICE
└─ LICENSE
```

### Task 1: Bootstrap the Python package and CLI

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/paperflow/__init__.py`
- Create: `src/paperflow/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI tests**

```python
# tests/test_cli.py
import json

from paperflow.cli import main


def test_version_text(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "paperflow 0.1.0"


def test_version_json(capsys):
    assert main(["--json", "--version"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "version": "0.1.0",
    }
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `py -3.11 -m pytest tests/test_cli.py -v`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'paperflow'`.

- [ ] **Step 3: Add the minimal package and entry point**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=75,<76"]
build-backend = "setuptools.build_meta"

[project]
name = "paperflow"
version = "0.1.0"
description = "Database-free daily paper discovery for Codex, Zotero, and Obsidian"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27,<0.29"]

[project.optional-dependencies]
dev = ["pytest>=8.3,<9"]

[project.scripts]
paperflow = "paperflow.cli:console_main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
htmlcov/
build/
dist/
*.egg-info/
config.toml
*.log
*.tmp
```

```python
# src/paperflow/__init__.py
__version__ = "0.1.0"
```

```python
# src/paperflow/cli.py
from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from paperflow import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperflow")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--version", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        if args.json_output:
            print(json.dumps({"ok": True, "version": __version__}))
        else:
            print(f"paperflow {__version__}")
        return 0
    build_parser().print_help()
    return 0


def console_main() -> None:
    raise SystemExit(main())
```

- [ ] **Step 4: Install development dependencies and verify GREEN**

Run: `py -3.11 -m venv .venv`

Expected: `.venv\Scripts\python.exe` exists.

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`

Expected: exit code 0 and `paperflow-0.1.0` installed.

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`

Expected: `2 passed`.

- [ ] **Step 5: Commit the bootstrap**

```powershell
git add pyproject.toml .gitignore src/paperflow/__init__.py src/paperflow/cli.py tests/test_cli.py
git commit -m "build: bootstrap PaperFlow CLI"
```

### Task 2: Load and validate local/cloud configuration

**Files:**
- Create: `src/paperflow/config.py`
- Create: `config.example.toml`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write configuration tests**

```python
# tests/test_config.py
import json

import pytest

from paperflow.config import ConfigError, load_cloud_config, load_local_config


def test_load_local_config(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    path = tmp_path / "config.toml"
    path.write_text(
        f'''vault_path = "{vault.as_posix()}"
top_n = 10
timezone = "Asia/Hong_Kong"
history_reports = 30
arxiv_categories = ["cs.AI", "cs.CV"]

[keywords]
robotics = 5
"3d reconstruction" = 8
''',
        encoding="utf-8",
    )
    config = load_local_config(path)
    assert config.vault_path == vault
    assert config.keywords["3d reconstruction"] == 8
    assert config.top_n == 10


def test_load_cloud_config_from_private_json():
    raw = json.dumps(
        {
            "mail_to": "reader@example.com",
            "keywords": {"robotics": 5},
            "arxiv_categories": ["cs.RO"],
            "timezone": "Asia/Hong_Kong",
            "top_n": 10,
        }
    )
    config = load_cloud_config(raw)
    assert config.mail_to == "reader@example.com"
    assert config.vault_path is None


def test_rejects_empty_keywords(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('vault_path = "C:/Vault"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="keywords"):
        load_local_config(path)
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'paperflow.config'`.

- [ ] **Step 3: Implement the concrete configuration contract**

```python
# src/paperflow/config.py
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
```

```toml
# config.example.toml
vault_path = "C:/Users/YourName/Documents/Obsidian Vault"
top_n = 10
timezone = "Asia/Hong_Kong"
history_reports = 30
arxiv_categories = ["cs.RO", "cs.CV", "cs.AI", "cs.LG"]

[keywords]
robotics = 5
"3d reconstruction" = 8
```

- [ ] **Step 4: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py -v`

Expected: `3 passed`.

- [ ] **Step 5: Commit configuration**

```powershell
git add src/paperflow/config.py config.example.toml tests/test_config.py
git commit -m "feat: add private local and cloud configuration"
```

### Task 3: Normalize, deduplicate, score, and sort papers

**Files:**
- Create: `src/paperflow/models.py`
- Create: `src/paperflow/normalize.py`
- Create: `src/paperflow/rank.py`
- Create: `tests/test_rank.py`

- [ ] **Step 1: Write deterministic ranking tests**

```python
# tests/test_rank.py
from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id, deduplicate
from paperflow.rank import rank_papers


def paper(arxiv_id: str, title: str, abstract: str, *, upvotes: int = 0) -> Paper:
    return Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=("Ada Researcher",),
        abstract=abstract,
        primary_category="cs.RO",
        published="2026-08-20",
        sources=("arxiv",),
        hf_upvotes=upvotes,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def test_canonical_arxiv_id_removes_url_and_version():
    assert canonical_arxiv_id("https://arxiv.org/abs/2608.12345v2") == "2608.12345"


def test_deduplicate_merges_sources_and_keeps_upvotes():
    first = paper("2608.12345", "Robot", "A", upvotes=0)
    second = paper("2608.12345v2", "Robot", "A", upvotes=12)
    second = second.with_sources(("hf-daily",))
    result = deduplicate([first, second])
    assert len(result) == 1
    assert result[0].sources == ("arxiv", "hf-daily")
    assert result[0].hf_upvotes == 12


def test_title_match_beats_popularity():
    exact = paper("2608.00001", "3D reconstruction for robots", "method")
    popular = paper("2608.00002", "General vision", "robotics", upvotes=200)
    ranked = rank_papers(
        [popular, exact],
        keywords={"3d reconstruction": 8, "robotics": 1},
        categories=("cs.RO",),
    )
    assert ranked[0].paper.arxiv_id == "2608.00001"
    assert ranked[0].matched_keywords == ("3d reconstruction",)
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rank.py -v`

Expected: FAIL because `paperflow.models` does not exist.

- [ ] **Step 3: Implement immutable values and exact scoring**

```python
# src/paperflow/models.py
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    primary_category: str
    published: str
    sources: tuple[str, ...]
    hf_upvotes: int
    url: str
    pdf_url: str

    def with_sources(self, sources: tuple[str, ...]) -> "Paper":
        return replace(self, sources=sources)


@dataclass(frozen=True)
class RankedPaper:
    paper: Paper
    score: int
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class SourceFailure:
    source: str
    message: str
```

```python
# src/paperflow/normalize.py
from __future__ import annotations

import re
from dataclasses import replace

from paperflow.models import Paper

ARXIV_RE = re.compile(r"(?:(?:arxiv\.org)/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def canonical_arxiv_id(value: str) -> str:
    match = ARXIV_RE.search(value)
    if not match:
        raise ValueError(f"invalid arXiv identifier: {value}")
    return match.group(1)


def deduplicate(papers: list[Paper]) -> list[Paper]:
    merged: dict[str, Paper] = {}
    for paper in papers:
        arxiv_id = canonical_arxiv_id(paper.arxiv_id)
        current = merged.get(arxiv_id)
        normalized = replace(paper, arxiv_id=arxiv_id)
        if current is None:
            merged[arxiv_id] = normalized
            continue
        sources = tuple(sorted(set(current.sources) | set(normalized.sources)))
        preferred = normalized if normalized.hf_upvotes > current.hf_upvotes else current
        merged[arxiv_id] = replace(
            preferred,
            arxiv_id=arxiv_id,
            sources=sources,
            hf_upvotes=max(current.hf_upvotes, normalized.hf_upvotes),
        )
    return list(merged.values())
```

```python
# src/paperflow/rank.py
from __future__ import annotations

import math

from paperflow.models import Paper, RankedPaper


def rank_papers(
    papers: list[Paper],
    *,
    keywords: dict[str, int],
    categories: tuple[str, ...],
) -> list[RankedPaper]:
    ranked: list[RankedPaper] = []
    for paper in papers:
        title = paper.title.casefold()
        abstract = paper.abstract.casefold()
        matched: list[str] = []
        score = 0
        for keyword, weight in keywords.items():
            key = keyword.casefold()
            if key in title:
                score += weight * 10
                matched.append(key)
            elif key in abstract:
                score += weight * 3
                matched.append(key)
        if paper.primary_category in categories:
            score += 5
        score += min(5, int(math.log2(max(0, paper.hf_upvotes) + 1)))
        ranked.append(RankedPaper(paper, score, tuple(sorted(set(matched)))))
    return sorted(
        ranked,
        key=lambda item: (
            -item.score,
            item.paper.published,
            -item.paper.hf_upvotes,
            item.paper.arxiv_id,
        ),
    )
```

- [ ] **Step 4: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rank.py -v`

Expected: `3 passed`.

- [ ] **Step 5: Commit ranking core**

```powershell
git add src/paperflow/models.py src/paperflow/normalize.py src/paperflow/rank.py tests/test_rank.py
git commit -m "feat: add deterministic paper ranking"
```

### Task 4: Fetch Hugging Face and arXiv with bounded retries

**Files:**
- Create: `src/paperflow/hf_source.py`
- Create: `src/paperflow/arxiv_source.py`
- Create: `src/paperflow/fetch.py`
- Create: `tests/fixtures/hf_daily.json`
- Create: `tests/fixtures/arxiv_feed.xml`
- Create: `tests/test_sources.py`

- [ ] **Step 1: Add small source fixtures and parser tests**

```json
[
  {
    "paper": {
      "id": "2608.12345",
      "title": "Robotic 3D Reconstruction",
      "summary": "A reconstruction method for mobile robots.",
      "authors": [{"name": "Ada Researcher"}],
      "upvotes": 12,
      "publishedAt": "2026-08-20T01:00:00.000Z"
    }
  }
]
```

```xml
<!-- tests/fixtures/arxiv_feed.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2608.12345v2</id>
    <updated>2026-08-20T02:00:00Z</updated>
    <published>2026-08-20T02:00:00Z</published>
    <title>Robotic 3D Reconstruction</title>
    <summary>A reconstruction method for mobile robots.</summary>
    <author><name>Ada Researcher</name></author>
    <arxiv:primary_category term="cs.RO" />
  </entry>
</feed>
```

```python
# tests/test_sources.py
from pathlib import Path

import httpx

from paperflow.arxiv_source import parse_arxiv_feed
from paperflow.fetch import request_with_retry
from paperflow.hf_source import parse_hf_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_hf_daily_fixture():
    papers = parse_hf_payload((FIXTURES / "hf_daily.json").read_text(encoding="utf-8"), "hf-daily")
    assert papers[0].arxiv_id == "2608.12345"
    assert papers[0].hf_upvotes == 12


def test_parse_arxiv_fixture():
    papers = parse_arxiv_feed((FIXTURES / "arxiv_feed.xml").read_text(encoding="utf-8"))
    assert papers[0].arxiv_id == "2608.12345"
    assert papers[0].primary_category == "cs.RO"


def test_request_retries_429_then_succeeds(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(request.url)
        return httpx.Response(429 if len(attempts) < 3 else 200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("paperflow.fetch.time.sleep", lambda _: None)
    response = request_with_retry(client, "https://example.test", attempts=3)
    assert response.text == "ok"
    assert len(attempts) == 3
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sources.py -v`

Expected: FAIL because the source modules do not exist.

- [ ] **Step 3: Implement source parsing and retry policy**

Implement this exact retry primitive:

```python
# src/paperflow/fetch.py
from __future__ import annotations

import time

import httpx


def request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    attempts: int = 3,
    timeout: float = 30.0,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.get(url, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPStatusError("recoverable response", request=response.request, response=response)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error
```

Add the Hugging Face parser and two one-request fetch functions:

```python
# src/paperflow/hf_source.py
from __future__ import annotations

import json
from datetime import date

import httpx

from paperflow.fetch import request_with_retry
from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def parse_hf_payload(payload: str, source: str) -> list[Paper]:
    result: list[Paper] = []
    for item in json.loads(payload):
        data = item.get("paper", item)
        try:
            arxiv_id = canonical_arxiv_id(str(data.get("id", "")))
        except ValueError:
            continue
        authors = tuple(_clean(author.get("name")) for author in data.get("authors", []) if author.get("name"))
        published = _clean(data.get("publishedAt"))[:10]
        result.append(
            Paper(
                arxiv_id=arxiv_id,
                title=_clean(data.get("title")),
                authors=authors,
                abstract=_clean(data.get("summary")),
                primary_category=_clean(data.get("primaryCategory")),
                published=published,
                sources=(source,),
                hf_upvotes=int(data.get("upvotes", item.get("upvotes", 0)) or 0),
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            )
        )
    return result


def fetch_hf_daily(client: httpx.Client, target_date: date) -> list[Paper]:
    url = f"https://huggingface.co/api/daily_papers?date={target_date.isoformat()}&limit=100"
    return parse_hf_payload(request_with_retry(client, url).text, "hf-daily")


def fetch_hf_trending(client: httpx.Client, target_date: date) -> list[Paper]:
    del target_date
    url = "https://huggingface.co/api/daily_papers?sort=trending&limit=50"
    return parse_hf_payload(request_with_retry(client, url).text, "hf-trending")
```

Add the arXiv parser and one batched category request:

```python
# src/paperflow/arxiv_source.py
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from paperflow.fetch import request_with_retry
from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id

ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"


def _text(node: ET.Element, name: str) -> str:
    child = node.find(f"{{{ATOM}}}{name}")
    return " ".join((child.text if child is not None else "").split())


def parse_arxiv_feed(xml: str) -> list[Paper]:
    result: list[Paper] = []
    for entry in ET.fromstring(xml).findall(f"{{{ATOM}}}entry"):
        arxiv_id = canonical_arxiv_id(_text(entry, "id"))
        category = entry.find(f"{{{ARXIV}}}primary_category")
        authors = tuple(_text(author, "name") for author in entry.findall(f"{{{ATOM}}}author"))
        result.append(
            Paper(
                arxiv_id=arxiv_id,
                title=_text(entry, "title"),
                authors=authors,
                abstract=_text(entry, "summary"),
                primary_category=category.attrib.get("term", "") if category is not None else "",
                published=_text(entry, "published")[:10],
                sources=("arxiv",),
                hf_upvotes=0,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            )
        )
    return result


def fetch_arxiv(client: httpx.Client, target_date: date, categories: tuple[str, ...]) -> list[Paper]:
    del target_date
    query = "+OR+".join(f"cat:{category}" for category in categories)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": query, "start": 0, "max_results": 100, "sortBy": "submittedDate", "sortOrder": "descending"}
    )
    return parse_arxiv_feed(request_with_retry(client, url).text)
```

Do not add asynchronous calls or per-paper enrichment requests.

- [ ] **Step 4: Verify GREEN and regression suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sources.py tests/test_rank.py -v`

Expected: `6 passed`.

- [ ] **Step 5: Commit source clients**

```powershell
git add src/paperflow/fetch.py src/paperflow/hf_source.py src/paperflow/arxiv_source.py tests/fixtures tests/test_sources.py
git commit -m "feat: fetch Hugging Face and arXiv papers"
```

### Task 5: Render and atomically store Markdown reports

**Files:**
- Create: `src/paperflow/report.py`
- Create: `src/paperflow/obsidian.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Write report and atomic-write tests**

```python
# tests/test_report.py
from pathlib import Path

from paperflow.models import Paper, RankedPaper, SourceFailure
from paperflow.obsidian import recent_arxiv_ids, write_daily_report
from paperflow.report import render_daily_markdown, render_email_html


def ranked() -> RankedPaper:
    paper = Paper(
        arxiv_id="2608.12345",
        title="Robots < Vision",
        authors=("Ada Researcher",),
        abstract="A" * 600,
        primary_category="cs.RO",
        published="2026-08-20",
        sources=("arxiv", "hf-daily"),
        hf_upvotes=12,
        url="https://arxiv.org/abs/2608.12345",
        pdf_url="https://arxiv.org/pdf/2608.12345",
    )
    return RankedPaper(paper, 85, ("robotics",))


def test_markdown_has_stable_fields():
    text = render_daily_markdown("2026-08-20", [ranked()], [])
    assert "partial: false" in text
    assert "- arxiv_id: `2608.12345`" in text
    assert "- matched: `robotics`" in text


def test_email_escapes_html():
    html = render_email_html("2026-08-20", [ranked()], [SourceFailure("hf-trending", "timeout")])
    assert "Robots &lt; Vision" in html
    assert "hf-trending" in html


def test_write_and_scan_recent_reports(tmp_path):
    target = write_daily_report(tmp_path, "2026-08-20", "- arxiv_id: `2608.12345`\n")
    assert target == tmp_path / "PaperFlow" / "Reports" / "2026-08-20.md"
    assert recent_arxiv_ids(tmp_path, limit=30) == {"2608.12345"}
    assert not list(target.parent.glob("*.tmp"))
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_report.py -v`

Expected: FAIL because report modules do not exist.

- [ ] **Step 3: Implement stable rendering and atomic replacement**

`render_daily_markdown` must emit YAML fields `date`, `generated_at`, `paperflow_version`, `partial`, and `sources`, followed by numbered paper sections. Each section uses the exact labels tested above and truncates the abstract to 500 characters plus `…` only when necessary. `render_email_html` must use `html.escape` for all external text and the same paper order.

Implement atomic writes with this contract:

```python
# src/paperflow/obsidian.py
from __future__ import annotations

import os
import re
from pathlib import Path

ARXIV_FIELD = re.compile(r"^- arxiv_id: `(\d{4}\.\d{4,5})`$", re.MULTILINE)


def reports_dir(vault_path: Path) -> Path:
    return vault_path / "PaperFlow" / "Reports"


def write_daily_report(vault_path: Path, date: str, content: str) -> Path:
    directory = reports_dir(vault_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{date}.md"
    temporary = directory / f".{date}.md.tmp"
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return target


def recent_arxiv_ids(vault_path: Path, *, limit: int) -> set[str]:
    paths = sorted(reports_dir(vault_path).glob("????-??-??.md"), reverse=True)[:limit]
    result: set[str] = set()
    for path in paths:
        result.update(ARXIV_FIELD.findall(path.read_text(encoding="utf-8")))
    return result
```

- [ ] **Step 4: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_report.py -v`

Expected: `3 passed`.

- [ ] **Step 5: Commit report storage**

```powershell
git add src/paperflow/report.py src/paperflow/obsidian.py tests/test_report.py
git commit -m "feat: render database-free Markdown reports"
```

### Task 6: Orchestrate daily discovery and expose the `daily` command

**Files:**
- Create: `src/paperflow/daily.py`
- Modify: `src/paperflow/cli.py`
- Create: `tests/test_daily.py`

- [ ] **Step 1: Write partial-success and all-failed tests**

```python
# tests/test_daily.py
import pytest

from paperflow.daily import AllSourcesFailed, run_daily
from paperflow.models import Paper


def sample() -> Paper:
    return Paper("2608.12345", "Robot", ("Ada",), "robotics", "cs.RO", "2026-08-20", ("arxiv",), 0, "u", "p")


def test_daily_keeps_partial_results(config, monkeypatch):
    monkeypatch.setattr("paperflow.daily.fetch_hf_daily", lambda *_: [sample()])
    monkeypatch.setattr("paperflow.daily.fetch_hf_trending", lambda *_: (_ for _ in ()).throw(TimeoutError("slow")))
    monkeypatch.setattr("paperflow.daily.fetch_arxiv", lambda *_: [sample()])
    result = run_daily(config, "2026-08-20", write_report=False)
    assert len(result.papers) == 1
    assert result.failures[0].source == "hf-trending"


def test_daily_raises_when_every_source_fails(config, monkeypatch):
    for name in ("fetch_hf_daily", "fetch_hf_trending", "fetch_arxiv"):
        monkeypatch.setattr(f"paperflow.daily.{name}", lambda *_: (_ for _ in ()).throw(TimeoutError("down")))
    with pytest.raises(AllSourcesFailed):
        run_daily(config, "2026-08-20", write_report=False)
```

Create the shared fixture:

```python
# tests/conftest.py
import pytest

from paperflow.config import PaperFlowConfig


@pytest.fixture
def config(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    return PaperFlowConfig(
        keywords={"robotics": 5},
        arxiv_categories=("cs.RO",),
        timezone="Asia/Hong_Kong",
        top_n=10,
        history_reports=30,
        vault_path=vault,
    )
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_daily.py -v`

Expected: FAIL because `paperflow.daily` does not exist.

- [ ] **Step 3: Implement orchestration without persistence**

Create `DailyResult(date: str, papers: tuple[RankedPaper, ...], failures: tuple[SourceFailure, ...], report_path: Path | None)` in `models.py`. `run_daily` must call each source exactly once, capture exceptions as `SourceFailure`, raise `AllSourcesFailed` only when no source succeeds, deduplicate, exclude IDs from `recent_arxiv_ids` only for local writes, rank, slice `config.top_n`, render, and optionally write the report.

Extend `cli.py` with subparser `daily`, options `--date YYYY-MM-DD`, `--no-write`, and the existing global `--json`. Load local config unless `PAPERFLOW_PRIVATE_CONFIG_JSON` exists. JSON output must contain `ok`, `date`, `partial`, `papers`, `failures`, and `report_path`. Return exit code 2 for configuration errors and 3 when all sources fail.

- [ ] **Step 4: Verify GREEN and full suite**

Run: `.venv\Scripts\python.exe -m pytest -v`

Expected: all tests pass.

- [ ] **Step 5: Commit daily command**

```powershell
git add src/paperflow/models.py src/paperflow/daily.py src/paperflow/cli.py tests/conftest.py tests/test_daily.py tests/test_cli.py
git commit -m "feat: add daily discovery command"
```

### Task 7: Add online/history search and safe paper-note creation

**Files:**
- Create: `src/paperflow/search.py`
- Create: `src/paperflow/notes.py`
- Modify: `src/paperflow/arxiv_source.py`
- Modify: `src/paperflow/cli.py`
- Create: `tests/test_search_notes.py`

- [ ] **Step 1: Write search and no-overwrite note tests**

```python
# tests/test_search_notes.py
import pytest

from paperflow.models import Paper
from paperflow.notes import NoteExists, write_paper_note
from paperflow.search import search_history


def test_search_history_finds_title(tmp_path):
    reports = tmp_path / "PaperFlow" / "Reports"
    reports.mkdir(parents=True)
    (reports / "2026-08-20.md").write_text(
        "## 1. Robotic 3D Reconstruction\n- arxiv_id: `2608.12345`\n",
        encoding="utf-8",
    )
    assert search_history(tmp_path, "3d reconstruction")[0]["arxiv_id"] == "2608.12345"


def test_note_refuses_silent_overwrite(tmp_path):
    paper = Paper("2608.12345", "Robot", ("Ada",), "Abstract", "cs.RO", "2026-08-20", ("arxiv",), 0, "u", "p")
    path = write_paper_note(tmp_path, paper, force=False)
    assert path.name == "2608.12345.md"
    with pytest.raises(NoteExists):
        write_paper_note(tmp_path, paper, force=False)
    write_paper_note(tmp_path, paper, force=True)
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_search_notes.py -v`

Expected: FAIL because search/note modules do not exist.

- [ ] **Step 3: Implement bounded search and notes**

`search_history(vault_path, query)` scans only `PaperFlow/Reports/*.md`, case-folds the query, and returns dictionaries with `title`, `arxiv_id`, and `path`. Add `search_arxiv(client, query, max_results=20)` using one escaped arXiv `search_query=all:` request. `write_paper_note` writes `<Vault>/PaperFlow/Papers/<arxiv_id>.md` atomically with frontmatter fields `arxiv_id`, `title`, `authors`, `source_url`, `pdf_url`, `status: unread`, and `created`; it raises `NoteExists` before writing unless `force=True`.

Add CLI subcommands:

```text
paperflow search "3d reconstruction" [--history-only] [--json]
paperflow note 2608.12345 [--force] [--json]
```

`search` returns local matches plus online arXiv matches unless `--history-only`. `note` performs one arXiv ID request, then writes the note. Neither command modifies Zotero.

- [ ] **Step 4: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_search_notes.py tests/test_cli.py -v`

Expected: all focused tests pass.

- [ ] **Step 5: Commit search and notes**

```powershell
git add src/paperflow/search.py src/paperflow/notes.py src/paperflow/arxiv_source.py src/paperflow/cli.py tests/test_search_notes.py tests/test_cli.py
git commit -m "feat: add search and Obsidian paper notes"
```

### Task 8: Send Gmail reports and schedule GitHub Actions

**Files:**
- Create: `src/paperflow/email.py`
- Modify: `src/paperflow/cli.py`
- Create: `tests/test_email.py`
- Create: `.github/workflows/daily.yml`

- [ ] **Step 1: Write SMTP tests with no network**

```python
# tests/test_email.py
from paperflow.email import GmailSettings, send_daily_email


def test_email_uses_tls_login_and_plain_html(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def starttls(self):
            calls.append(("starttls",))
        def login(self, user, password):
            calls.append(("login", user, password))
        def send_message(self, message):
            calls.append(("send", message["To"], len(message.get_payload())))

    monkeypatch.setattr("paperflow.email.smtplib.SMTP", FakeSMTP)
    settings = GmailSettings("sender@gmail.com", "app-password", "reader@example.com")
    send_daily_email(settings, "PaperFlow 2026-08-20", "plain", "<p>html</p>")
    assert calls[0] == ("connect", "smtp.gmail.com", 587, 30)
    assert ("starttls",) in calls
    assert ("login", "sender@gmail.com", "app-password") in calls
    assert any(call[0] == "send" for call in calls)
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_email.py -v`

Expected: FAIL because `paperflow.email` does not exist.

- [ ] **Step 3: Implement SMTP and cloud CLI mode**

Use `email.message.EmailMessage`; set From/To/Subject, call `set_content(plain)`, then `add_alternative(html, subtype="html")`. `send_daily_email` must connect to `smtp.gmail.com:587`, call `starttls`, `login`, and `send_message` exactly as tested. Do not log the password or private JSON.

Add `paperflow daily --email --no-write`. It requires `PAPERFLOW_GMAIL_ADDRESS`, `PAPERFLOW_GMAIL_APP_PASSWORD`, and `PAPERFLOW_PRIVATE_CONFIG_JSON`; the `mail_to` field comes from private JSON. When all sources fail, attempt a short failure email and still return exit code 3.

- [ ] **Step 4: Add the exact daily workflow**

```yaml
# .github/workflows/daily.yml
name: Daily PaperFlow email

on:
  schedule:
    - cron: "0 0 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  daily:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: python -m pip install .
      - run: paperflow --json daily --email --no-write
        env:
          PAPERFLOW_GMAIL_ADDRESS: ${{ secrets.PAPERFLOW_GMAIL_ADDRESS }}
          PAPERFLOW_GMAIL_APP_PASSWORD: ${{ secrets.PAPERFLOW_GMAIL_APP_PASSWORD }}
          PAPERFLOW_PRIVATE_CONFIG_JSON: ${{ secrets.PAPERFLOW_PRIVATE_CONFIG_JSON }}
```

- [ ] **Step 5: Verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest tests/test_email.py tests/test_daily.py -v`

Expected: all focused tests pass without making network calls.

- [ ] **Step 6: Commit email automation**

```powershell
git add src/paperflow/email.py src/paperflow/cli.py tests/test_email.py tests/test_daily.py .github/workflows/daily.yml
git commit -m "feat: send daily reports through GitHub Actions"
```

### Task 9: Add read-only doctor checks and the Codex Skill

**Files:**
- Create: `src/paperflow/doctor.py`
- Modify: `src/paperflow/cli.py`
- Create: `tests/test_doctor.py`
- Create: `codex/skills/paperflow/SKILL.md`

- [ ] **Step 1: Write injectable doctor tests**

```python
# tests/test_doctor.py
from pathlib import Path

from paperflow.doctor import run_checks


def test_doctor_reports_required_and_optional_components(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    checks = run_checks(
        config_path=tmp_path / "missing.toml",
        vault_path=vault,
        which=lambda name: {"git": "C:/Git/git.exe", "codex.cmd": "C:/npm/codex.cmd"}.get(name),
        path_exists=lambda path: Path(path).name in {"Obsidian.exe", "zotero.exe"},
    )
    by_name = {check.name: check for check in checks}
    assert by_name["git"].ok is True
    assert by_name["config"].ok is False
    assert by_name["zotero"].ok is True
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_doctor.py -v`

Expected: FAIL because `paperflow.doctor` does not exist.

- [ ] **Step 3: Implement read-only checks and CLI output**

Create immutable `Check(name, ok, required, message)` values. `run_checks` checks Python version, Git, config path, Vault path, Codex (`codex.cmd` then `codex`), standard Windows Zotero/Obsidian paths, and the installed Skill path under `%CODEX_HOME%\skills\paperflow` or `%USERPROFILE%\.codex\skills\paperflow`. Sidebar is optional and reported as manual verification because profile paths vary. `paperflow doctor --json` returns all checks and exits 1 only when a required check fails. It must not write files.

- [ ] **Step 4: Add the Codex Skill contract**

```markdown
---
name: paperflow
description: Use when the user asks for today's papers, paper search, an Obsidian paper note, or PaperFlow diagnostics.
---

# PaperFlow

Use the installed `paperflow` CLI. Always request JSON output and interpret it for the user.

- Today's papers: `paperflow --json daily`
- Search: `paperflow --json search "<user query>"`
- Create a note: first show the target paper and path, then run `paperflow --json note <arxiv-id>` after the user asks to save it.
- Diagnostics: `paperflow --json doctor`

Never write `zotero.sqlite`, never read Sidebar API keys, and never add `--force` unless the user explicitly approves replacing the existing note.
```

- [ ] **Step 5: Verify GREEN and validate Skill text**

Run: `.venv\Scripts\python.exe -m pytest tests/test_doctor.py tests/test_cli.py -v`

Expected: all focused tests pass.

Run: `rg -n "zotero\.sqlite|--force|--json" codex/skills/paperflow/SKILL.md`

Expected: three safety/structured-output rules are present.

- [ ] **Step 6: Commit Codex integration**

```powershell
git add src/paperflow/doctor.py src/paperflow/cli.py tests/test_doctor.py codex/skills/paperflow/SKILL.md
git commit -m "feat: add Codex Skill and diagnostics"
```

### Task 10: Add the safe Windows installer, CI, documentation, and release proof

**Files:**
- Create: `scripts/install-windows.ps1`
- Create: `tests/test_installer_contract.py`
- Create: `.github/workflows/ci.yml`
- Create: `README.md`
- Create: `NOTICE`
- Create: `LICENSE`

- [ ] **Step 1: Write installer contract tests before the script**

```python
# tests/test_installer_contract.py
from pathlib import Path


def test_installer_has_non_mutating_check_mode():
    text = Path("scripts/install-windows.ps1").read_text(encoding="utf-8")
    assert "[switch]$CheckOnly" in text
    assert "[switch]$InstallMissing" in text
    assert "ShouldProcess" in text
    assert "Invoke-Expression" not in text
    assert "zotero.sqlite" not in text.casefold()


def test_installer_does_not_collect_cloud_secrets():
    text = Path("scripts/install-windows.ps1").read_text(encoding="utf-8")
    assert "PAPERFLOW_GMAIL_APP_PASSWORD" not in text
    assert "PAPERFLOW_PRIVATE_CONFIG_JSON" not in text
```

- [ ] **Step 2: Verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -v`

Expected: FAIL with `FileNotFoundError` for `scripts/install-windows.ps1`.

- [ ] **Step 3: Implement a preview-first installer**

The script starts with:

```powershell
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$CheckOnly,
    [switch]$InstallMissing,
    [string]$VaultPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$PaperFlowHome = Join-Path $env:LOCALAPPDATA 'PaperFlow'
$BinDir = Join-Path $PaperFlowHome 'bin'
$ConfigDir = Join-Path $env:APPDATA 'PaperFlow'
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$SkillTarget = Join-Path $CodexHome 'skills\paperflow'
```

It prints a table for Git, Python 3.11+, Codex, Zotero, Obsidian, Vault, and Sidebar manual verification. In `-CheckOnly`, it exits after the table. Without `-InstallMissing`, missing prerequisites are reported and never installed. With `-InstallMissing`, only `Git.Git`, `Python.Python.3.11`, `DigitalScholar.Zotero`, and `Obsidian.Obsidian` may be passed to `winget install --id <id> --exact`; each call is guarded by `$PSCmdlet.ShouldProcess`. Codex receives instructions only.

After prerequisites pass, the script creates `.venv`, runs `python -m pip install .`, creates `%LOCALAPPDATA%\PaperFlow\bin\paperflow.cmd`, copies `codex\skills\paperflow` to `$SkillTarget`, and writes local TOML only after validating an existing Vault path. It asks before changing user PATH. Re-running produces the same files without duplicate PATH entries.

- [ ] **Step 4: Verify installer contract and PowerShell syntax**

Run: `.venv\Scripts\python.exe -m pytest tests/test_installer_contract.py -v`

Expected: `2 passed`.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-windows.ps1 -CheckOnly`

Expected: a component table and no created `.venv`, config, Skill, wrapper, or PATH change.

- [ ] **Step 5: Add CI for both execution environments**

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  test:
    strategy:
      matrix:
        os: [windows-latest, ubuntu-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: python -m pip install -e ".[dev]"
      - run: python -m pytest -v
```

- [ ] **Step 6: Add exact user documentation and licensing**

`README.md` must contain these executable sections in order: purpose/non-goals, Windows prerequisites, two-command clone/install, local `config.toml` example, the four CLI commands, Codex Skill behavior, Zotero Connector + AI Sidebar manual flow, three GitHub Secrets with a valid compact JSON example, manual `workflow_dispatch` test, privacy boundaries, upgrade/uninstall, and troubleshooting for arXiv 429/Gmail App Password/PowerShell policy.

Create `NOTICE` with:

```text
PaperFlow
Copyright 2026 PaperFlow contributors

This product includes adapted ideas and portions from
huangkiki/dailypaper-skills, licensed under the Apache License 2.0.

huangkiki/zotero-ai-sidebar is not included in this distribution.
It is an optional, separately installed AGPL-3.0-or-later project.
```

Copy the canonical Apache-2.0 text from the reviewed upstream file at `C:\Users\admin\Documents\Codex\2026-08-12\huangkiki-dailypaper-skills-https-github-com\work\dailypaper-skills\LICENSE` into project `LICENSE`, then verify the first line is `Apache License` and the version line contains `Version 2.0, January 2004`.

- [ ] **Step 7: Run complete offline verification**

Run: `.venv\Scripts\python.exe -m pytest -v`

Expected: all tests pass on Windows with no live API or SMTP calls.

Run: `.venv\Scripts\paperflow.exe --json --version`

Expected: `{"ok": true, "version": "0.1.0"}` with insignificant JSON whitespace differences allowed.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 8: Run opt-in live smoke tests**

Run with a temporary Vault and non-secret test keywords: `paperflow --json daily --date 2026-08-20`

Expected: exit code 0, valid JSON, and exactly one `PaperFlow\Reports\2026-08-20.md`; a partial result is acceptable if one upstream source is unavailable.

Run: `paperflow --json search "robotics"`

Expected: exit code 0 and at least one online or local result when arXiv is reachable.

Do not test Gmail until the user has populated repository Secrets; use GitHub `workflow_dispatch` for that one external-state test.

- [ ] **Step 9: Commit release-ready MVP**

```powershell
git add scripts/install-windows.ps1 tests/test_installer_contract.py .github/workflows/ci.yml README.md NOTICE LICENSE
git commit -m "docs: add Windows install and release verification"
```

- [ ] **Step 10: Record the seven-day pilot without claiming premature completion**

Create `docs/pilot/2026-08-20-checklist.md` with seven dated rows for scheduled-email result, source status, duplicate observation, secret/log audit, and notes. Mark only the installation and local smoke rows after they are actually verified. Do not mark the seven-day acceptance complete until seven real scheduled runs have occurred.

Run: `git status --short --branch`

Expected: only the newly created pilot checklist is untracked.

Commit the checklist:

```powershell
git add docs/pilot/2026-08-20-checklist.md
git commit -m "test: start seven-day PaperFlow pilot"
```

## Final implementation review

- Run `python -m pytest -v` on Windows and verify all offline tests pass.
- Push the implementation branch and verify the Windows and Ubuntu CI matrix.
- Manually inspect one Markdown report for UTF-8 Chinese text, stable fields, valid links, and no leaked private configuration.
- Run `paperflow doctor --json` and retain its output as local evidence without committing private paths.
- Verify `git grep -n -I -E "gmail|app-password|PAPERFLOW_PRIVATE_CONFIG_JSON"` finds only variable names and documentation examples, never real values.
- Verify the GitHub workflow has `contents: read`, no Artifact upload step, and no model API secret.
- Do not claim the full seven-day acceptance until the pilot checklist contains seven real cloud runs.
