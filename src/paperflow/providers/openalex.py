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

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def _parse(payload: str) -> list[ResearchItem]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict) or not isinstance(decoded.get("results"), list):
        raise ValueError("invalid OpenAlex response")
    items: list[ResearchItem] = []
    for row in decoded["results"]:
        if not isinstance(row, dict):
            continue
        title = plain_text(row.get("title"))
        openalex_url = plain_text(row.get("id"))
        if not title or not openalex_url:
            continue
        external_id = openalex_url.rstrip("/").rsplit("/", 1)[-1]
        published = plain_text(row.get("publication_date"))
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else 0
        location = row.get("primary_location") if isinstance(row.get("primary_location"), dict) else {}
        url = plain_text(location.get("landing_page_url")) or openalex_url
        authorships = row.get("authorships", [])
        authors = tuple(
            name for entry in authorships if isinstance(entry, dict)
            and isinstance(entry.get("author"), dict)
            if (name := plain_text(entry["author"].get("display_name")))
        ) if isinstance(authorships, list) else ()
        concepts = row.get("concepts", [])
        subjects = tuple(
            name for concept in concepts if isinstance(concept, dict)
            if (name := plain_text(concept.get("display_name")))
        ) if isinstance(concepts, list) else ()
        items.append(ResearchItem(
            key=external_id, doi=plain_text(row.get("doi")), arxiv_id="", title=title,
            authors=authors, abstract="", published=published, year=year, url=url,
            pdf_url=plain_text(location.get("pdf_url")), subjects=subjects,
            sources=(SourceRecord("openalex", url, external_id),),
        ))
    return deduplicate_research_items(items)


def collect_openalex(client: httpx.Client, profile: DomainProfile, *, now: datetime) -> ProviderBatch:
    if "openalex" not in profile.providers:
        return ProviderBatch((), ProviderStatus("openalex", "skipped", 0))
    since = (now - timedelta(hours=profile.lookback_hours)).date().isoformat()
    encoded = urllib.parse.urlencode({"search": " OR ".join(profile.query_seeds), "filter": f"from_publication_date:{since}", "per-page": min(100, profile.candidate_limit)})
    try:
        items = tuple(_parse(request_with_retry(client, f"{OPENALEX_WORKS_URL}?{encoded}").text)[: profile.candidate_limit])
    except Exception as exc:
        return ProviderBatch((), ProviderStatus("openalex", "failed", 0, safe_error(exc)))
    return ProviderBatch(items, ProviderStatus("openalex", "ok", len(items)))
