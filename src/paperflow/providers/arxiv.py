from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from paperflow.arxiv_source import search_arxiv
from paperflow.domain import DomainProfile
from paperflow.models import Paper
from paperflow.research_dedupe import deduplicate_research_items
from paperflow.research_models import (
    ProviderBatch,
    ProviderStatus,
    ResearchItem,
    SourceRecord,
)


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return "network error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "request failed"
    if isinstance(exc, ValueError):
        return "invalid response"
    return "provider error"


def _adapt(paper: Paper) -> ResearchItem:
    year = int(paper.published[:4]) if len(paper.published) >= 4 and paper.published[:4].isdigit() else 0
    subjects = (paper.primary_category,) if paper.primary_category else ()
    return ResearchItem(
        key=f"arxiv:{paper.arxiv_id}",
        doi="",
        arxiv_id=paper.arxiv_id,
        title=paper.title,
        authors=paper.authors,
        abstract=paper.abstract,
        published=paper.published,
        year=year,
        url=paper.url,
        pdf_url=paper.pdf_url,
        subjects=subjects,
        sources=(SourceRecord("arxiv", paper.url, paper.arxiv_id),),
    )


def collect_arxiv(
    client: httpx.Client,
    profile: DomainProfile,
    *,
    now: datetime,
) -> ProviderBatch:
    if "arxiv" not in profile.providers:
        return ProviderBatch((), ProviderStatus("arxiv", "skipped", 0))

    since = (now - timedelta(hours=profile.lookback_hours)).date()
    items: list[ResearchItem] = []
    errors: list[str] = []
    for query in profile.query_seeds:
        remaining = profile.candidate_limit - len(items)
        if remaining <= 0:
            break
        try:
            papers = search_arxiv(
                client,
                query,
                max_results=min(100, remaining),
                categories=profile.arxiv_categories,
                since=since,
                sort="newest",
            )
            items.extend(_adapt(paper) for paper in papers)
            items = deduplicate_research_items(items)
        except Exception as exc:
            errors.append(_safe_error(exc))

    bounded = tuple(items[: profile.candidate_limit])
    if errors and bounded:
        state = "partial"
    elif errors:
        state = "failed"
    else:
        state = "ok"
    return ProviderBatch(
        bounded,
        ProviderStatus("arxiv", state, len(bounded), errors[0] if errors else ""),
    )
