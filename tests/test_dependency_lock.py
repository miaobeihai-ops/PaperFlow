from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _non_comment_lines(name: str) -> list[str]:
    return [
        line.strip()
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_runtime_lock_contains_only_the_verified_exact_pins():
    assert _non_comment_lines("requirements.lock") == [
        "anyio==4.14.2",
        "certifi==2026.7.22",
        "h11==0.16.0",
        "httpcore==1.0.9",
        "httpx==0.28.1",
        "idna==3.19",
        "typing_extensions==4.16.0",
        'tzdata==2026.3; sys_platform == "win32"',
    ]


def test_dev_lock_includes_runtime_lock_and_only_verified_exact_test_pins():
    assert _non_comment_lines("requirements-dev.lock") == [
        "-r requirements.lock",
        "colorama==0.4.6",
        "iniconfig==2.3.0",
        "packaging==26.3",
        "pluggy==1.6.0",
        "Pygments==2.21.0",
        "pytest==8.4.2",
    ]


def test_pyproject_direct_and_build_dependencies_are_exactly_pinned():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["build-system"]["requires"] == ["setuptools==75.8.2"]
    assert data["project"]["dependencies"] == [
        "httpx==0.28.1",
        'tzdata==2026.3; sys_platform == "win32"',
    ]
    assert data["project"]["optional-dependencies"]["dev"] == ["pytest==8.4.2"]
