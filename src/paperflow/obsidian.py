from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import re


_REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_ARXIV_ID = re.compile(r"^- arxiv_id: `(\d{4}\.\d{4,5})`$", re.MULTILINE)


def _valid_report_name(path: Path) -> bool:
    match = _REPORT_NAME.fullmatch(path.name)
    if match is None or not path.is_file():
        return False
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return False
    return True


def write_daily_report(vault_path: Path, report_date: str, content: str) -> Path:
    date.fromisoformat(report_date)
    reports = Path(vault_path) / "PaperFlow" / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    target = reports / f"{report_date}.md"
    temporary = reports / f".{report_date}.md.tmp"
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(normalized)
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def recent_arxiv_ids(vault_path: Path, limit: int) -> set[str]:
    if limit < 0:
        raise ValueError("limit must not be negative")
    if limit == 0:
        return set()
    reports = Path(vault_path) / "PaperFlow" / "Reports"
    if not reports.is_dir():
        return set()

    report_paths = sorted(
        (path for path in reports.iterdir() if _valid_report_name(path)),
        key=lambda path: path.name,
        reverse=True,
    )[:limit]
    arxiv_ids: set[str] = set()
    for path in report_paths:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        arxiv_ids.update(_ARXIV_ID.findall(content))
    return arxiv_ids
