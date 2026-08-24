# PaperFlow institutional search and PDF report design

## Goal

Produce a chemical-energy report that combines unattended public-source discovery with supervised SUSTech database enrichment, shows the exact search interval, and supports full-text analysis with cited paper screenshots. Keep credentials and authenticated sessions outside PaperFlow.

## Search layers

### Automated daily discovery

- Query the configured public providers without an institutional login.
- Preserve the rolling overlap configured by the domain profile to tolerate indexing delays.
- Record and display absolute start and end timestamps in `Asia/Hong_Kong`, for example `2026-08-22 16:53 - 2026-08-24 16:53`, instead of only saying `48 hours`.
- Do not backfill a run missed while the computer is off or asleep.

### Supervised institutional enrichment

- Use a controlled browser session for Web of Science, Scopus, and CAS SciFinder.
- Require the user to complete SUSTech CAS/CARSI authentication in the browser. Do not receive, persist, print, log, commit, or schedule the username, password, cookies, or session tokens.
- Search metadata and citation relationships interactively. Do not scrape result pages or perform systematic or bulk downloads.
- Download no more than the selected full-text set for a report, normally one to five papers.
- Stop and return control to the user for CAPTCHA, reauthentication, terms changes, or an access warning.

### Coverage cadence

- Run a one-time supervised baseline search over the previous 12 months for all configured chemical-energy topics.
- Use the automated rolling interval for daily discovery.
- Use supervised institutional enrichment on demand and when public-source coverage has a material topic gap. Institutional access is not required for an openly accessible paper.

## Report contract

Generate stable sibling artifacts under `D:\PaperFlowData\reports\<domain>\`:

- `<date>.json` is the provenance and analysis record.
- `<date>.md` is the editable source report.
- `<date>.html` is the styled reading and print source.
- `<date>.pdf` is the portable report.
- `assets\<date>\` contains only images used by that report.

The reader-facing report order is:

1. Title, generation time, and exact search interval.
2. Source coverage and database status.
3. Concise executive findings.
4. Selected papers.
5. Cross-paper themes and disagreements.
6. Policy and industry signals.
7. Unresolved research questions.

Do not render standalone `Suggested actions` or global `Source limitations` sections. Keep compatibility fields in analysis JSON for now, but treat them as internal audit data.

For every selected paper, render the original article link and evidence boundary together:

- source URL and DOI;
- analysis depth: metadata, abstract, or full text;
- access status: open access, institutional full text, or abstract only;
- the paper-specific limitations immediately below the link;
- screenshots with figure number, PDF page number, caption, and attribution when used.

Policy webpages remain visibly separate from peer-reviewed paper evidence.

## First-paper deep read

Deep-read `10.26599/CF.2026.9200083` from the publisher's open-access PDF. Verify publication metadata against the publisher page before replacing context metadata in any reader-facing artifact.

The deep-read output must cover:

- the cell architecture and pH-asymmetric mechanism;
- electrolyte composition, electrodes, membrane orientation, and operating conditions;
- evidence for suppressing magnesium/calcium scaling and chlorine evolution;
- isotope-labeling evidence for water dissociation origin;
- the 500-hour stability result at `100 mA cm^-2` and its voltage-rise rate;
- energy, material, scale-up, and evidence-quality limitations not resolved by the paper.

Include at most three screenshots chosen for explanatory value, expected to cover the mechanism, key validation evidence, and long-duration stability. Crop from the PDF without altering scientific content. Attribute the paper, figure, page, DOI, publisher, and CC BY 4.0 license.

## PDF implementation

Render a self-contained HTML document using a project-owned template and print CSS, then use an installed Chromium-family browser to print it to PDF. Do not add Quarto, Pandoc, or a LaTeX distribution.

Use A4 pages, embedded or system-safe Chinese fonts, clickable links, restrained colors, page numbers, and nonbreaking figure/caption blocks. Avoid decorative charts when the report has insufficient quantitative data.

## Skill boundary

Add one project-owned skill for requests to render a PaperFlow research report, export its PDF, or attach full-text paper figures. Keep the skill concise and place deterministic rendering and validation in scripts or production modules rather than repeating commands in `SKILL.md`.

The skill must require:

- exact search timestamps;
- paper-level evidence boundaries;
- visual inspection of the final PDF;
- no credential persistence;
- no automated institutional downloading.

## Validation

Use test-first changes and verify:

- context contains a derivable exact search start and end;
- Markdown and HTML show both timestamps and the timezone;
- standalone suggested-action and source-limitation headings are absent;
- each paper link is adjacent to its analysis depth and limitation;
- full-text status is rejected unless a local PDF was actually read and recorded;
- screenshot metadata includes figure, page, source URL, and license;
- generated HTML contains no external runtime dependency;
- PDF exists, has a plausible page count, and preserves extractable text and links;
- rendered pages show no clipped text, overlaps, blank pages, or unreadable Chinese glyphs;
- repository and output scans find no credential fields or authenticated-session material.

Run focused tests first, then the complete suite. Reinstall into `D:\PaperFlow`, regenerate the report, and visually inspect the produced PDF before any completion claim.

## Non-goals

- Unattended CAS/CARSI login.
- Automated Web of Science, Scopus, or SciFinder scraping.
- Bulk full-text downloading.
- A database or new local state store.
- DOCX, LaTeX, presentation, dashboard, or website generation.
- Catch-up processing for missed scheduled runs.
