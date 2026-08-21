import json
import os
import tomllib
from pathlib import Path

import pytest

import paperflow.config as config_module

from paperflow.config import (
    ConfigError,
    _build,
    default_local_config_path,
    load_cloud_config,
    load_local_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _paperflow_home_resolver():
    assert hasattr(config_module, "resolve_paperflow_home"), (
        "config must expose the shared PAPERFLOW_HOME resolver"
    )
    return config_module.resolve_paperflow_home


def test_resolve_paperflow_home_returns_none_when_variable_is_absent():
    resolver = _paperflow_home_resolver()

    assert (
        resolver(
            {},
            path_exists=lambda _path: False,
            path_is_dir=lambda _path: False,
        )
        is None
    )


@pytest.mark.parametrize("invalid_home", ["", "relative/path", "C:\\Paper\nFlow"])
def test_resolve_paperflow_home_rejects_invalid_present_values(invalid_home):
    resolver = _paperflow_home_resolver()

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        resolver(
            {"PAPERFLOW_HOME": invalid_home},
            path_exists=lambda _path: False,
            path_is_dir=lambda _path: False,
        )


def test_resolve_paperflow_home_rejects_existing_file(tmp_path):
    resolver = _paperflow_home_resolver()
    home = tmp_path / "paperflow-home"
    home.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        resolver(
            {"PAPERFLOW_HOME": str(home)},
            path_exists=Path.exists,
            path_is_dir=Path.is_dir,
        )


def test_resolve_paperflow_home_accepts_existing_and_nonexistent_absolute_dirs(
    tmp_path,
):
    resolver = _paperflow_home_resolver()
    existing = tmp_path / "existing"
    existing.mkdir()
    nonexistent = tmp_path / "nonexistent"

    assert resolver(
        {"PAPERFLOW_HOME": str(existing)},
        path_exists=Path.exists,
        path_is_dir=Path.is_dir,
    ) == existing
    assert resolver(
        {"PAPERFLOW_HOME": str(nonexistent)},
        path_exists=Path.exists,
        path_is_dir=Path.is_dir,
    ) == nonexistent


@pytest.mark.parametrize("home_variable", ["USERPROFILE", "HOME"])
def test_resolve_paperflow_home_expands_tilde_from_supplied_environment_only(
    monkeypatch, tmp_path, home_variable
):
    resolver = _paperflow_home_resolver()
    injected_user_home = tmp_path / "InjectedUser"
    process_user_home = tmp_path / "ProcessUser"
    monkeypatch.setenv("USERPROFILE", str(process_user_home))
    monkeypatch.setenv("HOME", str(process_user_home))

    resolved = resolver(
        {"PAPERFLOW_HOME": "~/PaperFlow", home_variable: str(injected_user_home)},
        path_exists=lambda _path: False,
        path_is_dir=lambda _path: False,
    )

    assert resolved == injected_user_home / "PaperFlow"


def test_resolve_paperflow_home_rejects_tilde_without_injected_home():
    resolver = _paperflow_home_resolver()

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        resolver(
            {"PAPERFLOW_HOME": "~/PaperFlow"},
            path_exists=lambda _path: False,
            path_is_dir=lambda _path: False,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize(
    "raw_home",
    ["~//PaperFlow", "~///PaperFlow", r"~\\PaperFlow", r"~\\\\PaperFlow"],
)
def test_resolve_paperflow_home_strips_repeated_tilde_separators(raw_home):
    resolver = _paperflow_home_resolver()
    injected_home = Path(r"C:\Users\injected")

    resolved = resolver(
        {"PAPERFLOW_HOME": raw_home, "USERPROFILE": str(injected_home)},
        path_exists=lambda _path: False,
        path_is_dir=lambda _path: False,
    )

    assert resolved == injected_home / "PaperFlow"
    assert resolved.is_relative_to(injected_home)


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize(
    "raw_home",
    [r"~/C:\PaperFlow", r"~\D:\PaperFlow"],
)
def test_resolve_paperflow_home_rejects_drive_qualified_tilde_suffix(raw_home):
    resolver = _paperflow_home_resolver()

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        resolver(
            {
                "PAPERFLOW_HOME": raw_home,
                "USERPROFILE": r"Z:\Users\injected",
            },
            path_exists=lambda _path: False,
            path_is_dir=lambda _path: False,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize(
    ("raw_home", "relative_suffix"),
    [
        ("~/nested/PaperFlow", Path("nested/PaperFlow")),
        (r"~\nested\PaperFlow", Path(r"nested\PaperFlow")),
    ],
)
def test_resolve_paperflow_home_accepts_standard_relative_tilde_suffixes(
    raw_home, relative_suffix
):
    resolver = _paperflow_home_resolver()
    injected_home = Path(r"Z:\Users\injected")

    assert resolver(
        {"PAPERFLOW_HOME": raw_home, "USERPROFILE": str(injected_home)},
        path_exists=lambda _path: False,
        path_is_dir=lambda _path: False,
    ) == injected_home / relative_suffix


@pytest.mark.skipif(os.name != "nt", reason="Windows environment semantics")
def test_resolve_paperflow_home_uses_legacy_home_drive_and_path():
    resolver = _paperflow_home_resolver()

    resolved = resolver(
        {
            "PAPERFLOW_HOME": r"~\PaperFlow",
            "HOMEDRIVE": "Z:",
            "HOMEPATH": r"\Users\legacy",
        },
        path_exists=lambda _path: False,
        path_is_dir=lambda _path: False,
    )

    assert resolved == Path(r"Z:\Users\legacy\PaperFlow")


@pytest.mark.skipif(os.name != "nt", reason="Windows environment semantics")
@pytest.mark.parametrize(
    "legacy_home",
    [
        {"HOMEDRIVE": "Z:"},
        {"HOMEPATH": r"\Users\legacy"},
        {"HOMEDRIVE": "Z:", "HOMEPATH": r"Users\legacy"},
    ],
)
def test_resolve_paperflow_home_rejects_incomplete_or_relative_legacy_home(
    legacy_home,
):
    resolver = _paperflow_home_resolver()

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        resolver(
            {"PAPERFLOW_HOME": r"~\PaperFlow", **legacy_home},
            path_exists=lambda _path: False,
            path_is_dir=lambda _path: False,
        )


def test_resolve_paperflow_home_rejects_named_user_tilde():
    resolver = _paperflow_home_resolver()

    with pytest.raises(ConfigError, match="^PAPERFLOW_HOME must be an absolute path$"):
        resolver(
            {"PAPERFLOW_HOME": "~another-user/PaperFlow"},
            path_exists=lambda _path: False,
            path_is_dir=lambda _path: False,
        )


def test_resolve_paperflow_home_probes_each_path_once(tmp_path):
    resolver = _paperflow_home_resolver()
    home = tmp_path / "PaperFlowHome"
    calls = {"exists": 0, "is_dir": 0}

    def exists(path):
        assert path == home
        calls["exists"] += 1
        return True

    def is_dir(path):
        assert path == home
        calls["is_dir"] += 1
        return True

    assert resolver(
        {"PAPERFLOW_HOME": str(home)},
        path_exists=exists,
        path_is_dir=is_dir,
    ) == home
    assert calls == {"exists": 1, "is_dir": 1}


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize("home", [r"C:\PaperFlow", r"\\server\share\PaperFlow"])
def test_resolve_paperflow_home_accepts_windows_drive_and_unc_paths(home):
    resolver = _paperflow_home_resolver()

    assert resolver(
        {"PAPERFLOW_HOME": home},
        path_exists=lambda _path: False,
        path_is_dir=lambda _path: False,
    ) == Path(home)


def test_default_local_config_path_delegates_to_shared_resolver(
    monkeypatch, tmp_path
):
    home = tmp_path / "ResolvedHome"
    calls = []

    def resolve(environ, *, path_exists, path_is_dir):
        calls.append((environ, path_exists, path_is_dir))
        return home

    monkeypatch.setattr(config_module, "resolve_paperflow_home", resolve)
    monkeypatch.setenv("PAPERFLOW_HOME", "ignored-by-spy")

    assert default_local_config_path() == home / "config" / "config.toml"
    assert calls == [(os.environ, Path.exists, Path.is_dir)]


def test_default_local_config_path_prefers_absolute_paperflow_home(
    monkeypatch, tmp_path
):
    paperflow_home = tmp_path / "PaperFlowHome"
    monkeypatch.setenv("PAPERFLOW_HOME", str(paperflow_home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    assert default_local_config_path() == paperflow_home / "config" / "config.toml"


def test_default_local_config_path_uses_appdata_when_paperflow_home_is_absent(
    monkeypatch, tmp_path
):
    appdata = tmp_path / "AppData"
    monkeypatch.delenv("PAPERFLOW_HOME", raising=False)
    monkeypatch.setenv("APPDATA", str(appdata))

    assert default_local_config_path() == appdata / "PaperFlow" / "config.toml"


@pytest.mark.parametrize("invalid_home", ["", "relative/path", "C:\\Paper\nFlow"])
def test_default_local_config_path_rejects_invalid_paperflow_home(
    monkeypatch, tmp_path, invalid_home
):
    monkeypatch.setenv("PAPERFLOW_HOME", invalid_home)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))

    with pytest.raises(ConfigError) as exc_info:
        default_local_config_path()

    assert str(exc_info.value) == "PAPERFLOW_HOME must be an absolute path"


def test_default_local_config_path_rejects_paperflow_home_that_is_a_file(
    monkeypatch, tmp_path
):
    paperflow_home = tmp_path / "paperflow-home"
    paperflow_home.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("PAPERFLOW_HOME", str(paperflow_home))

    with pytest.raises(ConfigError) as exc_info:
        default_local_config_path()

    assert str(exc_info.value) == "PAPERFLOW_HOME must be an absolute path"


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

    assert 'tzdata==2026.3; sys_platform == "win32"' in dependencies


@pytest.mark.parametrize(
    ("invalid_value", "expected_message"),
    [
        (-1, "history_reports must be non-negative"),
        (False, "history_reports must be an integer"),
    ],
)
def test_cloud_config_rejects_invalid_history_reports(
    invalid_value, expected_message
):
    raw_json = json.dumps(
        {"keywords": {"robotics": 5}, "history_reports": invalid_value}
    )

    with pytest.raises(ConfigError, match=f"^{expected_message}$"):
        load_cloud_config(raw_json)


@pytest.mark.parametrize(
    ("toml_value", "expected_message"),
    [
        ("-1", "history_reports must be non-negative"),
        ("false", "history_reports must be an integer"),
    ],
)
def test_local_config_rejects_invalid_history_reports(
    tmp_path, toml_value, expected_message
):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''vault_path = "{tmp_path.as_posix()}"
history_reports = {toml_value}

[keywords]
robotics = 5
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=f"^{expected_message}$"):
        load_local_config(config_path)
