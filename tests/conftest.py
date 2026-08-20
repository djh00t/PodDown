"""Explicit local authentication mode for offline contract tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def local_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make legacy header-based API fixtures explicitly local-only."""
    monkeypatch.setenv("PODDOWN_AUTH_MODE", "local")
