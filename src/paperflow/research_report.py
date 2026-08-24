from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from paperflow.report import escape_markdown_block, escape_markdown_text
from paperflow.research_analysis import _load_context, validate_analysis


@dataclass(frozen=True)
class FinalizedResearch:
    markdown_path: Path
    json_path: Path
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


def _render(context: dict, analysis: dict) -> str:
    candidates = {item["key"]: item for item in context["candidates"]}
    lines = ["---", f"domain: {json.dumps(context['domain'], ensure_ascii=False)}", f"date: {json.dumps(context['local_date'])}", f"run_id: {json.dumps(context['run_id'])}", "---", "", f"# {escape_markdown_text(context['profile']['display_name'])}", "", "## 覆盖情况", "", escape_markdown_block(analysis["coverage"]), ""]
    for index, selected in enumerate(analysis["selected"], 1):
        source = candidates[selected["candidate_id"]]
        lines.extend([f"## {index}. {escape_markdown_text(source['title'])}", "", f"- ID: `{escape_markdown_text(source['key'])}`", f"- 作者: {', '.join(escape_markdown_text(value) for value in source['authors'])}", f"- 日期: {escape_markdown_text(source['published'])}", f"- 分析深度: `{selected['analysis_depth']}`", f"- 置信度: `{selected['confidence']}`", f"- 评分: 相关性 {selected['relevance']}/10；新颖性 {selected['novelty']}/10；证据 {selected['evidence_quality']}/10；产业价值 {selected['industrial_value']}/10", "", f"**入选理由：** {escape_markdown_block(selected['reason'])}", "", f"**方法：** {escape_markdown_block(selected['method'])}", "", f"**证据：** {escape_markdown_block(selected['evidence'])}", "", f"**局限：** {escape_markdown_block(selected['limitations'])}", "", f"**实际启示：** {escape_markdown_block(selected['practical_implications'])}", "", f"- 来源: {source['url']}", ""])
    sections = [("跨论文主题", "themes"), ("分歧", "disagreements"), ("政策与产业联系", "policy_industry_links"), ("建议行动", "actions"), ("来源局限", "source_limitations"), ("待解决问题", "unresolved_questions")]
    for title, field in sections:
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {escape_markdown_block(value)}" for value in analysis[field])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def finalize_research(context_path: Path, analysis: dict | Path, *, home: Path) -> FinalizedResearch:
    context = _load_context(context_path)
    if isinstance(analysis, Path):
        analysis = json.loads(analysis.read_text(encoding="utf-8"))
    validated = validate_analysis(context_path, analysis)
    report = {"schema_version": 1, "context": context, "analysis": validated}
    directory = Path(home) / "reports" / context["domain"]
    markdown_path = directory / f"{context['local_date']}.md"
    json_path = directory / f"{context['local_date']}.json"
    _atomic_write(json_path, (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    _atomic_write(markdown_path, _render(context, validated).encode("utf-8"))
    return FinalizedResearch(markdown_path, json_path, context["domain"], context["local_date"], len(validated["selected"]))
