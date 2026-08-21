from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from paperflow.fetch import request_with_retry
from paperflow.models import Paper
from paperflow.normalize import canonical_arxiv_id

ATOM = "http://www.w3.org/2005/Atom"
ARXIV = "http://arxiv.org/schemas/atom"


class ArxivPaperNotFound(ValueError):
    pass


class ArxivResponseError(ValueError):
    def __init__(self) -> None:
        super().__init__("arXiv response was invalid")


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _text(node: ET.Element, name: str) -> str:
    child = node.find(f"{{{ATOM}}}{name}")
    if child is None:
        return ""
    return _clean("".join(child.itertext()))


def parse_arxiv_feed(xml: str) -> list[Paper]:
    result: list[Paper] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ArxivResponseError() from exc
    if root.tag != f"{{{ATOM}}}feed":
        raise ArxivResponseError()

    for entry in root.findall(f"{{{ATOM}}}entry"):
        try:
            arxiv_id = canonical_arxiv_id(_text(entry, "id"))
        except ValueError:
            continue
        category = entry.find(f"{{{ARXIV}}}primary_category")
        authors = tuple(
            name
            for author in entry.findall(f"{{{ATOM}}}author")
            if (name := _text(author, "name"))
        )
        result.append(
            Paper(
                arxiv_id=arxiv_id,
                title=_text(entry, "title"),
                authors=authors,
                abstract=_text(entry, "summary"),
                primary_category=(
                    _clean(category.attrib.get("term"))
                    if category is not None
                    else ""
                ),
                published=_text(entry, "published")[:10],
                sources=("arxiv",),
                hf_upvotes=0,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            )
        )
    return result


def fetch_arxiv(
    client: httpx.Client,
    target_date: date,
    categories: tuple[str, ...],
) -> list[Paper]:
    del target_date
    search_query = " OR ".join(f"cat:{category}" for category in categories)
    query = urllib.parse.urlencode(
        {
            "search_query": search_query,
            "start": 0,
            "max_results": 100,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{query}"
    return parse_arxiv_feed(request_with_retry(client, url).text)


def _validate_max_results(max_results: int) -> None:
    if type(max_results) is not int:
        raise TypeError("max_results must be an integer")
    if not 1 <= max_results <= 100:
        raise ValueError("max_results must be between 1 and 100")


def _literal_search_query(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'all:"{escaped}"'


def search_arxiv(
    client: httpx.Client,
    query: str,
    max_results: int = 20,
) -> list[Paper]:
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("query must not be blank")
    _validate_max_results(max_results)
    encoded = urllib.parse.urlencode(
        {
            "search_query": _literal_search_query(cleaned),
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{encoded}"
    return parse_arxiv_feed(request_with_retry(client, url).text)


def fetch_arxiv_by_id(client: httpx.Client, value: str) -> Paper:
    arxiv_id = canonical_arxiv_id(value)
    encoded = urllib.parse.urlencode(
        {"id_list": arxiv_id, "start": 0, "max_results": 1}
    )
    url = f"https://export.arxiv.org/api/query?{encoded}"
    papers = parse_arxiv_feed(request_with_retry(client, url).text)
    for paper in papers:
        if canonical_arxiv_id(paper.arxiv_id) == arxiv_id:
            return paper
    raise ArxivPaperNotFound("paper was not found")
