---
name: paperflow
description: Use when the user asks for a chemical-energy or robotics research report, today's papers, active paper search, watched topics, a paper note, or PaperFlow diagnostics.
---

# PaperFlow

Use PaperFlow as the single entry point and always request JSON. Parse only returned fields.

## Quick Reference

- Daily: `paperflow --json daily` atomically writes or updates today's Obsidian report and is same-day idempotent. Report `report_path`. Only `--no-write` prevents the write. Cloud email is `paperflow --json daily --email --no-write`.
- Search: `paperflow --json search "<user query>"`. Optional implemented filters are `--category <arxiv-category>` (repeatable), `--since <YYYY-MM-DD|Nd>`, `--limit <1-100>`, `--sort <relevance|newest>`, and `--history-only`. Show both `history` and `online`; online results are not saved.
- Watch: `paperflow --json watch list` is read-only. Before `paperflow --json watch add "<topic>" --weight <1-100>` or `paperflow --json watch remove "<topic>"`, show the proposed change and wait for explicit approval.
- Note: first show the selected paper and proposed `PaperFlow/Papers/<id>.md`, then wait until the user explicitly approves saving before `paperflow --json note <arxiv-id>`. Use `--force` only after separate replacement approval.
- Diagnostics: `paperflow --json doctor` is read-only. Explain required versus optional checks.

## Codex Research Flow

- For an intelligent domain report, run `paperflow --json doctor`, then exactly one of `paperflow --json research prepare --domain chemical-energy` or `paperflow --json research prepare --domain robotics`.
- Read only the returned `context_path`. Report exact start and end timestamps with the timezone. Deeply inspect at most the profile's `deep_read_limit` papers.
- Use `full_text` only after reading a real PDF stored below the run directory and record its run-relative `full_text_file`; otherwise use `abstract`. Every included screenshot needs a figure number, PDF page, caption, source URL, and license such as CC BY 4.0.
- Write schema-version 1 `analysis.json` next to `context.json`. Every selected `candidate_id` and citation URL must come from that context. Do not copy or rewrite title, authors, date, DOI, arXiv ID, or source URLs into analysis fields.
- Run `paperflow --json research finalize --context <context.json> --analysis <analysis.json>` for Markdown, JSON, and HTML. For PDF, run `paperflow --json research finalize --context <context.json> --analysis <analysis.json> --pdf` and report all artifact paths.
- The scheduled flow is current-run only. Never add `--date`, catch-up, backfill, or missed-run logic. A sleeping or powered-off computer simply misses that run.
- Provider failures with a usable context are partial success: explain the missing coverage and continue. If Scopus or Web of Science requires login, stop that provider and ask the user; never store credentials or automate CAPTCHA.
- Institutional enrichment is supervised: open CARSI or CAS in a controlled browser, the user completes authentication, and Codex performs bounded metadata searches or selected-paper reads. Never store institutional credentials. Never automate institutional database downloads.

- Treat one-off search and watched topics as different intents. Never add a search query to the watchlist without approval.
- For a complex question, run one to three bounded searches, merge online results by `arxiv_id`, and explain why the strongest candidates match. Do not invent fields absent from JSON.
- Commit or push a topic-file change only when the user explicitly asks for Git synchronization.
- For full-text analysis, use an available PDF-reading capability. If none is available, state that analysis is limited to returned titles and abstracts.

## Safety and Exit Handling

- Exit 0 is success; Exit 1 is a required doctor failure; Exit 2 is configuration/input failure; Exit 3 is source/arXiv failure; Exit 4 means a note exists; Exit 5 is email delivery failure. `partial=true` is valid; identify failed sources.
- Never print or read Gmail App Passwords. Never read Sidebar API keys. Never write `zotero.sqlite`. Never YOLO. Never auto-configure WebDAV. PaperFlow does not automatically write Zotero; suggest Zotero Connector.
- Never overwrite a note, mutate watch topics, commit, or push without the explicit approval required above.
- If JSON parsing fails or the command is unavailable, report the real error and do not guess the schema.
