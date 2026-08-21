from datetime import date
from pathlib import Path

import pytest

from paperflow.models import Paper
from paperflow.notes import NoteExists, paper_note_path, write_paper_note
from paperflow.search import search_history


def paper(**changes) -> Paper:
    values = {
        "arxiv_id": "2608.12345v3",
        "title": 'Robotic 3D Reconstruction: "Safe"',
        "authors": ("Ada Researcher", "Bo: Builder"),
        "abstract": "Original abstract.\n\nSecond paragraph.",
        "primary_category": "cs.RO",
        "published": "2026-08-20",
        "sources": ("arxiv",),
        "hf_upvotes": 0,
        "url": "https://arxiv.org/abs/2608.12345v3",
        "pdf_url": "https://arxiv.org/pdf/2608.12345v3",
    }
    values.update(changes)
    return Paper(**values)


def _report(vault: Path, name: str, content: str) -> Path:
    reports = vault / "PaperFlow" / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / name
    path.write_text(content, encoding="utf-8")
    return path


def test_search_history_finds_title_and_id_in_same_section(tmp_path):
    vault = tmp_path / "Vault"
    path = _report(
        vault,
        "2026-08-20.md",
        "# Daily\n\n"
        "## 1. Robotic 3D Reconstruction\n"
        "- arxiv_id: `2608.12345`\n"
        "- abstract: Builds a map.\n\n"
        "## 2. Unrelated Paper\n"
        "- arxiv_id: `2608.99999`\n",
    )

    assert search_history(vault, "3D RECONSTRUCTION") == [
        {
            "title": "Robotic 3D Reconstruction",
            "arxiv_id": "2608.12345",
            "path": str(path),
        }
    ]


def test_search_history_matches_section_content_without_cross_pairing(tmp_path):
    vault = tmp_path / "Vault"
    _report(
        vault,
        "2026-08-20.md",
        "## 1. First title\n"
        "- abstract: semantic target\n\n"
        "## 2. Second title\n"
        "- arxiv_id: `2608.22222`\n",
    )

    assert search_history(vault, "semantic target") == []


def test_search_history_stops_candidate_at_any_level_two_heading(tmp_path):
    vault = tmp_path / "Vault"
    _report(
        vault,
        "2026-08-20.md",
        "## 1. Candidate without ID\n"
        "- abstract: target phrase\n\n"
        "## Appendix\n"
        "- arxiv_id: `2608.99999`\n",
    )

    assert search_history(vault, "target phrase") == []


def test_search_history_uses_newest_report_first_and_skips_invalid_files(tmp_path):
    vault = tmp_path / "Vault"
    old = _report(vault, "2026-08-19.md", "## 1. Match\n- arxiv_id: `2608.11111`\n")
    new = _report(vault, "2026-08-20.md", "## 1. Match\n- arxiv_id: `2608.22222`\n")
    _report(vault, "notes.md", "## 1. Match\n- arxiv_id: `2608.33333`\n")
    corrupt = vault / "PaperFlow" / "Reports" / "2026-08-21.md"
    corrupt.write_bytes(b"\xff\xfe")

    results = search_history(vault, "match")

    assert [item["arxiv_id"] for item in results] == ["2608.22222", "2608.11111"]
    assert [item["path"] for item in results] == [str(new), str(old)]


@pytest.mark.parametrize("query", ["", " \t\n"])
def test_search_history_rejects_blank_query_without_creating_files(tmp_path, query):
    vault = tmp_path / "Vault"

    with pytest.raises(ValueError, match="query must not be blank"):
        search_history(vault, query)

    assert not vault.exists()


def test_write_paper_note_creates_safe_utf8_lf_note(tmp_path):
    vault = tmp_path / "Vault"

    target = write_paper_note(vault, paper(), created=date(2026, 8, 21))

    assert target == vault / "PaperFlow" / "Papers" / "2608.12345.md"
    raw = target.read_bytes()
    assert b"\r" not in raw
    content = raw.decode("utf-8")
    assert 'arxiv_id: "2608.12345"' in content
    assert 'title: "Robotic 3D Reconstruction: \\"Safe\\""' in content
    assert 'authors: ["Ada Researcher", "Bo: Builder"]' in content
    assert 'status: "unread"' in content
    assert 'created: "2026-08-21"' in content
    assert "Original abstract.\n\nSecond paragraph." in content
    assert "## Reading Notes" in content
    assert "AI summary" not in content


def test_paper_note_path_canonicalizes_id_or_url(tmp_path):
    vault = tmp_path / "Vault"

    assert paper_note_path(
        vault, "https://arxiv.org/abs/2608.12345v3"
    ) == vault / "PaperFlow" / "Papers" / "2608.12345.md"


def test_write_paper_note_no_overwrite_then_force_replaces(tmp_path):
    vault = tmp_path / "Vault"
    target = write_paper_note(vault, paper(title="First"), created=date(2026, 8, 21))
    before_names = {path.name for path in target.parent.iterdir()}

    with pytest.raises(NoteExists, match="paper note already exists"):
        write_paper_note(vault, paper(title="Second"), created=date(2026, 8, 21))

    assert target.read_text(encoding="utf-8").count("# First") == 1
    assert {path.name for path in target.parent.iterdir()} == before_names

    replaced = write_paper_note(
        vault,
        paper(title="Second"),
        force=True,
        created=date(2026, 8, 22),
    )
    assert replaced == target
    assert "# Second" in target.read_text(encoding="utf-8")


def test_write_paper_note_preserves_old_target_and_cleans_own_temp_on_replace_failure(
    tmp_path, monkeypatch
):
    vault = tmp_path / "Vault"
    target = write_paper_note(vault, paper(title="Old"), created=date(2026, 8, 21))
    original = target.read_bytes()
    before_names = {path.name for path in target.parent.iterdir()}

    def fail_replace(_source, _target):
        raise OSError("replace failed")

    monkeypatch.setattr("paperflow.notes.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_paper_note(
            vault,
            paper(title="New"),
            force=True,
            created=date(2026, 8, 22),
        )

    assert target.read_bytes() == original
    assert {path.name for path in target.parent.iterdir()} == before_names


def test_write_paper_note_rejects_invalid_id_before_creating_directories(tmp_path):
    vault = tmp_path / "Vault"

    with pytest.raises(ValueError, match="invalid arXiv identifier"):
        write_paper_note(vault, paper(arxiv_id="../private"))

    assert not vault.exists()
