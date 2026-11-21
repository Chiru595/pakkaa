import tomllib
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]


def _lines(name: str) -> set[str]:
    text = (API_DIR / name).read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith(("#", "-"))}


def test_pyproject_dependencies_match_requirements():
    """Vercel installs from pyproject.toml; local setup uses requirements.txt. They must stay identical."""
    project = tomllib.loads((API_DIR / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert set(project["dependencies"]) == _lines("requirements.txt")
