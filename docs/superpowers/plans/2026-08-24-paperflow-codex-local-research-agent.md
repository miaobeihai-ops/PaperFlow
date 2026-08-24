# PaperFlow Codex Local Research Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a database-free research toolkit that two local Codex scheduled tasks use to create independent chemical-energy and robotics reports without catch-up behavior or an embedded LLM API.

**Architecture:** Keep the existing CLI compatible and add a `research` boundary. Versioned domain profiles drive deterministic provider adapters into an immutable context JSON; Codex writes versioned analysis JSON; PaperFlow validates references and atomically renders domain-specific JSON and Markdown reports under `PAPERFLOW_HOME`.

**Tech Stack:** Python 3.11+, stdlib TOML/JSON/XML/dataclasses, httpx, pytest, PowerShell installer, Codex local automations.

---

## File Map

- Create `config/domains/chemical-energy.toml`: public chemical-energy research profile.
- Create `config/domains/robotics.toml`: public robotics research profile.
- Create `src/paperflow/domain.py`: resolve, parse, validate, and overlay domain profiles.
- Create `src/paperflow/research_models.py`: provider-neutral immutable records and statuses.
- Create `src/paperflow/research_dedupe.py`: DOI/arXiv/title-year identity and deterministic merge.
- Create `src/paperflow/providers/__init__.py`: provider registry with fixed provider names.
- Create `src/paperflow/providers/arxiv.py`: adapt existing arXiv APIs to research records.
- Create `src/paperflow/providers/huggingface.py`: adapt existing HF feeds.
- Create `src/paperflow/providers/crossref.py`: bounded Crossref REST collector.
- Create `src/paperflow/providers/openalex.py`: bounded OpenAlex REST collector.
- Create `src/paperflow/providers/feed.py`: bounded HTTPS RSS/Atom collector.
- Create `src/paperflow/research_context.py`: prepare and inspect immutable run contexts.
- Create `src/paperflow/research_analysis.py`: validate Codex analysis against a context.
- Create `src/paperflow/research_report.py`: safe Markdown/JSON rendering and atomic writes.
- Modify `src/paperflow/cli.py`: add `research prepare|inspect|finalize` dispatch.
- Modify `src/paperflow/config.py`: expose the validated `PAPERFLOW_HOME` requirement.
- Modify `scripts/install-windows.ps1`: create D-root research directories and install updated Skill.
- Modify `.agents/skills/paperflow/SKILL.md`: teach Codex scheduled and active research contracts.
- Modify `README.md`: document local-only automation and remove cloud-daily guidance.
- Delete `.github/workflows/daily.yml`: remove scheduled cloud report/email execution.
- Add focused tests and fixtures under `tests/`.

### Task 1: Domain profiles and safe overlays

**Files:**
- Create: `config/domains/chemical-energy.toml`
- Create: `config/domains/robotics.toml`
- Create: `src/paperflow/domain.py`
- Test: `tests/test_domain.py`

- [ ] **Step 1: Write failing profile tests**

```python
from pathlib import Path
import pytest

from paperflow.domain import load_domain_profile
from paperflow.errors import ConfigError


def test_bundled_domains_are_independent_and_current_window_only():
    chemical = load_domain_profile("chemical-energy", project_root=Path.cwd())
    robotics = load_domain_profile("robotics", project_root=Path.cwd())
    assert chemical.slug == "chemical-energy"
    assert "carbon capture" in chemical.query_seeds
    assert robotics.slug == "robotics"
    assert "embodied intelligence" in robotics.query_seeds
    assert chemical.deep_read_limit == 5
    assert not hasattr(chemical, "catch_up_days")


@pytest.mark.parametrize("field", ["command", "provider_url", "api_key", "password"])
def test_private_overlay_cannot_add_executable_secret_or_url(tmp_path, field):
    overlay = tmp_path / "chemical-energy.local.toml"
    overlay.write_text(f'{field} = "PRIVATE_SENTINEL"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="private domain overlay contains forbidden fields"):
        load_domain_profile("chemical-energy", project_root=Path.cwd(), overlay_path=overlay)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_domain.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-domain-red
```

Expected: collection fails because `paperflow.domain` does not exist.

- [ ] **Step 3: Add minimal immutable profile parser**

```python
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
```

`load_domain_profile()` must accept only slugs matching `[a-z0-9-]+`, load
`config/domains/{slug}.toml`, validate integer bounds (`lookback_hours` 1-168,
`candidate_limit` 1-500, `deep_read_limit` 0-10), validate HTTPS feeds, reject
unknown providers, and allow a private overlay to modify only query/concept/rubric
and numeric preference fields.

- [ ] **Step 4: Run profile tests and the existing topic/config tests**

```powershell
python -m pytest tests/test_domain.py tests/test_topics.py tests/test_config.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-domain-green
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add config/domains src/paperflow/domain.py tests/test_domain.py
git commit -m "feat: add research domain profiles"
```

### Task 2: Provider-neutral records and deterministic deduplication

**Files:**
- Create: `src/paperflow/research_models.py`
- Create: `src/paperflow/research_dedupe.py`
- Test: `tests/test_research_dedupe.py`

- [ ] **Step 1: Write failing identity and merge tests**

```python
from paperflow.research_dedupe import deduplicate_research_items
from paperflow.research_models import ResearchItem, SourceRecord


def item(*, key, doi="", arxiv_id="", title="Paper", year=2026, source="crossref"):
    return ResearchItem(
        key=key, doi=doi, arxiv_id=arxiv_id, title=title, authors=(),
        abstract="", published="2026-08-24", year=year, url="https://example.test/p",
        pdf_url="", subjects=(), sources=(SourceRecord(source, "https://example.test/p"),),
    )


def test_dedup_precedence_is_doi_then_arxiv_then_normalized_title_year():
    merged = deduplicate_research_items([
        item(key="a", doi="https://doi.org/10.1/ABC", source="crossref"),
        item(key="b", doi="10.1/abc", source="openalex"),
        item(key="c", arxiv_id="2608.12345v2", source="arxiv"),
        item(key="d", arxiv_id="2608.12345", source="hf-daily"),
        item(key="e", title="A  Useful: Paper", year=2026),
        item(key="f", title="a useful paper", year=2026, source="rss"),
    ])
    assert len(merged) == 3
    assert [record.name for record in merged[0].sources] == ["crossref", "openalex"]
```

- [ ] **Step 2: Run test and verify RED**

Expected: missing module failure.

- [ ] **Step 3: Implement frozen records and merge rules**

`ResearchItem` carries only provider-neutral bibliographic fields. `SourceRecord`
contains `name`, `url`, and optional external ID. Normalize DOI by removing
`https://doi.org/` and case-folding; normalize arXiv versions with the existing
`canonical_arxiv_id`; title fallback uses Unicode normalization, alphanumeric
tokens, single spaces, and year. Sort merged output by published date descending,
then title and key, so provider response order cannot change the context.

- [ ] **Step 4: Run new and existing normalization tests**

```powershell
python -m pytest tests/test_research_dedupe.py tests/test_sources.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-dedupe-green
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/paperflow/research_models.py src/paperflow/research_dedupe.py tests/test_research_dedupe.py
git commit -m "feat: add provider-neutral research records"
```

### Task 3: Adapt existing arXiv and Hugging Face sources

**Files:**
- Create: `src/paperflow/providers/__init__.py`
- Create: `src/paperflow/providers/arxiv.py`
- Create: `src/paperflow/providers/huggingface.py`
- Test: `tests/test_research_existing_providers.py`

- [ ] **Step 1: Write failing adapter tests with existing fixtures**

```python
def test_arxiv_adapter_returns_normalized_records_and_bounded_status(profile):
    xml = Path("tests/fixtures/arxiv_feed.xml").read_text(encoding="utf-8")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=xml, request=request)
    )
    with httpx.Client(transport=transport) as client:
        batch = collect_arxiv(client, profile, now=datetime(2026, 8, 24, tzinfo=UTC))
    assert batch.status.name == "arxiv"
    assert batch.status.state == "ok"
    assert all(record.sources[0].name == "arxiv" for record in batch.items)


def test_hf_adapter_marks_one_feed_failure_partial_without_leaking_exception(profile):
    batch = collect_huggingface(FailingSecondRequestClient(), profile, now=FIXED_NOW)
    assert batch.status.state == "partial"
    assert batch.status.message == "network error"
```

- [ ] **Step 2: Run and verify RED**

Expected: missing provider package.

- [ ] **Step 3: Implement adapters over existing parsers**

Add `ProviderStatus(name, state, count, message)` and
`ProviderBatch(items, status)`. Use existing retry helpers and parsers. Never copy
raw exceptions into status. The arXiv adapter executes bounded searches from the
profile categories/query seeds; HF adapts Daily and Trending once each and merges
their records.

- [ ] **Step 4: Run provider and legacy source suites**

```powershell
python -m pytest tests/test_research_existing_providers.py tests/test_sources.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-existing-providers
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/paperflow/providers tests/test_research_existing_providers.py
git commit -m "feat: adapt existing paper providers for research"
```

### Task 4: Add Crossref, OpenAlex, and RSS/Atom providers

**Files:**
- Create: `src/paperflow/providers/crossref.py`
- Create: `src/paperflow/providers/openalex.py`
- Create: `src/paperflow/providers/feed.py`
- Create: `tests/fixtures/crossref_response.json`
- Create: `tests/fixtures/openalex_response.json`
- Create: `tests/fixtures/research_feed.xml`
- Test: `tests/test_research_web_providers.py`

- [ ] **Step 1: Write fixture-driven failing parser and URL tests**

```python
def test_crossref_query_is_encoded_and_bounded(fixed_client, profile):
    batch = collect_crossref(fixed_client, profile, now=FIXED_NOW)
    assert "rows=100" in fixed_client.request.url.query.decode()
    assert "filter=from-pub-date%3A2026-08-17" in fixed_client.request.url.query.decode()
    assert batch.items[0].doi == "10.1000/example"


def test_openalex_does_not_accept_profile_supplied_base_urls(profile):
    assert collect_openalex(FixtureClient(), profile, now=FIXED_NOW).status.state == "ok"


def test_feed_rejects_http_and_parses_atom_without_active_content(tmp_path):
    with pytest.raises(ConfigError, match="feed URLs must use HTTPS"):
        profile_with_feeds("http://example.test/feed")
```

- [ ] **Step 2: Run and verify RED**

Expected: provider modules absent.

- [ ] **Step 3: Implement fixed-endpoint collectors**

Use code-owned endpoints:

```python
CROSSREF_WORKS_URL = "https://api.crossref.org/works"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
```

Build requests with `urllib.parse.urlencode`; cap each query at 100 results and
the combined provider result at the profile candidate limit. Parse malformed
individual records by skipping them; malformed top-level payloads fail only that
provider. RSS/Atom uses `xml.etree.ElementTree`, HTTPS-only configured feeds, one
request per feed, no embedded HTML rendering, and a maximum of 20 feeds.

- [ ] **Step 4: Run provider suites**

```powershell
python -m pytest tests/test_research_web_providers.py tests/test_fetch.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-web-providers
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/paperflow/providers tests/fixtures tests/test_research_web_providers.py
git commit -m "feat: add scholarly and feed providers"
```

### Task 5: Prepare immutable current-window contexts

**Files:**
- Create: `src/paperflow/research_context.py`
- Test: `tests/test_research_context.py`

- [ ] **Step 1: Write failing storage and no-catch-up tests**

```python
def test_prepare_writes_unique_contexts_below_data_root(tmp_path, fixed_batches):
    first = prepare_research("chemical-energy", home=tmp_path, now=FIXED_NOW, collectors=fixed_batches)
    second = prepare_research("chemical-energy", home=tmp_path, now=FIXED_NOW, collectors=fixed_batches)
    assert first.context_path != second.context_path
    assert first.context_path.parts[-5:-1] == ("runs", "chemical-energy", "2026-08-24", first.run_id)
    assert first.context_path.read_bytes() == first.context_bytes


def test_prepare_has_no_date_or_catch_up_input():
    assert list(inspect.signature(prepare_research).parameters) == [
        "domain", "home", "now", "collectors", "project_root"
    ]
```

- [ ] **Step 2: Run and verify RED**

Expected: missing `research_context`.

- [ ] **Step 3: Implement context schema and atomic exclusive write**

Use schema version `1`, a UUID4 run ID, UTC generated timestamp, domain-local date,
profile snapshot, normalized candidates, and provider statuses. Write UTF-8/LF
JSON with `open(..., "x")` to a unique run directory. `inspect_context()` resolves
the path, requires it to be below `home/runs/{domain}`, rejects symlinks and
schema/domain mismatches, and returns a bounded summary.

- [ ] **Step 4: Run context tests**

```powershell
python -m pytest tests/test_research_context.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-context-green
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/paperflow/research_context.py tests/test_research_context.py
git commit -m "feat: prepare immutable research contexts"
```

### Task 6: Validate Codex analysis and render independent reports

**Files:**
- Create: `src/paperflow/research_analysis.py`
- Create: `src/paperflow/research_report.py`
- Test: `tests/test_research_analysis.py`
- Test: `tests/test_research_report.py`

- [ ] **Step 1: Write failing provenance and atomic report tests**

```python
def test_analysis_rejects_unknown_candidate_and_metadata_override(context_path, analysis):
    analysis["selected"][0]["candidate_id"] = "unknown"
    with pytest.raises(ConfigError, match="analysis references an unknown candidate"):
        validate_analysis(context_path, analysis)


def test_finalize_writes_separate_domain_json_and_markdown(tmp_path, contexts, analyses):
    chemical = finalize_research(contexts["chemical-energy"], analyses["chemical-energy"], home=tmp_path)
    robotics = finalize_research(contexts["robotics"], analyses["robotics"], home=tmp_path)
    assert chemical.markdown_path == tmp_path / "reports/chemical-energy/2026-08-24.md"
    assert robotics.markdown_path == tmp_path / "reports/robotics/2026-08-24.md"
    assert chemical.markdown_path.read_text(encoding="utf-8").startswith("---\n")
```

- [ ] **Step 2: Run and verify RED**

Expected: missing analysis/report modules.

- [ ] **Step 3: Implement schema validation and safe renderer**

Require exact schema/run/domain fields, bounded score integers 0-10, confidence
`low|medium|high`, analysis depth `abstract|full_text`, known candidate IDs, and
source URLs already present in context. Ignore no unknown fields: reject them.
Render bibliographic metadata only from context. Reuse
`escape_markdown_text/block` and atomically replace same-day Markdown/JSON with
unique same-directory temporary files and `os.replace`.

- [ ] **Step 4: Run new and existing report safety tests**

```powershell
python -m pytest tests/test_research_analysis.py tests/test_research_report.py tests/test_report.py tests/test_search_notes.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-report-green
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/paperflow/research_analysis.py src/paperflow/research_report.py tests/test_research_analysis.py tests/test_research_report.py
git commit -m "feat: validate and render Codex research analysis"
```

### Task 7: Expose the JSON-first research CLI

**Files:**
- Modify: `src/paperflow/cli.py`
- Modify: `src/paperflow/config.py`
- Test: `tests/test_research_cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI contract tests**

```python
def test_research_prepare_json_supports_global_flag_positions(monkeypatch, capsys):
    monkeypatch.setattr("paperflow.cli.prepare_research", fake_prepare)
    for argv in (["--json", "research", "prepare", "--domain", "chemical-energy"],
                 ["research", "prepare", "--domain", "chemical-energy", "--json"]):
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "research.prepare"
        assert payload["domain"] == "chemical-energy"


@pytest.mark.parametrize("forbidden", ["--date", "--catch-up", "--backfill"])
def test_research_prepare_rejects_catch_up_style_flags(forbidden, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["research", "prepare", "--domain", "robotics", forbidden, "1"])
```

- [ ] **Step 2: Run and verify RED**

Expected: parser has no `research` command.

- [ ] **Step 3: Implement parser and typed dispatch**

Add nested subcommands exactly as specified. Require `PAPERFLOW_HOME` for all
research commands; do not fall back to APPDATA. Preserve exit codes 2/3 and
`partial=true`. JSON output exposes safe paths, run/domain IDs, counts, statuses,
and candidate fields but no environment dump or raw exception.

- [ ] **Step 4: Run CLI suites**

```powershell
python -m pytest tests/test_research_cli.py tests/test_cli.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-cli-green
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/paperflow/cli.py src/paperflow/config.py tests/test_research_cli.py tests/test_cli.py
git commit -m "feat: expose Codex research CLI"
```

### Task 8: Update installation, Skill, docs, and cloud workflow boundary

**Files:**
- Modify: `scripts/install-windows.ps1`
- Modify: `.agents/skills/paperflow/SKILL.md`
- Modify: `README.md`
- Delete: `.github/workflows/daily.yml`
- Modify: `tests/test_installer_contract.py`
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Write failing contract tests**

```python
def test_installer_wrapper_keeps_research_runtime_on_data_root(installed_tree):
    assert (installed_tree / "runs").is_dir()
    assert (installed_tree / "reports/chemical-energy").is_dir()
    assert (installed_tree / "reports/robotics").is_dir()


def test_skill_defines_local_scheduled_research_without_catch_up():
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "research prepare --domain" in text
    assert "research finalize" in text
    assert "never perform catch-up" in text


def test_no_bundled_daily_cloud_workflow():
    assert not Path(".github/workflows/daily.yml").exists()
```

- [ ] **Step 2: Run and verify RED**

Expected: missing directories/Skill language and existing workflow cause failures.

- [ ] **Step 3: Implement installer and documentation changes**

The installer creates only known D-root directories, keeps existing config bytes,
and installs the updated Skill atomically. README examples use local Codex tasks,
describe 08:00/08:30 schedules, state that shutdown/sleep means no report, and do
not describe recovery. Remove the cloud-daily workflow; retain existing CI.

- [ ] **Step 4: Run installer, Skill, workflow, and README contracts**

```powershell
python -m pytest tests/test_installer_contract.py tests/test_skill_contract.py tests/test_workflow.py -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-contract-green
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add scripts/install-windows.ps1 .agents/skills/paperflow/SKILL.md README.md tests/test_installer_contract.py tests/test_skill_contract.py tests/test_workflow.py
git add -u .github/workflows/daily.yml
git commit -m "docs: switch PaperFlow to local Codex research tasks"
```

### Task 9: Full verification and isolated live smoke

**Files:**
- Modify only if a failing test exposes a defect covered by this specification.

- [ ] **Step 1: Run the complete offline suite without pytest C-drive cache**

```powershell
python -m pytest -q -p no:cacheprovider --basetemp D:\PaperFlowData\tmp\pf-full-suite
```

Expected: all existing 496 tests plus new tests pass with no warnings.

- [ ] **Step 2: Run static checks**

```powershell
git diff --check
python -m compileall -q src tests
paperflow --json doctor
```

Expected: all commands exit 0; doctor has no required failure.

- [ ] **Step 3: Run an isolated live prepare smoke test**

Set `PAPERFLOW_HOME` to a new explicit directory under
`D:\PaperFlowData\pilot\codex-local-agent`, run one domain with its fixture-sized
limits, inspect the context, and confirm no report exists before finalization.

```powershell
$prepared = paperflow --json research prepare --domain robotics | ConvertFrom-Json
paperflow --json research inspect --context $prepared.context_path
```

Expected: at least one provider succeeds or the command returns a truthful bounded
partial/all-source failure; all writes remain inside the isolated pilot root.

- [ ] **Step 4: Create a fixture analysis and finalize the pilot context**

Use only returned candidate IDs and URLs, then run `research finalize`. Confirm
Markdown and JSON paths are under the robotics report directory and render safely.

- [ ] **Step 5: Handle verification defects through a fresh TDD cycle**

If verification exposes a defect, add one focused failing regression test to the
task that owns the behavior, verify RED, make the minimal fix, verify GREEN, and
commit that exact test and implementation with message
`fix: harden local research verification`. If no defect is found, make no commit.

### Task 10: Install and create two local Codex automations

**Files/State:**
- Update local installation at `D:\PaperFlow` only after branch integration.
- Preserve `D:\PaperFlowData\config\config.toml`.
- Create two Codex local automations through the Codex automation API.

- [ ] **Step 1: Review branch and integration state**

Run the requesting-code-review and verification-before-completion skills. Confirm
the worktree is clean, commits contain only scoped files, and tests are current.

- [ ] **Step 2: Present integration choices**

Use finishing-a-development-branch. Do not merge, push, or delete the worktree
without the user's selected branch disposition.

- [ ] **Step 3: Re-run the Windows installer after integration**

```powershell
.\scripts\install-windows.ps1 -DataRoot "D:\PaperFlowData"
```

Expected: existing local config is preserved, the wrapper and Skill are updated,
and doctor succeeds.

- [ ] **Step 4: Create the chemical-energy automation**

Create a local cron automation named `PaperFlow 化工能源日报`, scheduled daily at
08:00 Asia/Hong_Kong, targeting the integrated `D:\PaperFlow` project. Its prompt
must run doctor, prepare exactly `chemical-energy`, perform bounded Codex analysis,
finalize, and report partial/needs-attention sources. It must explicitly prohibit
catch-up, profile mutation, Zotero writes, Git operations, and credential handling.

- [ ] **Step 5: Create the robotics automation**

Create a separate local cron automation named `PaperFlow 机器人日报`, scheduled
daily at 08:30 Asia/Hong_Kong, with the same constraints and domain `robotics`.

- [ ] **Step 6: Verify automation definitions and one manual run per domain**

View both automation definitions, verify project ID/model/reasoning/schedule/prompt,
and manually run each once. Confirm each report is written to its own D-root path
and neither task produces a prior-date report.

---

## Final Acceptance

- Existing CLI behavior and all tests remain green.
- No SQLite, vector store, web UI, Docker, embedded LLM key, or catch-up logic exists.
- GitHub no longer schedules daily report/email work.
- Chemical-energy and robotics use independent profiles, contexts, reports, and local Codex tasks.
- PaperFlow validates every Codex-selected candidate and bibliographic citation against deterministic context data.
- Runtime writes and test caches used for verification stay on D drive except Codex application's own managed state.
