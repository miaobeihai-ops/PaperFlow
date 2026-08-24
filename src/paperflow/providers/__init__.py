from __future__ import annotations

from paperflow.providers.arxiv import collect_arxiv
from paperflow.providers.crossref import collect_crossref
from paperflow.providers.feed import collect_feed
from paperflow.providers.huggingface import collect_huggingface
from paperflow.providers.openalex import collect_openalex

PROVIDERS = {
    "arxiv": collect_arxiv,
    "huggingface": collect_huggingface,
    "crossref": collect_crossref,
    "openalex": collect_openalex,
    "feed": collect_feed,
}

__all__ = ["PROVIDERS"]
