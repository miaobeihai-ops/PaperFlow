from __future__ import annotations

import base64
from html import escape
from pathlib import Path


_CSS = """
:root { color-scheme: light; --ink:#172033; --muted:#5d687b; --line:#d9e0ea; --accent:#176b87; --soft:#eef6f8; }
* { box-sizing: border-box; }
body { margin:0; color:var(--ink); background:#fff; font-family:"Microsoft YaHei","Noto Sans CJK SC","Segoe UI",sans-serif; font-size:10.5pt; line-height:1.62; }
main { max-width:190mm; margin:0 auto; padding:16mm 14mm 18mm; }
h1 { margin:0 0 4mm; color:#123c4b; font-size:24pt; line-height:1.25; }
h2 { margin:11mm 0 4mm; padding-bottom:2mm; border-bottom:1px solid var(--line); color:#164f63; font-size:16pt; break-after:avoid; }
h3 { margin:5mm 0 2mm; font-size:11.5pt; break-after:avoid; }
p, li { orphans:3; widows:3; }
.meta { display:grid; grid-template-columns:32mm 1fr; gap:1.5mm 4mm; padding:4mm; background:var(--soft); border-left:3px solid var(--accent); }
.label { color:var(--muted); font-weight:700; }
.paper { break-inside:avoid-page; }
.scores { color:var(--muted); }
.evidence-boundary { padding:3mm 4mm; border:1px solid var(--line); border-radius:2mm; background:#fafbfd; }
figure { margin:5mm 0 7mm; break-inside:avoid; }
figure img { display:block; max-width:100%; max-height:150mm; margin:auto; object-fit:contain; }
figcaption { margin-top:2mm; color:var(--muted); font-size:9pt; }
a { color:#0c607c; overflow-wrap:anywhere; }
ul { padding-left:6mm; }
@page { size:A4; margin:13mm 12mm 15mm; @bottom-center { content: counter(page); color:#697586; font-size:8pt; } }
@media print { main { max-width:none; margin:0; padding:0; } a { color:inherit; text-decoration:none; } }
"""


def _list_section(title: str, values: list[str]) -> str:
    items = "".join(f"<li>{escape(value)}</li>" for value in values)
    return f"<section><h2>{escape(title)}</h2><ul>{items}</ul></section>"


def _data_uri(path: Path) -> str:
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media};base64,{encoded}"


def render_research_html(
    context: dict,
    analysis: dict,
    *,
    search_window: str,
    figure_paths: dict[tuple[str, int], str],
    report_directory: Path,
) -> str:
    candidates = {item["key"]: item for item in context["candidates"]}
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{escape(context['profile']['display_name'])}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        f"<h1>{escape(context['profile']['display_name'])}</h1>",
        '<section><h2>检索时段与来源</h2><div class="meta">',
        f'<div class="label">生成日期</div><div>{escape(context["local_date"])}</div>',
        f'<div class="label">检索时段</div><div>{escape(search_window)}</div>',
        f'<div class="label">覆盖说明</div><div>{escape(analysis["coverage"])}</div>',
        "</div></section>",
    ]
    for index, selected in enumerate(analysis["selected"], 1):
        source = candidates[selected["candidate_id"]]
        authors = ", ".join(source["authors"])
        parts.extend([
            f'<article class="paper"><h2>{index}. {escape(source["title"])}</h2>',
            '<div class="meta">',
            f'<div class="label">作者</div><div>{escape(authors)}</div>',
            f'<div class="label">日期</div><div>{escape(source["published"])}</div>',
            f'<div class="label">原文</div><div><a href="{escape(source["url"], quote=True)}">{escape(source["url"])}</a></div>',
            f'<div class="label">阅读</div><div>{escape(selected["analysis_depth"])} / {escape(selected["access_status"])}</div>',
            "</div>",
            f'<p class="evidence-boundary"><strong>证据边界：</strong>{escape(selected["limitations"])}</p>',
            f'<p class="scores">相关性 {selected["relevance"]}/10 · 新颖性 {selected["novelty"]}/10 · 证据 {selected["evidence_quality"]}/10 · 产业价值 {selected["industrial_value"]}/10 · 置信度 {escape(selected["confidence"])}</p>',
            f'<h3>入选理由</h3><p>{escape(selected["reason"])}</p>',
            f'<h3>方法</h3><p>{escape(selected["method"])}</p>',
            f'<h3>证据</h3><p>{escape(selected["evidence"])}</p>',
            f'<h3>实际启示</h3><p>{escape(selected["practical_implications"])}</p>',
        ])
        for figure_index, figure in enumerate(selected["figures"], 1):
            relative = figure_paths[(selected["candidate_id"], figure_index)]
            image_uri = _data_uri(report_directory / relative)
            alt = f"{figure['figure']} - {figure['caption']}"
            parts.append(
                f'<figure><img src="{image_uri}" alt="{escape(alt, quote=True)}">'
                f'<figcaption>{escape(figure["figure"])}；PDF第{figure["page"]}页；'
                f'{escape(figure["caption"])}；{escape(figure["license"])}；'
                f'<a href="{escape(figure["source_url"], quote=True)}">原文</a></figcaption></figure>'
            )
        parts.append("</article>")
    for title, field in (
        ("跨论文主题", "themes"),
        ("分歧", "disagreements"),
        ("政策与产业联系", "policy_industry_links"),
        ("待解决问题", "unresolved_questions"),
    ):
        parts.append(_list_section(title, analysis[field]))
    parts.append("</main></body></html>\n")
    return "".join(parts)


__all__ = ["render_research_html"]
