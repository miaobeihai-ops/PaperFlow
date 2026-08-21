import json

import pytest

from paperflow.config import ConfigError, load_cloud_config, load_local_config


def test_load_local_config(tmp_path):
    vault_path = tmp_path / "Vault"
    vault_path.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''vault_path = "{vault_path.as_posix()}"
top_n = 10
timezone = "Asia/Hong_Kong"
history_reports = 30
arxiv_categories = ["cs.AI", "cs.CV"]

[keywords]
robotics = 5
"3d reconstruction" = 8
''',
        encoding="utf-8",
    )

    config = load_local_config(config_path)

    assert config.vault_path == vault_path
    assert config.keywords["3d reconstruction"] == 8
    assert config.top_n == 10


def test_load_cloud_config_from_private_json():
    raw_json = json.dumps(
        {
            "mail_to": "reader@example.com",
            "keywords": {"robotics": 5},
            "arxiv_categories": ["cs.RO"],
            "timezone": "Asia/Hong_Kong",
            "top_n": 10,
        }
    )

    config = load_cloud_config(raw_json)

    assert config.mail_to == "reader@example.com"
    assert config.vault_path is None


def test_rejects_empty_keywords(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('vault_path = "C:/Vault"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="keywords"):
        load_local_config(config_path)
