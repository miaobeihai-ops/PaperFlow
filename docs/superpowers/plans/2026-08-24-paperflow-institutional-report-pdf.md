# PaperFlow Institutional Report and PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact search timestamps, paper-level full-text evidence and figures, styled HTML/PDF export, and a verified deep read of the first chemical-energy paper without automating institutional credentials.

**Architecture:** Extend the existing immutable research context with an explicit search window, normalize optional evidence attachments during analysis validation, and render Markdown/HTML from the same validated context. Keep PDF export explicit through `research finalize --pdf`, using installed Chrome and a temporary D-drive profile. Extend the existing PaperFlow skill instead of introducing another installed skill.

**Tech Stack:** Python 3.11 standard library, pytest, HTML/CSS print layout, Chrome headless PDF export, existing PaperFlow JSON contracts.

---

## File map

- Modify `src/paperflow/research_context.py`: record exact local search start/end.
- Modify `src/paperflow/research_analysis.py`: validate optional access, full-text, and figure evidence.
- Modify `src/paperflow/research_report.py`: render the revised Markdown and copy verified figures.
- Create `src/paperflow/research_html.py`: render a styled, self-contained HTML report.
- Create `src/paperflow/research_pdf.py`: locate Chrome and export HTML to PDF.
- Modify `src/paperflow/cli.py`: add explicit `--pdf` and return HTML/PDF paths.
- Modify `.agents/skills/paperflow/SKILL.md`: teach exact intervals, supervised institutional access, and PDF/full-text rules.
- Modify `scripts/install-windows.ps1`: no new target; the existing skill copy picks up the revised file.
- Modify focused tests under `tests/test_research_*.py`, `tests/test_skill_contract.py`, and `tests/test_installer_contract.py`.
- Create report-run evidence only under `D:\PaperFlowData\runs\...` and generated report assets under `D:\PaperFlowData\reports\...`.

### Task 1: Record an exact search interval

**Files:**
- Modify: `tests/test_research_context.py`
- Modify: `src/paperflow/research_context.py`

- [ ] **Step 1: Write the failing context test**

Add assertions to the prepared context test:

```python
assert payload["search_window"] == {
    "started_at": "2026-08-22T08:00:00+08:00",
    "ended_at": "2026-08-24T08:00:00+08:00",
    "timezone": "Asia/Hong_Kong",
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest tests/test_research_context.py -q`

Expected: FAIL because `search_window` is absent.

- [ ] **Step 3: Add the minimal context field**

In `prepare_research`, derive the interval from `profile.lookback_hours` and `now`:

```python
local_end = now.astimezone(_LOCAL_ZONE)
local_start = local_end - timedelta(hours=profile.lookback_hours)
payload["search_window"] = {
    "started_at": local_start.isoformat(timespec="seconds"),
    "ended_at": local_end.isoformat(timespec="seconds"),
    "timezone": _LOCAL_ZONE.key,
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `pytest tests/test_research_context.py -q`

Expected: all context tests pass.

- [ ] **Step 5: Commit**

```text
git add src/paperflow/research_context.py tests/test_research_context.py
git commit -m "feat: record exact research search interval"
```

### Task 2: Validate paper-level full-text and figure evidence

**Files:**
- Modify: `tests/test_research_analysis.py`
- Modify: `src/paperflow/research_analysis.py`

- [ ] **Step 1: Write failing evidence-validation tests**

Cover these cases with real temporary files:

```python
analysis["selected"][0].update({
    "access_status": "open_access",
    "full_text_file": "full-text/paper.pdf",
    "figures": [{
        "file": "figures/figure-1.png",
        "figure": "Figure 1",
        "page": 2,
        "caption": "Cell architecture",
        "source_url": "https://doi.org/10.1/x",
        "license": "CC BY 4.0",
    }],
})
analysis["selected"][0]["analysis_depth"] = "full_text"
```

Assert that validation accepts files inside the run directory, rejects missing PDFs, rejects path traversal, rejects more than three figures, and rejects figures for abstract-only analysis.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest tests/test_research_analysis.py -q`

Expected: FAIL because the evidence fields are unknown.

- [ ] **Step 3: Normalize optional evidence fields**

Keep old schema-version 1 analyses valid by accepting optional fields and returning a copied normalized analysis:

```python
defaults = {
    "access_status": "abstract_only",
    "full_text_file": "",
    "figures": [],
}
```

Allow access values `abstract_only`, `open_access`, and `institutional`. For `full_text`, require an existing `.pdf` below `context_path.parent`; for every figure require an existing `.png` or `.jpg` below the same directory, page greater than zero, a candidate URL, and at most three entries. Never follow symlinks outside the run directory.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `pytest tests/test_research_analysis.py -q`

Expected: all analysis tests pass.

- [ ] **Step 5: Commit**

```text
git add src/paperflow/research_analysis.py tests/test_research_analysis.py
git commit -m "feat: validate full-text research evidence"
```

### Task 3: Revise Markdown report structure

**Files:**
- Modify: `tests/test_research_report.py`
- Modify: `src/paperflow/research_report.py`

- [ ] **Step 1: Write failing report-contract tests**

Assert that Markdown contains the exact interval and timezone, does not render headings for suggested actions or global source limitations, and orders paper evidence as source, depth/access, limitation, then figures.

```python
assert "2026-08-22 08:00 - 2026-08-24 08:00" in markdown
assert "Asia/Hong_Kong" in markdown
assert "## 建议行动" not in markdown
assert "## 来源局限" not in markdown
assert markdown.index("原文") < markdown.index("证据边界")
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest tests/test_research_report.py -q`

Expected: FAIL on the old headings and missing interval.

- [ ] **Step 3: Implement the minimal revised renderer**

Add one interval formatter, render `检索时段与来源`, omit `actions` and `source_limitations` from reader-facing sections, and copy validated figures to `reports/chemical-energy/assets/2026-08-24/` before creating relative Markdown image links.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `pytest tests/test_research_report.py -q`

Expected: all report tests pass and no temporary files remain.

- [ ] **Step 5: Commit**

```text
git add src/paperflow/research_report.py tests/test_research_report.py
git commit -m "feat: render evidence-led research reports"
```

### Task 4: Add self-contained HTML rendering

**Files:**
- Create: `src/paperflow/research_html.py`
- Create: `tests/test_research_html.py`
- Modify: `src/paperflow/research_report.py`

- [ ] **Step 1: Write a failing HTML test**

Finalize a fixture and assert:

```python
assert result.html_path == tmp_path / "reports/chemical-energy/2026-08-24.html"
html = result.html_path.read_text(encoding="utf-8")
assert "<!doctype html>" in html.lower()
assert "@page" in html
assert "Asia/Hong_Kong" in html
assert "建议行动" not in html
assert "来源局限" not in html
assert "https://doi.org/10.1/x" in html
assert "<script" not in html
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest tests/test_research_html.py -q`

Expected: FAIL because there is no HTML renderer or path.

- [ ] **Step 3: Implement standard-library HTML generation**

Use `html.escape`, inline print CSS, semantic headings, paper cards, and base64 data URIs for report figures. Keep the document single-column and A4-printable. Do not add JavaScript, a template framework, or external fonts.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `pytest tests/test_research_html.py tests/test_research_report.py -q`

Expected: all HTML and Markdown tests pass.

- [ ] **Step 5: Commit**

```text
git add src/paperflow/research_html.py src/paperflow/research_report.py tests/test_research_html.py tests/test_research_report.py
git commit -m "feat: render printable research HTML"
```

### Task 5: Add explicit Chrome PDF export

**Files:**
- Create: `src/paperflow/research_pdf.py`
- Create: `tests/test_research_pdf.py`
- Modify: `src/paperflow/research_report.py`
- Modify: `src/paperflow/cli.py`
- Modify: `tests/test_research_cli.py`

- [ ] **Step 1: Write failing PDF and CLI tests**

Test browser discovery from an injected candidate list, subprocess argument construction, nonzero export handling, and parser support:

```python
args = build_parser().parse_args([
    "research", "finalize", "--context", "context.json",
    "--analysis", "analysis.json", "--pdf",
])
assert args.pdf is True
```

The exporter test must assert a temporary user-data directory, `--headless=new`, `--no-pdf-header-footer`, and an explicit output path.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest tests/test_research_pdf.py tests/test_research_cli.py -q`

Expected: FAIL because the exporter and flag do not exist.

- [ ] **Step 3: Implement PDF export**

Locate Chrome or Edge from known Windows install paths, create a temporary browser profile below `PAPERFLOW_HOME/tmp`, print the finalized HTML, require a nonempty PDF, and remove only the exact temporary profile created for the command. `finalize_research(..., export_pdf=False)` remains the compatibility default.

- [ ] **Step 4: Return artifact paths from CLI**

Always return `html_path`; return `pdf_path` only when `--pdf` succeeds. Convert browser absence or export failure to a clear PaperFlow configuration error without deleting Markdown, JSON, or HTML artifacts.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `pytest tests/test_research_pdf.py tests/test_research_cli.py tests/test_research_report.py -q`

Expected: all focused tests pass.

- [ ] **Step 6: Commit**

```text
git add src/paperflow/research_pdf.py src/paperflow/research_report.py src/paperflow/cli.py tests/test_research_pdf.py tests/test_research_cli.py tests/test_research_report.py
git commit -m "feat: export research reports to PDF"
```

### Task 6: Update the installed PaperFlow skill contract

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `tests/test_installer_contract.py`
- Modify: `.agents/skills/paperflow/SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Add failing skill-contract assertions**

Require the skill to mention exact timestamps, `--pdf`, full-text file proof, figure attribution, supervised CARSI login, and the prohibition on credentials and automated institutional downloads. Update installer documentation assertions for the new command without adding another skill directory.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest tests/test_skill_contract.py tests/test_installer_contract.py -q`

Expected: FAIL because the current skill lacks the new contract.

- [ ] **Step 3: Make the smallest documentation changes**

Extend the existing Codex Research Flow section. Keep `SKILL.md` concise and direct users to the CLI output rather than duplicating implementation details.

- [ ] **Step 4: Validate skill and focused tests**

Run the system skill validator against `.agents/skills/paperflow`, then run:

`pytest tests/test_skill_contract.py tests/test_installer_contract.py -q`

Expected: validator succeeds and focused tests pass.

- [ ] **Step 5: Commit**

```text
git add .agents/skills/paperflow/SKILL.md README.md tests/test_skill_contract.py tests/test_installer_contract.py
git commit -m "docs: extend PaperFlow report skill"
```

### Task 7: Deep-read the first paper and attach figures

**Files:**
- Create outside Git: `D:\PaperFlowData\runs\chemical-energy\2026-08-24\94e88e00-9587-4ed5-8fbb-b2d91073a6f0\full-text\10.26599-CF.2026.9200083.pdf`
- Create outside Git: up to three PNG files in the same run's `figures\` directory
- Modify outside Git: that run's `analysis.json`

- [ ] **Step 1: Download the publisher PDF manually and safely**

Use the open-access SciOpen publisher page. Do not log in and do not use institutional access. Save the single PDF under the run directory on D:. Record its SHA-256 and publisher DOI URL in working evidence.

- [ ] **Step 2: Read the entire PDF**

Extract text and inspect every page. Record experimental configuration, electrolyte concentrations, electrodes, membrane direction, isotope method, anti-scaling/CER evidence, polarization performance, and the 500-hour stability result. Distinguish values shown in figures from author interpretation.

- [ ] **Step 3: Produce attributed screenshots**

Render pages at readable resolution and crop at most three complete figure regions without modifying data. Use filenames that include figure and page. Record figure number, PDF page, caption, DOI URL, and `CC BY 4.0` in `analysis.json`.

- [ ] **Step 4: Upgrade only the first selected paper**

Set `analysis_depth` to `full_text`, `access_status` to `open_access`, and supply the run-relative full-text and figure paths. Rewrite its method, evidence, limitations, and practical implications from the actual paper. Keep the other papers at abstract depth.

- [ ] **Step 5: Validate analysis without finalizing**

Run the analysis validator through the installed CLI/package. Expected: the selected candidate and all evidence attachments validate successfully.

### Task 8: Install, generate, and visually verify the final report

**Files:**
- Generated: `D:\PaperFlowData\reports\chemical-energy\2026-08-24.{json,md,html,pdf}`
- Generated: `D:\PaperFlowData\reports\chemical-energy\assets\2026-08-24\*`

- [ ] **Step 1: Run complete source verification**

Run: `pytest -q`

Expected: all tests pass with zero failures.

- [ ] **Step 2: Reinstall to D drive**

Run the existing Windows installer with project root `D:\PaperFlow` and data root `D:\PaperFlowData`. Run `paperflow --json doctor` and require all mandatory checks to pass.

- [ ] **Step 3: Finalize with PDF**

Run:

```text
paperflow --json research finalize --context D:\PaperFlowData\runs\chemical-energy\2026-08-24\94e88e00-9587-4ed5-8fbb-b2d91073a6f0\context.json --analysis D:\PaperFlowData\runs\chemical-energy\2026-08-24\94e88e00-9587-4ed5-8fbb-b2d91073a6f0\analysis.json --pdf
```

Expected: JSON output contains Markdown, JSON, HTML, and PDF paths.

- [ ] **Step 4: Verify content and security**

Parse the report JSON, inspect Markdown headings and exact timestamps, verify the first paper is `full_text`, confirm other papers remain abstract-level, and scan generated artifacts for credential/session field names. Do not search for or print actual secrets.

- [ ] **Step 5: Render and inspect every PDF page**

Render the PDF pages to PNG with the available PDF runtime, inspect every page for clipped text, overlap, missing glyphs, blank pages, image distortion, captions, DOI links, and section transitions. Correct and regenerate until no visible defects remain.

- [ ] **Step 6: Commit implementation**

Commit only tracked source, tests, skill, and documentation. Do not commit downloaded PDFs, screenshots, generated reports, credentials, cookies, or browser profiles.

- [ ] **Step 7: Report handoff**

Provide clickable paths to Markdown, HTML, PDF, the first-paper full-text analysis, and the three screenshots. State institutional search coverage separately from the open-access deep read and identify any database that still needs a user-authenticated session.
