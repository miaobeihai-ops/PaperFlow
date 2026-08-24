from pathlib import Path

import pytest

from paperflow.domain import load_domain_profile
from paperflow.errors import ConfigError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bundled_domains_are_independent_and_current_window_only():
    chemical = load_domain_profile("chemical-energy", project_root=PROJECT_ROOT)
    robotics = load_domain_profile("robotics", project_root=PROJECT_ROOT)

    assert chemical.slug == "chemical-energy"
    assert "carbon capture" in chemical.query_seeds
    assert chemical.providers == ("arxiv", "crossref", "openalex", "feed")
    assert robotics.slug == "robotics"
    assert "embodied intelligence" in robotics.query_seeds
    assert robotics.providers == (
        "arxiv",
        "huggingface",
        "crossref",
        "openalex",
        "feed",
    )
    assert chemical.deep_read_limit == 5
    assert robotics.deep_read_limit == 5
    assert not hasattr(chemical, "catch_up_days")


def test_private_overlay_can_adjust_preferences_without_replacing_sources(tmp_path):
    overlay = tmp_path / "chemical-energy.local.toml"
    overlay.write_text(
        'query_seeds = ["hydrogen purification"]\n'
        'include_concepts = ["mixed gas"]\n'
        "candidate_limit = 80\n",
        encoding="utf-8",
    )

    profile = load_domain_profile(
        "chemical-energy",
        project_root=PROJECT_ROOT,
        overlay_path=overlay,
    )

    assert profile.query_seeds == ("hydrogen purification",)
    assert profile.include_concepts == ("mixed gas",)
    assert profile.candidate_limit == 80
    assert profile.providers == ("arxiv", "crossref", "openalex", "feed")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", '"PRIVATE_SENTINEL"'),
        ("provider_url", '"https://private.example.test"'),
        ("api_key", '"PRIVATE_SENTINEL"'),
        ("password", '"PRIVATE_SENTINEL"'),
        ("providers", '["arxiv"]'),
        ("feeds", '["https://private.example.test/feed"]'),
        ("arxiv_categories", '["cs.AI"]'),
    ],
)
def test_private_overlay_cannot_add_executable_secret_or_source_fields(
    tmp_path, field, value
):
    overlay = tmp_path / "chemical-energy.local.toml"
    overlay.write_text(f"{field} = {value}\n", encoding="utf-8")

    with pytest.raises(
        ConfigError, match="private domain overlay contains forbidden fields"
    ):
        load_domain_profile(
            "chemical-energy",
            project_root=PROJECT_ROOT,
            overlay_path=overlay,
        )


@pytest.mark.parametrize("slug", ["", "../robotics", "Robotics", "robotics.toml"])
def test_domain_slug_is_strict(slug):
    with pytest.raises(ConfigError, match="invalid domain"):
        load_domain_profile(slug, project_root=PROJECT_ROOT)


def test_domain_rejects_http_feed(tmp_path):
    domain_dir = tmp_path / "config" / "domains"
    domain_dir.mkdir(parents=True)
    (domain_dir / "unsafe.toml").write_text(
        'display_name = "Unsafe"\n'
        'language = "zh-CN"\n'
        "lookback_hours = 24\n"
        "candidate_limit = 20\n"
        "deep_read_limit = 2\n"
        'query_seeds = ["test"]\n'
        'include_concepts = ["test"]\n'
        "exclude_concepts = []\n"
        'providers = ["feed"]\n'
        "arxiv_categories = []\n"
        'feeds = ["http://example.test/feed"]\n'
        'rubric = ["relevance"]\n'
        'report_sections = ["highlights"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="feed URLs must use HTTPS"):
        load_domain_profile("unsafe", project_root=tmp_path)


def test_missing_explicit_overlay_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="private domain overlay is unavailable"):
        load_domain_profile(
            "robotics",
            project_root=PROJECT_ROOT,
            overlay_path=tmp_path / "missing.toml",
        )
