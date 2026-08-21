from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    primary_category: str
    published: str
    sources: tuple[str, ...]
    hf_upvotes: int
    url: str
    pdf_url: str

    def with_sources(self, sources: tuple[str, ...]) -> "Paper":
        return replace(self, sources=sources)


@dataclass(frozen=True)
class RankedPaper:
    paper: Paper
    score: int
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True)
class SourceFailure:
    source: str
    message: str


@dataclass(frozen=True)
class DailyResult:
    date: str
    papers: tuple[RankedPaper, ...]
    failures: tuple[SourceFailure, ...]
    report_path: Path | None
