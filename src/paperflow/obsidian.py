from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import re
import tempfile


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
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    descriptor = None
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{report_date}.", suffix=".tmp", dir=reports
        )
        temporary = Path(temporary_name)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with stream:
            stream.write(normalized)
            stream.flush()
        os.replace(temporary, target)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def recent_arxiv_ids(
    vault_path: Path,
    limit: int,
    *,
    exclude_date: str | None = None,
) -> set[str]:
    if limit < 0:
        raise ValueError("limit must not be negative")
    if limit == 0:
        return set()
    reports = Path(vault_path) / "PaperFlow" / "Reports"
    if not reports.is_dir():
        return set()

    excluded_name = f"{exclude_date}.md" if exclude_date is not None else None
    report_paths = sorted(
        (
            path
            for path in reports.iterdir()
            if _valid_report_name(path) and path.name != excluded_name
        ),
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
