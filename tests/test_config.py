import json
import tomllib
from pathlib import Path

import pytest

from paperflow.config import ConfigError, _build, load_cloud_config, load_local_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


@pytest.mark.parametrize("raw_json", ["[]", "null"])
def test_rejects_non_object_cloud_config(raw_json):
    with pytest.raises(ConfigError) as exc_info:
        load_cloud_config(raw_json)

    assert str(exc_info.value) == "cloud configuration must be a JSON object"


def test_rejects_invalid_private_json_without_exposing_payload():
    private_payload = '{"secret":"PRIVATE_SENTINEL"'

    with pytest.raises(ConfigError) as exc_info:
        load_cloud_config(private_payload)

    assert str(exc_info.value) == "PAPERFLOW_PRIVATE_CONFIG_JSON is invalid JSON"
    assert "PRIVATE_SENTINEL" not in str(exc_info.value)


def test_rejects_malformed_local_toml_without_exposing_payload(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('vault_path = "PRIVATE_SENTINEL\n', encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_local_config(config_path)

    assert str(exc_info.value) == "local configuration is invalid TOML"
    assert "PRIVATE_SENTINEL" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("keywords", {"robotics": 1.5}, "keyword weights must be integers"),
        ("keywords", {"robotics": True}, "keyword weights must be integers"),
        ("top_n", "PRIVATE_SENTINEL", "top_n must be an integer"),
        ("top_n", 10.5, "top_n must be an integer"),
        ("top_n", True, "top_n must be an integer"),
        ("history_reports", "PRIVATE_SENTINEL", "history_reports must be an integer"),
        ("history_reports", 30.5, "history_reports must be an integer"),
        ("history_reports", False, "history_reports must be an integer"),
        (
            "arxiv_categories",
            "PRIVATE_SENTINEL",
            "arxiv_categories must be a list of non-empty strings",
        ),
        ("arxiv_categories", [""], "arxiv_categories must be a list of non-empty strings"),
        ("arxiv_categories", [1], "arxiv_categories must be a list of non-empty strings"),
        ("timezone", "", "timezone must be a non-empty string"),
        ("timezone", 1, "timezone must be a non-empty string"),
        ("vault_path", "", "vault_path must be a non-empty string"),
        ("vault_path", 1, "vault_path must be a non-empty string"),
        ("mail_to", "", "mail_to must be a non-empty string"),
        ("mail_to", 1, "mail_to must be a non-empty string"),
    ],
)
def test_rejects_invalid_field_types_without_exposing_payload(field, invalid_value, message):
    data = {
        "keywords": {"robotics": 5},
        "private_note": "PRIVATE_SENTINEL",
        field: invalid_value,
    }

    with pytest.raises(ConfigError) as exc_info:
        load_cloud_config(json.dumps(data))

    assert str(exc_info.value) == message
    assert "PRIVATE_SENTINEL" not in str(exc_info.value)


def test_rejects_non_string_keyword_keys():
    with pytest.raises(ConfigError, match="^keyword names must be strings$"):
        _build({"keywords": {1: 5}}, require_vault=False)


@pytest.mark.parametrize("top_n", [0, 51])
def test_rejects_top_n_outside_boundaries(top_n):
    raw_json = json.dumps({"keywords": {"robotics": 5}, "top_n": top_n})

    with pytest.raises(ConfigError, match="^top_n must be between 1 and 50$"):
        load_cloud_config(raw_json)


def test_rejects_relative_local_vault_path_without_exposing_value(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '''vault_path = "PRIVATE_SENTINEL/relative"

[keywords]
robotics = 5
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc_info:
        load_local_config(config_path)

    assert str(exc_info.value) == "vault_path must be absolute"
    assert "PRIVATE_SENTINEL" not in str(exc_info.value)


def test_runtime_dependencies_include_windows_tzdata_contract():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]

    assert (
        'tzdata>=2025.2,<2027; sys_platform == "win32"' in dependencies
    )
