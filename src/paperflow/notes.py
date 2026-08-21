from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import tempfile

from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id


class NoteExists(FileExistsError):
    pass


def paper_note_path(vault_path: Path, value: str) -> Path:
    arxiv_id = canonical_arxiv_id(value)
    return Path(vault_path) / "PaperFlow" / "Papers" / f"{arxiv_id}.md"


def _yaml(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_note(paper: Paper, arxiv_id: str, created: date) -> str:
    title = paper.title.replace("\r\n", "\n").replace("\r", "\n")
    abstract = paper.abstract.replace("\r\n", "\n").replace("\r", "\n")
    lines = [
        "---",
        f"arxiv_id: {_yaml(arxiv_id)}",
        f"title: {_yaml(paper.title)}",
        f"authors: {_yaml(list(paper.authors))}",
        f"source_url: {_yaml(paper.url)}",
        f"pdf_url: {_yaml(paper.pdf_url)}",
        f"status: {_yaml('unread')}",
        f"created: {_yaml(created.isoformat())}",
        "---",
        "",
        f"# {title}",
        "",
        "## Abstract",
        "",
        abstract,
        "",
        "## Reading Notes",
        "",
        "<!-- Add your own reading notes here. -->",
        "",
    ]
    return "\n".join(lines)


def write_paper_note(
    vault_path: Path,
    paper: Paper,
    force: bool = False,
    *,
    created: date | None = None,
) -> Path:
    arxiv_id = canonical_arxiv_id(paper.arxiv_id)
    target = paper_note_path(vault_path, arxiv_id)
    papers = target.parent
    if target.exists() and not force:
        raise NoteExists("paper note already exists")

    papers.mkdir(parents=True, exist_ok=True)
    content = _render_note(paper, arxiv_id, created or date.today())
    if target.exists() and not force:
        raise NoteExists("paper note already exists")
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{arxiv_id}.", suffix=".tmp", dir=papers
        )
        temporary = Path(temporary_name)
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with stream:
            stream.write(content)
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
