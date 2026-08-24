from __future__ import annotations

from paperflow.providers.arxiv import collect_arxiv
from paperflow.providers.huggingface import collect_huggingface

PROVIDERS = {
    "arxiv": collect_arxiv,
    "huggingface": collect_huggingface,
}

__all__ = ["PROVIDERS", "collect_arxiv", "collect_huggingface"]
