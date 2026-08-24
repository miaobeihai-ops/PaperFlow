from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

from paperflow.report import escape_markdown_block, escape_markdown_text
from paperflow.research_analysis import _load_context, validate_analysis
from paperflow.research_html import render_research_html


@dataclass(frozen=True)
class FinalizedResearch:
    markdown_path: Path
    json_path: Path
    html_path: Path
    domain: str
    local_date: str
    selected_count: int


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _search_window_text(context: dict) -> str:
    window = context.get("search_window")
    timezone = "Asia/Hong_Kong"
    if isinstance(window, dict):
        started_at = window.get("started_at")
        ended_at = window.get("ended_at")
        timezone = window.get("timezone", timezone)
    else:
        ended = datetime.fromisoformat(context["generated_at"].replace("Z", "+00:00"))
        zone = ZoneInfo(timezone)
        ended = ended.astimezone(zone)
        started = ended - timedelta(hours=context["profile"]["lookback_hours"])
        started_at = started.isoformat()
        ended_at = ended.isoformat()
    started = datetime.fromisoformat(started_at).strftime("%Y-%m-%d %H:%M")
    ended = datetime.fromisoformat(ended_at).strftime("%Y-%m-%d %H:%M")
    return f"{started} - {ended} ({timezone})"


def _safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _copy_figures(context_path: Path, context: dict, analysis: dict, directory: Path) -> dict[tuple[str, int], str]:
    copied: dict[tuple[str, int], str] = {}
    asset_directory = directory / "assets" / context["local_date"]
    for selected in analysis["selected"]:
        for index, figure in enumerate(selected["figures"], 1):
            source = context_path.parent / figure["file"]
            filename = f"{_safe_slug(selected['candidate_id'])}-figure-{index}{source.suffix.lower()}"
            destination = asset_directory / filename
            _atomic_write(destination, source.read_bytes())
            copied[(selected["candidate_id"], index)] = f"assets/{context['local_date']}/{filename}"
    return copied


def _render(context: dict, analysis: dict, figure_paths: dict[tuple[str, int], str]) -> str:
    candidates = {item["key"]: item for item in context["candidates"]}
    lines = ["---", f"domain: {json.dumps(context['domain'], ensure_ascii=False)}", f"date: {json.dumps(context['local_date'])}", f"run_id: {json.dumps(context['run_id'])}", "---", "", f"# {escape_markdown_text(context['profile']['display_name'])}", "", "## 检索时段与来源", "", f"- 检索时段: {_search_window_text(context)}", f"- 覆盖说明: {escape_markdown_block(analysis['coverage'])}", ""]
    for index, selected in enumerate(analysis["selected"], 1):
        source = candidates[selected["candidate_id"]]
        lines.extend([f"## {index}. {escape_markdown_text(source['title'])}", "", f"- ID: `{escape_markdown_text(source['key'])}`", f"- 作者: {', '.join(escape_markdown_text(value) for value in source['authors'])}", f"- 日期: {escape_markdown_text(source['published'])}", f"- 原文: {source['url']}", f"- 阅读: `{selected['analysis_depth']}` / `{selected['access_status']}`", f"- 证据边界: {escape_markdown_block(selected['limitations'])}", f"- 置信度: `{selected['confidence']}`", f"- 评分: 相关性 {selected['relevance']}/10；新颖性 {selected['novelty']}/10；证据 {selected['evidence_quality']}/10；产业价值 {selected['industrial_value']}/10", "", f"**入选理由：** {escape_markdown_block(selected['reason'])}", "", f"**方法：** {escape_markdown_block(selected['method'])}", "", f"**证据：** {escape_markdown_block(selected['evidence'])}", "", f"**实际启示：** {escape_markdown_block(selected['practical_implications'])}", ""])
        for figure_index, figure in enumerate(selected["figures"], 1):
            path = figure_paths[(selected["candidate_id"], figure_index)]
            alt = f"{figure['figure']} - {figure['caption']}"
            lines.extend([f"![{escape_markdown_text(alt)}]({path})", "", f"图注: {escape_markdown_block(figure['figure'])}；PDF第{figure['page']}页；{escape_markdown_block(figure['license'])}；{figure['source_url']}", ""])
    sections = [("跨论文主题", "themes"), ("分歧", "disagreements"), ("政策与产业联系", "policy_industry_links"), ("待解决问题", "unresolved_questions")]
    for title, field in sections:
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {escape_markdown_block(value)}" for value in analysis[field])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def finalize_research(context_path: Path, analysis: dict | Path, *, home: Path) -> FinalizedResearch:
    context_path = Path(context_path)
    context = _load_context(context_path)
    if isinstance(analysis, Path):
        analysis = json.loads(analysis.read_text(encoding="utf-8"))
    validated = validate_analysis(context_path, analysis)
    report = {"schema_version": 1, "context": context, "analysis": validated}
    directory = Path(home) / "reports" / context["domain"]
    markdown_path = directory / f"{context['local_date']}.md"
    json_path = directory / f"{context['local_date']}.json"
    html_path = directory / f"{context['local_date']}.html"
    figure_paths = _copy_figures(context_path, context, validated, directory)
    _atomic_write(json_path, (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    _atomic_write(markdown_path, _render(context, validated, figure_paths).encode("utf-8"))
    html = render_research_html(
        context,
        validated,
        search_window=_search_window_text(context),
        figure_paths=figure_paths,
        report_directory=directory,
    )
    _atomic_write(html_path, html.encode("utf-8"))
    return FinalizedResearch(markdown_path, json_path, html_path, context["domain"], context["local_date"], len(validated["selected"]))
