"""Shared pytest-bdd state for executable PodDown specifications."""

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class ScenarioContext:
    """Mutable state scoped to one BDD scenario."""

    values: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def context() -> ScenarioContext:
    """Return isolated scenario state."""
    return ScenarioContext()
