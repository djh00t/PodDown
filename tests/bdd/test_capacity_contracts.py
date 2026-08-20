"""BDD bindings for deterministic load admission contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.capacity import CapacityDecision, LoadContract

scenarios("../features/capacity_contracts.feature")


@given("a bounded load contract")
def load_contract(context: Any) -> None:
    context.values["contract"] = LoadContract(
        max_in_flight=4,
        max_queue_age_seconds=90,
        max_estimated_cost=Decimal("2.50"),
    )


@when("one episode is checked with safe queue age and cost")
def safe_episode(context: Any) -> None:
    context.values["decision"] = context.values["contract"].check(
        in_flight=2,
        queue_age_seconds=12,
        estimated_cost=Decimal("0.25"),
    )


@when("the concurrency limit is already full")
def full_concurrency(context: Any) -> None:
    context.values["decision"] = context.values["contract"].check(
        in_flight=4,
        queue_age_seconds=12,
        estimated_cost=Decimal("0.25"),
    )


@then("the load decision admits the episode")
def episode_admitted(context: Any) -> None:
    decision: CapacityDecision = context.values["decision"]
    assert decision.admitted is True
    assert decision.reason == "within capacity bounds"


@then("the load decision rejects the episode with a bounded reason")
def episode_rejected(context: Any) -> None:
    decision: CapacityDecision = context.values["decision"]
    assert decision.admitted is False
    assert decision.reason == "concurrency limit reached"
    assert "source" not in decision.reason
