from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from paperflow.normalize import canonical_arxiv_id
from paperflow.research_models import ResearchItem, SourceRecord

_DOI_RE = re.compile(r"10\.\d+/\S+", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_SOURCE_PRIORITY = {
    "arxiv": 0,
    "crossref": 1,
    "openalex": 2,
    "hf-daily": 3,
    "hf-trending": 4,
    "feed": 5,
    "rss": 5,
}
_METADATA_FIELDS = (
    "title",
    "authors",
    "abstract",
    "published",
    "year",
    "url",
    "pdf_url",
    "subjects",
)


def _normalize_doi(value: str) -> str:
    cleaned = _DOI_PREFIX_RE.sub("", value.strip()).casefold().rstrip(".,;)")
    return cleaned if _DOI_RE.fullmatch(cleaned) else ""


def _normalize_arxiv_id(value: str) -> str:
    try:
        return canonical_arxiv_id(value)
    except (TypeError, ValueError):
        return ""


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    words = "".join(character if character.isalnum() else " " for character in normalized)
    return " ".join(words.split())


def _has_value(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, tuple):
        return bool(value)
    return bool(value)


def _candidate_key(item: ResearchItem) -> tuple[object, ...]:
    priority = min(
        (_SOURCE_PRIORITY.get(source.name.casefold(), len(_SOURCE_PRIORITY)) for source in item.sources),
        default=len(_SOURCE_PRIORITY),
    )
    completeness = sum(_has_value(getattr(item, field)) for field in _METADATA_FIELDS)
    return (
        priority,
        -completeness,
        item.title.casefold(),
        item.title,
        item.published,
        item.url,
        item.key,
    )


def _identity_aliases(item: ResearchItem) -> tuple[str, ...]:
    aliases: list[str] = []
    if item.doi:
        aliases.append(f"doi:{item.doi}")
    if item.arxiv_id:
        aliases.append(f"arxiv:{item.arxiv_id}")
    if aliases:
        return tuple(aliases)
    title = _normalize_title(item.title)
    if title:
        return (f"title:{item.year}:{title}",)
    return (f"source:{item.key}",)


def _normalize_item(item: ResearchItem) -> ResearchItem:
    return replace(
        item,
        doi=_normalize_doi(item.doi),
        arxiv_id=_normalize_arxiv_id(item.arxiv_id),
        authors=tuple(author.strip() for author in item.authors if author.strip()),
        subjects=tuple(sorted({subject.strip() for subject in item.subjects if subject.strip()})),
        sources=tuple(sorted(set(item.sources), key=lambda source: (source.name, source.url, source.external_id))),
    )


def _merge_group(candidates: list[ResearchItem]) -> ResearchItem:
    ordered = sorted(candidates, key=_candidate_key)
    dois = sorted({candidate.doi for candidate in ordered if candidate.doi})
    arxiv_ids = sorted({candidate.arxiv_id for candidate in ordered if candidate.arxiv_id})
    values: dict[str, object] = {}
    for field in _METADATA_FIELDS:
        for candidate in ordered:
            value = getattr(candidate, field)
            if _has_value(value):
                values[field] = value
                break

    doi = dois[0] if dois else ""
    arxiv_id = arxiv_ids[0] if arxiv_ids else ""
    if doi:
        key = f"doi:{doi}"
    elif arxiv_id:
        key = f"arxiv:{arxiv_id}"
    else:
        year = int(values.get("year", ordered[0].year))
        key = f"title:{year}:{_normalize_title(str(values.get('title', ordered[0].title)))}"

    sources = tuple(
        sorted(
            {source for candidate in ordered for source in candidate.sources},
            key=lambda source: (source.name, source.url, source.external_id),
        )
    )
    return replace(
        ordered[0],
        **values,
        key=key,
        doi=doi,
        arxiv_id=arxiv_id,
        sources=sources,
    )


def deduplicate_research_items(items: list[ResearchItem]) -> list[ResearchItem]:
    """Merge records by DOI, then arXiv id, then normalized title and year."""

    normalized = [_normalize_item(item) for item in items]
    parents = list(range(len(normalized)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    owner_by_alias: dict[str, int] = {}
    for index, item in enumerate(normalized):
        for alias in _identity_aliases(item):
            owner = owner_by_alias.setdefault(alias, index)
            union(index, owner)

    grouped: dict[int, list[ResearchItem]] = {}
    for index, item in enumerate(normalized):
        grouped.setdefault(find(index), []).append(item)

    merged = [_merge_group(group) for group in grouped.values()]
    merged.sort(key=lambda item: (item.title.casefold(), item.title, item.key))
    merged.sort(key=lambda item: item.published, reverse=True)
    return merged
