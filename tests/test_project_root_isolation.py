"""A nested source export must never inherit a parent's account configuration."""
from pathlib import Path

from app.auth import settings


def test_project_root_stays_with_package_when_parent_looks_configured(monkeypatch, tmp_path):
    parent = tmp_path / "operator"
    nested = parent / "public-snapshot"
    monkeypatch.setattr(settings, "__file__", str(nested / "app/auth/settings.py"))
    # Model the discovery conditions without writing or opening any credentials.
    monkeypatch.setattr(Path, "exists", lambda path: path.parent == parent and path.name.startswith("."))
    monkeypatch.setattr(Path, "is_dir", lambda path: path == parent / "app")
    assert settings._resolve_project_root() == nested
