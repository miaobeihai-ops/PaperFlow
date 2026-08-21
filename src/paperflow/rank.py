from __future__ import annotations

import math
import re
from dataclasses import replace

from paperflow.models import Paper, RankedPaper


def rank_papers(
    papers: list[Paper],
    *,
    keywords: dict[str, int],
    categories: tuple[str, ...],
) -> list[RankedPaper]:
    ranked: list[RankedPaper] = []
    for paper in papers:
        normalized_upvotes = max(0, paper.hf_upvotes)
        normalized_paper = replace(paper, hf_upvotes=normalized_upvotes)
        title = paper.title.casefold()
        abstract = paper.abstract.casefold()
        matched: list[str] = []
        score = 0
        for keyword, weight in keywords.items():
            key = keyword.casefold()
            pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(key)}(?![a-z0-9_])")
            if pattern.search(title):
                score += weight * 10
                matched.append(key)
            elif pattern.search(abstract):
                score += weight * 3
                matched.append(key)
        if paper.primary_category in categories:
            score += 5
        score += min(5, int(math.log2(normalized_upvotes + 1)))
        ranked.append(RankedPaper(normalized_paper, score, tuple(sorted(set(matched)))))
    return sorted(
        ranked,
        key=lambda item: (
            -item.score,
            item.paper.published,
            -item.paper.hf_upvotes,
            item.paper.arxiv_id,
        ),
    )
