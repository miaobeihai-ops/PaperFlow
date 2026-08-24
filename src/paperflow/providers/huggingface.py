from __future__ import annotations

from datetime import datetime

import httpx

from paperflow.domain import DomainProfile
from paperflow.hf_source import fetch_hf_daily, fetch_hf_trending
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
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid response"
    return "provider error"


def _adapt(paper: Paper) -> ResearchItem:
    year = int(paper.published[:4]) if len(paper.published) >= 4 and paper.published[:4].isdigit() else 0
    subjects = (paper.primary_category,) if paper.primary_category else ()
    source = paper.sources[0] if paper.sources else "huggingface"
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
        sources=(SourceRecord(source, paper.url, paper.arxiv_id),),
    )


def collect_huggingface(
    client: httpx.Client,
    profile: DomainProfile,
    *,
    now: datetime,
) -> ProviderBatch:
    if "huggingface" not in profile.providers:
        return ProviderBatch((), ProviderStatus("huggingface", "skipped", 0))

    papers: list[Paper] = []
    errors: list[str] = []
    for fetcher in (fetch_hf_daily, fetch_hf_trending):
        try:
            papers.extend(fetcher(client, now.date()))
        except Exception as exc:
            errors.append(_safe_error(exc))

    items = deduplicate_research_items([_adapt(paper) for paper in papers])
    bounded = tuple(items[: profile.candidate_limit])
    if errors and bounded:
        state = "partial"
    elif errors:
        state = "failed"
    else:
        state = "ok"
    return ProviderBatch(
        bounded,
        ProviderStatus(
            "huggingface", state, len(bounded), errors[0] if errors else ""
        ),
    )
