from __future__ import annotations

import copy
import json
from pathlib import Path

from paperflow.errors import ConfigError

_TOP_FIELDS = {"schema_version", "run_id", "domain", "generated_at", "coverage", "additional_queries", "selected", "themes", "disagreements", "policy_industry_links", "actions", "source_limitations", "unresolved_questions"}
_SELECTED_FIELDS = {"candidate_id", "analysis_depth", "relevance", "novelty", "evidence_quality", "industrial_value", "confidence", "reason", "method", "evidence", "limitations", "practical_implications", "citations"}
_SELECTED_EVIDENCE_FIELDS = {"access_status", "full_text_file", "figures"}
_FIGURE_FIELDS = {"file", "figure", "page", "caption", "source_url", "license"}
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
    normalized = copy.deepcopy(analysis)
    selected = normalized.get("selected")
    if not isinstance(selected, list) or len(selected) > len(context["candidates"]):
        raise ConfigError("analysis selection is invalid")
    candidates = {item.get("key"): item for item in context["candidates"] if isinstance(item, dict)}
    seen: set[str] = set()
    for item in selected:
        if not isinstance(item, dict) or not _SELECTED_FIELDS.issubset(item) or not set(item) <= _SELECTED_FIELDS | _SELECTED_EVIDENCE_FIELDS:
            raise ConfigError("analysis fields are invalid")
        item.setdefault("access_status", "abstract_only")
        item.setdefault("full_text_file", "")
        item.setdefault("figures", [])
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
        access_status = item.get("access_status")
        if access_status not in {"abstract_only", "open_access", "institutional"}:
            raise ConfigError("analysis access status is invalid")
        full_text_file = item.get("full_text_file")
        figures = item.get("figures")
        if not isinstance(full_text_file, str) or not isinstance(figures, list):
            raise ConfigError("analysis evidence fields are invalid")
        if item["analysis_depth"] == "full_text":
            if access_status == "abstract_only" or not _evidence_file(context_path, full_text_file, {".pdf"}):
                raise ConfigError("full-text evidence is invalid")
        elif full_text_file or figures:
            raise ConfigError("figures require full-text analysis")
        if len(figures) > 3:
            raise ConfigError("analysis supports at most three figures")
        for figure in figures:
            if not isinstance(figure, dict) or set(figure) != _FIGURE_FIELDS:
                raise ConfigError("analysis figure evidence is invalid")
            if not _evidence_file(context_path, figure.get("file"), {".png", ".jpg", ".jpeg"}):
                raise ConfigError("analysis figure evidence is invalid")
            if type(figure.get("page")) is not int or figure["page"] < 1:
                raise ConfigError("analysis figure evidence is invalid")
            if any(not isinstance(figure.get(field), str) or not figure[field].strip() for field in ("figure", "caption", "license")):
                raise ConfigError("analysis figure evidence is invalid")
            if figure.get("source_url") not in allowed_urls:
                raise ConfigError("analysis figure source URL is not present in the research context")
    return normalized


def _evidence_file(context_path: Path, relative: object, suffixes: set[str]) -> bool:
    if not isinstance(relative, str) or not relative.strip():
        return False
    run_dir = Path(context_path).parent.resolve()
    candidate = run_dir / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(run_dir)
    except (OSError, ValueError):
        return False
    return resolved.is_file() and resolved.suffix.lower() in suffixes


__all__ = ["validate_analysis", "_load_context"]
