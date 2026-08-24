from __future__ import annotations

import json
from pathlib import Path

from paperflow.errors import ConfigError

_TOP_FIELDS = {"schema_version", "run_id", "domain", "generated_at", "coverage", "additional_queries", "selected", "themes", "disagreements", "policy_industry_links", "actions", "source_limitations", "unresolved_questions"}
_SELECTED_FIELDS = {"candidate_id", "analysis_depth", "relevance", "novelty", "evidence_quality", "industrial_value", "confidence", "reason", "method", "evidence", "limitations", "practical_implications", "citations"}
_TEXT_FIELDS = {"reason", "method", "evidence", "limitations", "practical_implications"}
_LIST_FIELDS = {"additional_queries", "themes", "disagreements", "policy_industry_links", "actions", "source_limitations", "unresolved_questions"}


def _load_context(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("context file is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("candidates"), list):
        raise ConfigError("context file is invalid")
    return payload


def validate_analysis(context_path: Path, analysis: dict) -> dict:
    context = _load_context(context_path)
    if not isinstance(analysis, dict) or set(analysis) != _TOP_FIELDS:
        raise ConfigError("analysis fields are invalid")
    if analysis.get("schema_version") != 1 or analysis.get("run_id") != context.get("run_id") or analysis.get("domain") != context.get("domain"):
        raise ConfigError("analysis run identity mismatch")
    for field in ("generated_at", "coverage"):
        if not isinstance(analysis.get(field), str) or not analysis[field].strip():
            raise ConfigError("analysis text fields are invalid")
    for field in _LIST_FIELDS:
        value = analysis.get(field)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ConfigError("analysis list fields are invalid")
    selected = analysis.get("selected")
    if not isinstance(selected, list) or len(selected) > len(context["candidates"]):
        raise ConfigError("analysis selection is invalid")
    candidates = {item.get("key"): item for item in context["candidates"] if isinstance(item, dict)}
    seen: set[str] = set()
    for item in selected:
        if not isinstance(item, dict) or set(item) != _SELECTED_FIELDS:
            raise ConfigError("analysis fields are invalid")
        candidate_id = item.get("candidate_id")
        if candidate_id not in candidates or candidate_id in seen:
            raise ConfigError("analysis references an unknown candidate")
        seen.add(candidate_id)
        if item.get("analysis_depth") not in {"abstract", "full_text"} or item.get("confidence") not in {"low", "medium", "high"}:
            raise ConfigError("analysis rating fields are invalid")
        if any(type(item.get(field)) is not int or not 0 <= item[field] <= 10 for field in ("relevance", "novelty", "evidence_quality", "industrial_value")):
            raise ConfigError("analysis scores must be integers from 0 to 10")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in _TEXT_FIELDS):
            raise ConfigError("analysis text fields are invalid")
        citations = item.get("citations")
        if not isinstance(citations, list):
            raise ConfigError("analysis citations are invalid")
        allowed_urls = {candidates[candidate_id].get("url"), candidates[candidate_id].get("pdf_url")}
        allowed_urls.update(source.get("url") for source in candidates[candidate_id].get("sources", []) if isinstance(source, dict))
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"candidate_id", "url"} or citation.get("candidate_id") != candidate_id:
                raise ConfigError("analysis citations are invalid")
            if citation.get("url") not in allowed_urls:
                raise ConfigError("citation URL is not present in the research context")
    return analysis


__all__ = ["validate_analysis", "_load_context"]
