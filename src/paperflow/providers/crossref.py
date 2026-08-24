from __future__ import annotations

from datetime import datetime, timedelta
import json
import urllib.parse

import httpx

from paperflow.domain import DomainProfile
from paperflow.fetch import request_with_retry
from paperflow.providers._shared import plain_text, safe_error
from paperflow.research_dedupe import deduplicate_research_items
from paperflow.research_models import ProviderBatch, ProviderStatus, ResearchItem, SourceRecord

CROSSREF_WORKS_URL = "https://api.crossref.org/works"


def _date(value: object) -> tuple[str, int]:
    if not isinstance(value, dict):
        return "", 0
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return "", 0
    try:
        numbers = [int(part) for part in parts[0][:3]]
        year = numbers[0]
        numbers.extend([1] * (3 - len(numbers)))
        return f"{numbers[0]:04d}-{numbers[1]:02d}-{numbers[2]:02d}", year
    except (TypeError, ValueError, OverflowError):
        return "", 0


def _parse(payload: str) -> list[ResearchItem]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("message"), dict):
        raise ValueError("invalid Crossref response")
    rows = decoded["message"].get("items")
    if not isinstance(rows, list):
        raise ValueError("invalid Crossref response")
    result: list[ResearchItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        doi = plain_text(row.get("DOI"))
        titles = row.get("title")
        title = plain_text(titles[0]) if isinstance(titles, list) and titles else ""
        if not doi or not title:
            continue
        authors = tuple(
            name
            for author in row.get("author", []) if isinstance(author, dict)
            if (name := plain_text(f"{author.get('given', '')} {author.get('family', '')}"))
        ) if isinstance(row.get("author", []), list) else ()
        published, year = _date(row.get("published"))
        url = plain_text(row.get("URL")) or f"https://doi.org/{doi}"
        subjects = tuple(plain_text(value) for value in row.get("subject", []) if plain_text(value)) if isinstance(row.get("subject", []), list) else ()
        result.append(ResearchItem(
            key=f"doi:{doi}", doi=doi, arxiv_id="", title=title, authors=authors,
            abstract=plain_text(row.get("abstract")), published=published, year=year,
            url=url, pdf_url="", subjects=subjects,
            sources=(SourceRecord("crossref", url, doi),),
        ))
    return deduplicate_research_items(result)


def collect_crossref(client: httpx.Client, profile: DomainProfile, *, now: datetime) -> ProviderBatch:
    if "crossref" not in profile.providers:
        return ProviderBatch((), ProviderStatus("crossref", "skipped", 0))
    query = " OR ".join(profile.query_seeds)
    since = (now - timedelta(hours=profile.lookback_hours)).date().isoformat()
    until = now.date().isoformat()
    encoded = urllib.parse.urlencode({"query": query, "filter": f"from-pub-date:{since},until-pub-date:{until}", "rows": min(100, profile.candidate_limit), "sort": "published", "order": "desc"})
    try:
        parsed = _parse(request_with_retry(client, f"{CROSSREF_WORKS_URL}?{encoded}").text)
        items = tuple(item for item in parsed if since <= item.published <= until)[: profile.candidate_limit]
    except Exception as exc:
        return ProviderBatch((), ProviderStatus("crossref", "failed", 0, safe_error(exc)))
    return ProviderBatch(items, ProviderStatus("crossref", "ok", len(items)))
