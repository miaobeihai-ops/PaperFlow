---
name: paperflow
description: Use when the user asks for today's papers or 论文推荐, paper search or historical/arXiv search, an Obsidian paper note, or PaperFlow diagnostics.
---

# PaperFlow

Use PaperFlow as the single entry point. ALWAYS use JSON. Parse only fields returned by the command.

## Quick Reference

- Daily: `paperflow --json daily`. By default it writes a new or atomically updates today's Obsidian Report; this operation is atomic and same-day idempotent. After success, report `report_path` to the user. Only `--no-write` prevents the write. Use only `--date` or `--no-write` when requested. For cloud email, use `paperflow --json daily --email --no-write`; it does not write a local report. For that email command, exit 2: email/private config invalid or missing; exit 3: all paper sources failed (failure email may be attempted); exit 5: SMTP/email delivery failed. Treat `partial=true` as a valid result and identify each failed source.
- Search: `paperflow --json search "<user query>"`. Show both `history` and `online`; online results are not saved. Use `--history-only` only when requested.
- Note: first search, show the target and proposed `PaperFlow/Papers/<id>.md`, and wait until the user explicitly requests saving. Then run `paperflow --json note <arxiv-id>`. On exit 4, never add `--force` yourself. Run `paperflow --json note <arxiv-id> --force` only after the user explicitly approves replacing the existing note.
- Diagnostics: `paperflow --json doctor`. It is read-only. Explain required versus optional checks; do not install anything.

## Safety and Exit Handling

- Exit 0: success. Exit 1: required doctor check failed. Exit 2: configuration or input error. Exit 3: sources or arXiv failed. Exit 4: note exists. Exit 5: email delivery failed.
- Never write `zotero.sqlite`. PaperFlow does not automatically write Zotero; suggest manual saving with Zotero Connector.
- Never read Sidebar API keys. Never YOLO. Never auto-configure WebDAV.
- If JSON parsing fails or the command is unavailable, report the real error and do not guess the schema.
