# PaperFlow Search, Watch, and Codex Workflow Design

## Goal

Make one-off paper search, persistent daily interests, cloud email, and Codex-assisted research feel like one lightweight workflow without adding a database, web application, vector store, or paid model dependency.

## Scope

This revision adds:

- broader, filtered one-off arXiv search;
- a small `watch` command group for daily topics;
- one versioned, non-secret topic file shared by local and GitHub Actions runs;
- mail-only GitHub Secrets;
- a richer Codex Skill contract that connects search, comparison, note saving, watch changes, and optional full-text reading.

It does not add new paper providers, automatic Zotero writes, PDF storage, model API calls, a database, or a web UI.

## User Workflows

### One-off search

`paperflow --json search "vision language action"` searches immediately and does not change the daily watchlist or save online results. A multi-word query means all safely escaped terms, not one exact phrase. Users can narrow the online request with repeatable `--category`, `--since`, `--limit`, and `--sort` options.

`--since` accepts either an ISO date such as `2026-08-01` or a positive relative duration such as `30d`. `--sort` accepts `relevance` or `newest`. `--limit` accepts integers from 1 through 100. These filters apply to online arXiv results; local report history remains a text match over existing reports.

### Save a paper

The existing `note` behavior remains unchanged. Codex first shows the selected arXiv ID and target note path. It runs `paperflow --json note <id>` only after explicit approval and never adds `--force` without separate replacement approval.

### Persistent daily interests

The command group is:

```text
paperflow --json watch list
paperflow --json watch add "vision language action" --weight 8
paperflow --json watch remove "robotics"
```

`list` is read-only. `add` creates or updates one case-insensitive topic. `remove` is idempotent and reports whether anything changed. Mutations write the topic file atomically. Weights are integers from 1 through 100.

Codex must show the proposed topic and weight before running `watch add` or `watch remove`, then wait for explicit user approval.

### Daily report and cloud email

`paperflow --json daily` continues to write or atomically update the local Obsidian report. GitHub Actions continues to run `paperflow --json daily --email --no-write` at UTC 00:00, which is 08:00 in Hong Kong.

## Configuration Boundaries

### Shared topic file

The repository contains `config/topics.toml`:

```toml
top_n = 10
timezone = "Asia/Hong_Kong"
history_reports = 30
arxiv_categories = ["cs.RO", "cs.CV", "cs.AI", "cs.LG"]

[topics]
robotics = 5
"3d reconstruction" = 8
```

This file is intentionally non-secret and versioned. Clones and GitHub Actions therefore use the same categories, weights, timezone, and report limits. The documentation must state that a public repository exposes these research interests.

`PAPERFLOW_TOPICS_PATH` selects the file and must be absolute. The Windows installer writes that variable into the generated wrapper using the repository's absolute `config\topics.toml` path. GitHub Actions sets it to `${{ github.workspace }}/config/topics.toml`.

### Local-only configuration

`D:\PaperFlowData\config\config.toml` remains local and contains `vault_path`. Existing inline `keywords`, categories, timezone, top-N, and history fields remain accepted for backward compatibility. When `PAPERFLOW_TOPICS_PATH` is set, validated values from the topic file override those inline topic fields while `vault_path` remains local.

### Cloud-only configuration

GitHub Actions uses only these Secrets:

- `PAPERFLOW_GMAIL_ADDRESS`;
- `PAPERFLOW_GMAIL_APP_PASSWORD`;
- `PAPERFLOW_MAIL_TO`.

The workflow no longer needs `PAPERFLOW_PRIVATE_CONFIG_JSON`. The loader retains the old JSON environment variable as a compatibility fallback for callers outside the bundled workflow, but new documentation and tests use the topic file plus mail-only Secrets.

No command prints Secret values. PaperFlow does not collect them through the installer.

## Search Design

Paper provider URLs remain internal constants. Users configure intent and filters, never API addresses.

The arXiv query builder:

1. splits the plain query into Unicode whitespace-delimited terms;
2. safely escapes every term as an `all:` literal;
3. joins terms with `AND`;
4. optionally joins validated `cat:` filters with `OR`;
5. optionally adds a submitted-date lower bound;
6. maps `relevance` and `newest` to fixed arXiv sort parameters.

Raw arXiv operators from user input are never passed through. This preserves the existing parameter-injection protection while avoiding exact-phrase-only searches.

Search JSON keeps `query`, `history`, and `online`, and adds a `filters` object containing normalized categories, since date, limit, and sort. Online results remain unsaved.

## Codex Role

The installed Skill makes Codex the interaction and judgment layer:

- translate natural-language research questions into one to three bounded searches;
- merge results by arXiv ID and explain the most relevant candidates;
- preserve the distinction between one-off search and persistent watch topics;
- request approval before saving a note or changing the watchlist;
- run local daily reports and diagnostics through JSON commands;
- commit or push topic changes only when the user explicitly requests Git synchronization;
- use an available PDF-reading capability for full-text analysis when requested, otherwise clearly limit analysis to returned title and abstract fields.

Codex never reads Gmail App Passwords, AI Sidebar keys, or `zotero.sqlite`. Zotero saving remains manual through Zotero Connector.

## Components

- `src/paperflow/errors.py`: define the shared `ConfigError` without creating a topics/config import cycle; `config.py` continues to re-export it for compatibility.
- `src/paperflow/topics.py`: resolve, validate, render, and atomically mutate the shared topic file.
- `src/paperflow/config.py`: merge local/cloud runtime data with shared topic settings and retain legacy compatibility.
- `src/paperflow/arxiv_source.py`: build safe filtered online search requests.
- `src/paperflow/cli.py`: expose search filters and the `watch` command group.
- `src/paperflow/doctor.py`: report the topic file as a required check when `PAPERFLOW_TOPICS_PATH` is configured.
- `.agents/skills/paperflow/SKILL.md`: define Codex orchestration and write approvals.
- `.github/workflows/daily.yml`: load the shared topic file and mail-only Secrets.
- `scripts/install-windows.ps1`: put `PAPERFLOW_TOPICS_PATH` in the D-root wrapper.

## Error Handling

- Missing, invalid, symlinked, or non-file topic paths fail as configuration errors and never fall back silently when `PAPERFLOW_TOPICS_PATH` is explicitly set.
- Invalid categories, limits, dates, sort names, blank topics, and out-of-range weights return exit code 2 with bounded JSON errors.
- Failed topic mutations leave the original file unchanged.
- arXiv request and response failures retain exit code 3.
- Cloud email keeps exit code 5 for SMTP delivery failure.
- A missing mail Secret fails before paper fetching.

## Testing and Acceptance

Tests must prove:

- topic parsing, precedence, deterministic rendering, case folding, and atomic add/remove behavior;
- explicit invalid topic paths do not fall back;
- the wrapper and workflow select the same versioned topic file;
- search terms, categories, dates, limits, and sorts generate one encoded arXiv request without operator injection;
- CLI JSON reports normalized filters and watch changes;
- old inline local config and old private cloud JSON remain compatible;
- the Skill requires confirmation for watch mutations and notes;
- documentation contains executable local search, watch, daily, and cloud setup examples;
- the full offline suite passes on Windows and Ubuntu-compatible Python behavior;
- a live local search returns online results without writing the Vault;
- a manual GitHub Actions run is attempted only after the user separately authorizes and configures mail Secrets.

## Migration

The change is backward compatible for the existing local installation. Re-running the Windows installer updates the wrapper and Skill but preserves the migrated local config. The versioned topic file becomes authoritative for daily topic settings. No user report, note, Vault file, or Zotero data is migrated or deleted.

The old `PAPERFLOW_PRIVATE_CONFIG_JSON` Secret may be removed from the repository after the new workflow is verified. Its removal is not performed automatically.
