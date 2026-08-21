from __future__ import annotations

import math

from paperflow.models import Paper, RankedPaper


def rank_papers(
    papers: list[Paper],
    *,
    keywords: dict[str, int],
    categories: tuple[str, ...],
) -> list[RankedPaper]:
    ranked: list[RankedPaper] = []
    for paper in papers:
        title = paper.title.casefold()
        abstract = paper.abstract.casefold()
        matched: list[str] = []
        score = 0
        for keyword, weight in keywords.items():
            key = keyword.casefold()
            if key in title:
                score += weight * 10
                matched.append(key)
            elif key in abstract:
                score += weight * 3
                matched.append(key)
        if paper.primary_category in categories:
            score += 5
        score += min(5, int(math.log2(max(0, paper.hf_upvotes) + 1)))
        ranked.append(RankedPaper(paper, score, tuple(sorted(set(matched)))))
    return sorted(
        ranked,
        key=lambda item: (
            -item.score,
            item.paper.published,
            -item.paper.hf_upvotes,
            item.paper.arxiv_id,
        ),
    )
