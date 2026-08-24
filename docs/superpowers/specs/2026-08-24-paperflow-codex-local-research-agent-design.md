# PaperFlow Codex Local Research Agent Design

## Goal

Turn the existing PaperFlow repository into a lightweight toolkit that two local
Codex scheduled tasks can use to produce independent chemical-energy and robotics
research reports. Codex owns search planning, relevance judgment, full-text
analysis, cross-source synthesis, and report prose. PaperFlow owns deterministic
collection, normalization, deduplication, validation, storage, and rendering.

The design keeps the existing repository, CLI, Windows installer, D-drive data
root, JSON-first Codex contract, and database-free architecture. It does not add
catch-up runs: a missed local schedule remains missed.

## Decisions

- Execution is local through Codex scheduled tasks, not GitHub Actions. The
  repository's existing daily cloud schedule is removed; ordinary CI tests remain.
- PaperFlow does not call an LLM API. The scheduled Codex task is the LLM layer.
- The chemical-energy and robotics reports are separate runs with separate
  profiles, output directories, failures, and notifications.
- All PaperFlow runtime data stays under `PAPERFLOW_HOME`, normally
  `D:\PaperFlowData`.
- State is stored as bounded JSON and Markdown files, never SQLite or another
  database.
- A run processes only the current trigger window. There is no missed-date scan,
  backlog, catch-up option, or automatic replay after sleep or shutdown.
- GitHub remains the source-code and public-profile backup. Local reports, PDFs,
  run manifests, private preferences, and credentials are not committed.

## User Workflows

### Scheduled chemical-energy report

At 08:00 local time, Codex reads the chemical-energy profile, prepares a bounded
candidate packet, judges whether coverage is sufficient, performs up to three
additional bounded searches when needed, deep-reads the strongest available
papers, writes structured analysis, asks PaperFlow to validate and render it, and
reports the output path and any degraded sources.

The profile covers hydrogen energy, gas separation, VOC recovery, membrane
separation applications, carbon dioxide utilization, carbon capture, and related
national policy. English and Chinese query seeds are allowed.

### Scheduled robotics report

At 08:30 local time, a separate Codex task follows the same contract with a
robotics profile. Its relevance rubric emphasizes robotics, embodied systems,
perception, planning, control, deployment evidence, open-source artifacts,
industrial activity, and related policy.

### Active research

The existing `search` command remains the one-off entry point. Codex may run one
to three searches, merge results by canonical paper identity, and explain the
best matches. A one-off query never changes a domain profile or watch topic.

Full-text analysis is performed only when a PDF can be fetched and read by an
available Codex capability. When full text is unavailable, the report labels the
analysis as title-and-abstract only.

## CLI Contract

The existing `daily`, `search`, `watch`, `note`, and `doctor` commands remain
compatible. A new `research` command group provides the scheduled-agent boundary.

```text
paperflow --json research prepare --domain chemical-energy
paperflow --json research prepare --domain robotics
paperflow --json research finalize --context <context.json> --analysis <analysis.json>
paperflow --json research inspect --context <context.json>
```

`research prepare` performs no LLM work and does not write a final report. It:

1. resolves and validates the selected domain profile;
2. collects a bounded current-window candidate set from enabled providers;
3. normalizes and deduplicates candidates;
4. records provider successes and failures;
5. writes an immutable run context under
   `PAPERFLOW_HOME\runs\<domain>\<YYYY-MM-DD>\<run-id>\context.json`;
6. returns a bounded JSON envelope containing the context path, counts, provider
   status, and candidate summaries.

The run ID is unique. Re-running on the same day creates a new run and never
silently reuses or overwrites an earlier context.

`research inspect` validates an existing context and returns its safe summary.
It enables Codex to resume within the same task without trusting arbitrary JSON.

Codex writes `analysis.json` next to the context using the versioned schema below.
`research finalize` rejects analysis that cites an unknown paper, changes source
metadata, contains a mismatched domain/run ID, or omits required provenance. On
success it atomically writes:

```text
PAPERFLOW_HOME\reports\chemical-energy\YYYY-MM-DD.md
PAPERFLOW_HOME\reports\chemical-energy\YYYY-MM-DD.json
PAPERFLOW_HOME\reports\robotics\YYYY-MM-DD.md
PAPERFLOW_HOME\reports\robotics\YYYY-MM-DD.json
```

A same-domain same-day finalization replaces that day's report atomically, while
the immutable run directory preserves which context and analysis produced it.

## Domain Profiles

Versioned public profiles live in:

```text
config/domains/chemical-energy.toml
config/domains/robotics.toml
```

Each profile contains only public research intent:

- display name and report language;
- default lookback window and candidate limits;
- query seeds and include/exclude concepts;
- enabled providers and provider-specific non-secret settings;
- relevance rubric and deep-read limit;
- report section requirements.

Credentials, local paths, private feedback, and account identifiers are forbidden
in versioned profiles. Optional private overlays live under
`PAPERFLOW_HOME\config\domains\<domain>.local.toml` and may adjust preferences but
cannot define executable commands or provider URLs.

## Provider Framework

Providers implement one small interface: accept a validated profile and bounded
window, return normalized records plus a sanitized status. The first V2 slice
adapts the existing arXiv and Hugging Face providers and adds Crossref, OpenAlex,
and configurable RSS/Atom collection. The framework must tolerate one provider
failing while preserving successful candidates.

Scopus and Web of Science are optional future authenticated providers. PaperFlow
must not scrape login pages, store passwords, or automate CAPTCHA. An unavailable
authenticated provider returns `needs_attention` with a sanitized reason so the
Codex task can notify the user and continue with other sources.

Canonical identity precedence is DOI, then canonical arXiv ID, then a normalized
title-and-year fallback. Source-specific metadata is retained as provenance;
deduplication never asks Codex to invent or rewrite identifiers.

## Analysis Schema

Codex analysis is structured data, not final Markdown. It contains:

- schema version, run ID, domain, and generated timestamp;
- coverage assessment and additional queries performed;
- selected paper IDs and explicit analysis depth (`full_text` or `abstract`);
- relevance, novelty, evidence quality, industrial value, and confidence scores;
- concise reason for selection;
- method, evidence, limitations, and practical implications;
- cross-paper themes, disagreements, policy/industry links, and actions;
- citations that refer only to candidate IDs and source URLs in the context;
- source limitations and unresolved questions.

PaperFlow treats source title, authors, identifiers, dates, and URLs as immutable.
The renderer combines validated source metadata with Codex analysis, so prose can
be intelligent without allowing fabricated bibliographic fields.

## Codex Scheduled Task Contract

Two standalone local Codex automations target the PaperFlow project. Their prompts
must:

1. run `paperflow --json doctor` and stop on required failures;
2. run `research prepare` for exactly one domain;
3. read only fields returned by PaperFlow or source files named by those fields;
4. perform at most three additional searches and respect profile limits;
5. avoid changing watch topics, Zotero, Git, profiles, or credentials;
6. write analysis matching the versioned schema;
7. run `research finalize` and report the returned report path;
8. surface partial and `needs_attention` providers explicitly;
9. never perform catch-up or process a missed prior date.

The tasks use the current local date and timezone at trigger time. If the computer
is off, asleep, Codex is unavailable, or the task does not run, PaperFlow takes no
later recovery action.

## Storage Layout

```text
D:\PaperFlowData\
  config\
    config.toml
    domains\
      chemical-energy.local.toml   # optional, ignored/private
      robotics.local.toml          # optional, ignored/private
  cache\
    providers\
    pdf\
  tmp\
  runs\
    chemical-energy\YYYY-MM-DD\<run-id>\
    robotics\YYYY-MM-DD\<run-id>\
  reports\
    chemical-energy\
    robotics\
  bin\paperflow.cmd
```

Provider caches are bounded by age and size. Run cleanup, if later added, must be
an explicit maintenance command; it is not part of scheduled report generation.

## Security and Safety

- Provider URLs are code-owned allowlisted defaults or validated HTTPS feed URLs.
- Remote text is untrusted data and cannot modify the Codex task instructions.
- Error output is sanitized and never includes query-string credentials, headers,
  secrets, private overlay contents, or raw authenticated responses.
- Finalization accepts paths only within the matching run directory.
- Report Markdown neutralizes active HTML, Obsidian embeds, and instruction-like
  content copied from sources.
- PaperFlow never writes `zotero.sqlite`, never configures WebDAV, and never saves
  a paper to Zotero automatically.
- Git commit and push remain explicit user-authorized operations.

## Error Handling

- Invalid domain/profile/context/analysis returns exit code 2 with bounded JSON.
- Complete provider failure returns exit code 3 and creates no final report.
- Partial provider failure is successful with `partial=true` and detailed safe
  provider statuses.
- Authenticated sources that need login return `needs_attention`; they do not
  block anonymous providers.
- Failed finalization preserves the previous same-day report and cleans only its
  own temporary file.
- Keyboard interrupts and process termination are not converted into success.

## Testing and Acceptance

Implementation follows RED-GREEN-REFACTOR. Tests must prove:

- both domain profiles parse and reject secret/path/executable fields;
- private overlays cannot introduce providers, URLs, or commands;
- provider adapters return one normalized contract and sanitize failures;
- DOI/arXiv/title fallback deduplication is deterministic;
- `prepare` writes a unique immutable context below `PAPERFLOW_HOME`;
- `prepare` has no missed-date or catch-up behavior;
- analysis schema validation rejects unknown IDs and altered metadata;
- finalization writes separate domain reports atomically;
- one domain cannot read or overwrite another domain's run/report;
- existing CLI and 496-test baseline remain compatible;
- the installed PaperFlow Skill describes scheduled and active research correctly;
- Windows wrapper keeps cache, temp, run, and report paths on the selected D-drive
  data root;
- offline fixtures cover all providers before any live smoke test;
- a live prepare smoke test writes only to an isolated D-drive test root;
- two Codex local automations are created only after CLI verification and point to
  the PaperFlow project with separate domains and schedules.

## Out of Scope

- catch-up, backlog replay, or missed-run recovery;
- GitHub Actions report generation or scheduled email delivery;
- an embedded LLM client or OpenAI API key in PaperFlow;
- databases, vector stores, web dashboards, Docker, or always-on services;
- automatic Zotero writes;
- Scopus/Web of Science credential automation;
- automatic profile mutation from search queries or user feedback;
- weekly clustering, knowledge graphs, podcasts, and social-media publishing.

## Migration

The CLI feature is additive. Existing commands, topic configuration, Obsidian
reports, notes, and installer behavior remain valid. The existing GitHub Actions
daily report/email schedule is removed because local Codex automations replace it;
CI remains available for pushes and pull requests. Existing mail configuration is
left untouched but is no longer used by a bundled schedule.

Re-running the Windows installer updates the wrapper and installed Skill while
preserving `D:\PaperFlowData\config\config.toml`. New runtime directories are
created only when their corresponding research command runs. No existing report,
note, cache, Vault content, or Zotero data is migrated or deleted.
