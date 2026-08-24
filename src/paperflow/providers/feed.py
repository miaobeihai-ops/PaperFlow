from __future__ import annotations

from datetime import datetime
import xml.etree.ElementTree as ET

import httpx

from paperflow.domain import DomainProfile
from paperflow.fetch import request_with_retry
from paperflow.normalize import normalize_utc_date
from paperflow.providers._shared import plain_text, safe_error
from paperflow.research_dedupe import deduplicate_research_items
from paperflow.research_models import ProviderBatch, ProviderStatus, ResearchItem, SourceRecord


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _child_text(node: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in node:
        if _local(child.tag) in wanted:
            return plain_text("".join(child.itertext()))
    return ""


def _parse(payload: str, feed_url: str) -> list[ResearchItem]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("invalid feed response") from exc
    root_name = _local(root.tag)
    if root_name == "feed":
        entries = [node for node in root if _local(node.tag) == "entry"]
    elif root_name == "rss":
        entries = [node for node in root.iter() if _local(node.tag) == "item"]
    else:
        raise ValueError("invalid feed response")
    items: list[ResearchItem] = []
    for entry in entries:
        title = _child_text(entry, "title")
        external_id = _child_text(entry, "id", "guid")
        link = _child_text(entry, "link")
        if not link:
            for child in entry:
                if _local(child.tag) == "link" and child.attrib.get("href"):
                    link = plain_text(child.attrib["href"])
                    break
        if not title or not link:
            continue
        published = normalize_utc_date(_child_text(entry, "published", "updated", "pubdate"))
        year = int(published[:4]) if published[:4].isdigit() else 0
        authors = (_child_text(entry, "author", "creator"),)
        authors = tuple(value for value in authors if value)
        subjects = tuple(
            plain_text(child.attrib.get("term") or "".join(child.itertext()))
            for child in entry if _local(child.tag) in {"category", "subject"}
            and plain_text(child.attrib.get("term") or "".join(child.itertext()))
        )
        items.append(ResearchItem(
            key=external_id or link, doi="", arxiv_id="", title=title, authors=authors,
            abstract=_child_text(entry, "summary", "description", "content"),
            published=published, year=year, url=link, pdf_url="", subjects=subjects,
            sources=(SourceRecord("feed", link, external_id or feed_url),),
        ))
    return items


def collect_feed(client: httpx.Client, profile: DomainProfile, *, now: datetime) -> ProviderBatch:
    del now
    if "feed" not in profile.providers:
        return ProviderBatch((), ProviderStatus("feed", "skipped", 0))
    items: list[ResearchItem] = []
    errors: list[str] = []
    for url in profile.feeds[:20]:
        try:
            items.extend(_parse(request_with_retry(client, url).text, url))
        except Exception as exc:
            errors.append(safe_error(exc))
    merged = tuple(deduplicate_research_items(items)[: profile.candidate_limit])
    state = "partial" if errors and merged else "failed" if errors else "ok"
    return ProviderBatch(merged, ProviderStatus("feed", state, len(merged), errors[0] if errors else ""))
