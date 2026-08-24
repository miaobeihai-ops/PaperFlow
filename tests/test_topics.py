from pathlib import Path

import pytest

from paperflow.errors import ConfigError
from paperflow.topics import (
    TopicSettings,
    add_topic,
    load_topic_settings,
    remove_topic,
    render_topic_settings,
    resolve_topics_path,
)


def test_resolve_topics_path_returns_none_when_variable_is_absent():
    assert resolve_topics_path({}) is None


def test_explicit_topics_path_must_be_absolute(tmp_path):
    with pytest.raises(ConfigError, match="topics path must be an absolute file path"):
        resolve_topics_path({"PAPERFLOW_TOPICS_PATH": "config/topics.toml"})


def test_load_topic_settings_validates_and_casefolds_topics(tmp_path):
    path = tmp_path / "topics.toml"
    path.write_text(
        'top_n = 12\ntimezone = "Asia/Hong_Kong"\n'
        'history_reports = 20\narxiv_categories = ["cs.RO", "cs.CV"]\n\n'
        '[topics]\nRobotics = 5\n"3D Reconstruction" = 8\n',
        encoding="utf-8",
    )

    assert load_topic_settings(path) == TopicSettings(
        topics={"robotics": 5, "3d reconstruction": 8},
        arxiv_categories=("cs.RO", "cs.CV"),
        timezone="Asia/Hong_Kong",
        top_n=12,
        history_reports=20,
    )


def test_explicit_missing_topics_path_fails_without_fallback(tmp_path):
    with pytest.raises(ConfigError, match="topic file was not found"):
        load_topic_settings(tmp_path / "missing.toml")


def settings():
    return TopicSettings(
        topics={"robotics": 5, "3d reconstruction": 8},
        arxiv_categories=("cs.RO", "cs.CV"),
        timezone="Asia/Hong_Kong",
        top_n=10,
        history_reports=30,
    )


def test_render_topic_settings_is_deterministic():
    assert render_topic_settings(settings()) == (
        'top_n = 10\ntimezone = "Asia/Hong_Kong"\nhistory_reports = 30\n'
        'arxiv_categories = ["cs.RO", "cs.CV"]\n\n[topics]\n'
        '"3d reconstruction" = 8\n"robotics" = 5\n'
    )


def test_add_and_remove_topic_write_atomically(tmp_path):
    path = tmp_path / "topics.toml"
    path.write_text(render_topic_settings(settings()), encoding="utf-8")

    changed, updated = add_topic(path, "Vision Language Action", 9)
    assert changed is True
    assert updated.topics["vision language action"] == 9

    changed, updated = remove_topic(path, "ROBOTICS")
    assert changed is True
    assert "robotics" not in updated.topics
    assert list(tmp_path.glob("*.tmp")) == []


def test_remove_missing_topic_is_idempotent(tmp_path):
    path = tmp_path / "topics.toml"
    original = render_topic_settings(settings())
    path.write_text(original, encoding="utf-8")

    changed, updated = remove_topic(path, "missing")

    assert changed is False
    assert updated == settings()
    assert path.read_text(encoding="utf-8") == original


def test_remove_last_topic_is_rejected(tmp_path):
    path = tmp_path / "topics.toml"
    only_topic = TopicSettings(
        topics={"robotics": 5},
        arxiv_categories=("cs.RO",),
        timezone="Asia/Hong_Kong",
        top_n=10,
        history_reports=30,
    )
    path.write_text(render_topic_settings(only_topic), encoding="utf-8")

    with pytest.raises(ConfigError, match="at least one topic is required"):
        remove_topic(path, "robotics")

    assert load_topic_settings(path) == only_topic
