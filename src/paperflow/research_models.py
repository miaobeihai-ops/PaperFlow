from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRecord:
    """A provider's traceable reference to a research item."""

    name: str
    url: str
    external_id: str = ""


@dataclass(frozen=True)
class ResearchItem:
    """Provider-neutral metadata used by the Codex research workflow."""

    key: str
    doi: str
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    abstract: str
    published: str
    year: int
    url: str
    pdf_url: str
    subjects: tuple[str, ...]
    sources: tuple[SourceRecord, ...]


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    state: str
    count: int
    message: str = ""


@dataclass(frozen=True)
class ProviderBatch:
    items: tuple[ResearchItem, ...]
    status: ProviderStatus
