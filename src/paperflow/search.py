from __future__ import annotations

from datetime import date
from pathlib import Path
import re


_REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_LEVEL_TWO_HEADING = re.compile(r"^##\s+.*$", re.MULTILINE)
_PAPER_HEADING = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$")
_ARXIV_ID = re.compile(r"^-\s+arxiv_id:\s+`(\d{4}\.\d{4,5})`\s*$", re.MULTILINE)


def _report_path(path: Path) -> bool:
    match = _REPORT_NAME.fullmatch(path.name)
    if match is None or path.is_symlink() or not path.is_file():
        return False
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return False
    return True


def search_history(vault_path: Path, query: str) -> list[dict[str, str]]:
    needle = query.strip().casefold()
    if not needle:
        raise ValueError("query must not be blank")

    reports = Path(vault_path) / "PaperFlow" / "Reports"
    if not reports.is_dir():
        return []
    paths = sorted(
        (path for path in reports.iterdir() if _report_path(path)),
        key=lambda path: path.name,
        reverse=True,
    )

    results: list[dict[str, str]] = []
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        sections = list(_LEVEL_TWO_HEADING.finditer(content))
        for index, section in enumerate(sections):
            paper_heading = _PAPER_HEADING.fullmatch(section.group())
            if paper_heading is None:
                continue
            end = sections[index + 1].start() if index + 1 < len(sections) else len(content)
            body = content[section.start():end]
            identifier = _ARXIV_ID.search(body)
            if identifier is None or needle not in body.casefold():
                continue
            results.append(
                {
                    "title": paper_heading.group(1).strip(),
                    "arxiv_id": identifier.group(1),
                    "path": str(path),
                }
            )
    return results
