from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from paperflow.fetch import request_with_retry
from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id, normalize_utc_date


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    authors = []
    for author in value:
        if not isinstance(author, dict):
            continue
        name = _clean(author.get("name"))
        if name:
            authors.append(name)
    return tuple(authors)


def _upvotes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def parse_hf_payload(payload: str, source: str) -> list[Paper]:
    decoded: Any = json.loads(payload)
    if not isinstance(decoded, list):
        raise ValueError("Hugging Face payload must be a list")

    result: list[Paper] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        data = item.get("paper", item)
        if not isinstance(data, dict):
            continue
        try:
            arxiv_id = canonical_arxiv_id(_clean(data.get("id")))
        except ValueError:
            continue

        upvotes = _upvotes(data.get("upvotes"))
        if upvotes is None:
            upvotes = _upvotes(item.get("upvotes"))
        result.append(
            Paper(
                arxiv_id=arxiv_id,
                title=_clean(data.get("title")),
                authors=_authors(data.get("authors")),
                abstract=_clean(data.get("summary")),
                primary_category=_clean(data.get("primaryCategory")),
                published=normalize_utc_date(data.get("publishedAt")),
                sources=(source,),
                hf_upvotes=upvotes if upvotes is not None else 0,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            )
        )
    return result


def _filter_target_date(papers: list[Paper], target_date: date) -> list[Paper]:
    expected_date = target_date.isoformat()
    return [paper for paper in papers if paper.published == expected_date]


def fetch_hf_daily(client: httpx.Client, target_date: date) -> list[Paper]:
    url = (
        "https://huggingface.co/api/daily_papers"
        f"?date={target_date.isoformat()}&limit=100"
    )
    papers = parse_hf_payload(request_with_retry(client, url).text, "hf-daily")
    return _filter_target_date(papers, target_date)


def fetch_hf_trending(client: httpx.Client, target_date: date) -> list[Paper]:
    url = "https://huggingface.co/api/daily_papers?sort=trending&limit=50"
    papers = parse_hf_payload(request_with_retry(client, url).text, "hf-trending")
    return _filter_target_date(papers, target_date)
