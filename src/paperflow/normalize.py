from __future__ import annotations

import re
from dataclasses import replace

from paperflow.models import Paper

ARXIV_RE = re.compile(r"(?:(?:arxiv\.org)/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def canonical_arxiv_id(value: str) -> str:
    match = ARXIV_RE.search(value)
    if not match:
        raise ValueError(f"invalid arXiv identifier: {value}")
    return match.group(1)


def deduplicate(papers: list[Paper]) -> list[Paper]:
    merged: dict[str, Paper] = {}
    for paper in papers:
        arxiv_id = canonical_arxiv_id(paper.arxiv_id)
        current = merged.get(arxiv_id)
        normalized = replace(paper, arxiv_id=arxiv_id)
        if current is None:
            merged[arxiv_id] = normalized
            continue
        sources = tuple(sorted(set(current.sources) | set(normalized.sources)))
        preferred = normalized if normalized.hf_upvotes > current.hf_upvotes else current
        merged[arxiv_id] = replace(
            preferred,
            arxiv_id=arxiv_id,
            sources=sources,
            hf_upvotes=max(current.hf_upvotes, normalized.hf_upvotes),
        )
    return list(merged.values())
