from __future__ import annotations

import re
from pathlib import Path


SKILL_PATH = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "paperflow"
    / "SKILL.md"
)


def _skill_text() -> str:
    assert SKILL_PATH.is_file(), "repo PaperFlow Skill must exist under .agents/skills"
    return SKILL_PATH.read_text(encoding="utf-8")


def test_skill_frontmatter_has_trigger_only_name_and_description():
    text = _skill_text()
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert match is not None
    frontmatter = match.group(1).splitlines()
    assert frontmatter[0] == "name: paperflow"
    description = next(
        line.removeprefix("description: ")
        for line in frontmatter
        if line.startswith("description: ")
    )
    assert description.startswith("Use when ")
    assert all(
        trigger in description.casefold()
        for trigger in ("today's papers", "paper search", "obsidian paper note", "diagnostics")
    )
    assert len(frontmatter) == 2


def test_skill_allows_only_real_json_commands_and_flags():
    text = _skill_text()
    allowed_commands = {
        'paperflow --json daily',
        'paperflow --json search "<user query>"',
        'paperflow --json note <arxiv-id>',
        'paperflow --json doctor',
    }
    documented_commands = set(re.findall(r"`(paperflow [^`]+)`", text))
    assert allowed_commands <= documented_commands
    assert all(
        any(command.startswith(allowed) for allowed in allowed_commands)
        for command in documented_commands
    )
    assert set(re.findall(r"(?<!-)--[a-z][a-z-]*", text)) <= {
        "--json",
        "--date",
        "--no-write",
        "--history-only",
        "--force",
        "--email",
    }
    assert "paperflow recommend" not in text.casefold()
    assert "--format" not in text
    assert "--scope" not in text
    assert "database" not in text.casefold()


def test_skill_encodes_result_save_exit_and_safety_contracts():
    text = _skill_text()
    folded = text.casefold()
    assert "partial=true" in folded
    assert "history" in folded and "online" in folded
    assert "paperflow/papers/<id>.md" in folded
    assert "explicitly" in folded and "replac" in folded and "--force" in text
    for code in ("0", "1", "2", "3", "4", "5"):
        assert re.search(rf"\b{code}\b", text)
    for rule in (
        "never write `zotero.sqlite`",
        "never read sidebar api keys",
        "never yolo",
        "never auto-configure webdav",
        "does not automatically write zotero",
        "do not guess the schema",
    ):
        assert rule in folded


def test_skill_daily_write_behavior_and_doctor_read_only_are_unambiguous():
    text = _skill_text()
    daily = next(line for line in text.splitlines() if line.startswith("- Daily:"))
    doctor = next(
        line for line in text.splitlines() if line.startswith("- Diagnostics:")
    )
    daily_folded = daily.casefold()

    for requirement in ("writes", "obsidian", "report_path", "--no-write"):
        assert requirement in daily_folded
    assert "atomic" in daily_folded
    assert "same-day idempotent" in daily_folded
    assert "only `--no-write`" in daily_folded
    assert "read-only" not in daily_folded
    assert "read-only" in doctor.casefold()
    assert text.casefold().count("read-only") == 1


def test_skill_email_uses_complete_cloud_no_write_command():
    text = _skill_text()
    daily = next(line for line in text.splitlines() if line.startswith("- Daily:"))
    command = "paperflow --json daily --email --no-write"
    folded = daily.casefold()

    assert f"`{command}`" in daily
    assert "cloud email" in folded
    assert "does not write" in folded
    assert "local report" in folded
    assert "`--email`" not in daily.replace(f"`{command}`", "")


def test_skill_is_concise_and_has_required_sections():
    text = _skill_text()
    assert len(re.findall(r"\b[\w'-]+\b", text)) < 500
    assert "## Quick Reference" in text
    assert "## Safety and Exit Handling" in text
    assert "```mermaid" not in text
    assert "```dot" not in text
