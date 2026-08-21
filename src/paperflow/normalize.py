from __future__ import annotations

import re
from dataclasses import replace

from paperflow.models import Paper

ARXIV_RE = re.compile(
    r"(?:"
    r"(\d{4}\.\d{4,5})(?:v\d+)?"
    r"|https?://arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?/?"
    r"|https?://arxiv\.org/pdf/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?"
    r")",
    re.I,
)

_SOURCE_PRIORITY = {"arxiv": 0, "hf-daily": 1, "hf-trending": 2}
_METADATA_FIELDS = (
    "title",
    "authors",
    "abstract",
    "primary_category",
    "published",
    "url",
    "pdf_url",
)


def canonical_arxiv_id(value: str) -> str:
    match = ARXIV_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid arXiv identifier: {value}")
    return next(group for group in match.groups() if group is not None)


def _has_metadata(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, tuple):
        return any(_has_metadata(item) for item in value)
    return bool(value)


def _preference_key(paper: Paper) -> tuple[object, ...]:
    source_priority = min(
        (_SOURCE_PRIORITY.get(source.casefold(), len(_SOURCE_PRIORITY)) for source in paper.sources),
        default=len(_SOURCE_PRIORITY),
    )
    completeness = sum(_has_metadata(getattr(paper, field)) for field in _METADATA_FIELDS)
    return (
        source_priority,
        -completeness,
        paper.published,
        paper.title.casefold(),
        paper.title,
        tuple(author.casefold() for author in paper.authors),
        paper.authors,
        paper.abstract.casefold(),
        paper.abstract,
        paper.primary_category.casefold(),
        paper.primary_category,
        paper.url,
        paper.pdf_url,
    )


def deduplicate(papers: list[Paper]) -> list[Paper]:
    grouped: dict[str, list[Paper]] = {}
    for paper in papers:
        arxiv_id = canonical_arxiv_id(paper.arxiv_id)
        grouped.setdefault(arxiv_id, []).append(
            replace(
                paper,
                arxiv_id=arxiv_id,
                sources=tuple(sorted(set(paper.sources))),
                hf_upvotes=max(0, paper.hf_upvotes),
            )
        )

    merged: list[Paper] = []
    for arxiv_id in sorted(grouped):
        candidates = sorted(grouped[arxiv_id], key=_preference_key)
        metadata = {
            field: next(
                getattr(candidate, field)
                for candidate in candidates
                if _has_metadata(getattr(candidate, field))
            )
            for field in _METADATA_FIELDS
            if any(_has_metadata(getattr(candidate, field)) for candidate in candidates)
        }
        merged.append(
            replace(
                candidates[0],
                **metadata,
                arxiv_id=arxiv_id,
                sources=tuple(sorted({source for candidate in candidates for source in candidate.sources})),
                hf_upvotes=max(candidate.hf_upvotes for candidate in candidates),
            )
        )
    return merged
