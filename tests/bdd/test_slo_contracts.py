"""BDD bindings for bounded service-level objective measurements."""

from __future__ import annotations

from typing import Any

from pytest_bdd import given, scenarios, then, when

from poddown.production_readiness import (
    SloComparison,
    SloMeasurement,
    SloObjective,
)

scenarios("../features/slo_contracts.feature")


@given("an availability SLO objective")
def availability_objective(context: Any) -> None:
    context.values["objective"] = SloObjective(
        name="api.availability",
        comparison=SloComparison.AT_LEAST,
        target=0.99,
        unit="ratio",
    )


@when("the observed availability is 0.999")
def observed_availability(context: Any) -> None:
    context.values["measurement"] = context.values["objective"].measure(
        observed=0.999,
        sample_count=1000,
    )


@given("a queue-age SLO objective")
def queue_age_objective(context: Any) -> None:
    context.values["objective"] = SloObjective(
        name="workflow.queue_age",
        comparison=SloComparison.AT_MOST,
        target=90,
        unit="seconds",
    )


@when("the observed queue age is 91 seconds")
def observed_queue_age(context: Any) -> None:
    context.values["measurement"] = context.values["objective"].measure(
        observed=91,
        sample_count=12,
    )


@then("the SLO measurement is met")
def slo_is_met(context: Any) -> None:
    measurement: SloMeasurement = context.values["measurement"]
    assert measurement.met is True


@then("the SLO measurement is not met")
def slo_is_not_met(context: Any) -> None:
    measurement: SloMeasurement = context.values["measurement"]
    assert measurement.met is False
