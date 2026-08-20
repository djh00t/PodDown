"""BDD coverage for the durable provider-evidence and cost boundary."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pytest_bdd import given, scenarios, then, when

from poddown.persistence import SQLiteUsageLedger, UsageConflict, UsageEvent
from poddown.providers.contracts import ProviderEvidence
from tests.bdd.conftest import ScenarioContext

scenarios("../features/provider_evidence_persistence.feature")

_TENANT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b10")
_PROJECT_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b12")
_JOB_ID = UUID("018f3c7d-9d04-7c25-8e20-9e8e0c4d3b15")
_OCCURRED_AT = datetime(2026, 8, 14, 4, 0, tzinfo=UTC)


def _evidence(*, output_sha256: str = "b" * 64) -> ProviderEvidence:
    return ProviderEvidence(
        operation="render",
        provider="elevenlabs",
        request_id="provider-request-1",
        model="eleven-multilingual-v2",
        input_sha256="a" * 64,
        output_sha256=output_sha256,
        usage={"input_units": 12, "output_units": 48},
        currency="USD",
        estimated_cost=Decimal("0.0040"),
        reconciled_cost=None,
        latency_ms=420,
        retry_count=0,
        occurred_at=_OCCURRED_AT,
        evidence_kind="provider-live",
    )


def _event(evidence: ProviderEvidence) -> UsageEvent:
    return UsageEvent(
        tenant_id=_TENANT_ID,
        project_id=_PROJECT_ID,
        job_id=_JOB_ID,
        provider_request_id=evidence.request_id,
        operation=evidence.operation,
        units=evidence.usage,
        currency=evidence.currency,
        estimated_cost=evidence.estimated_cost,
        reconciled_cost=evidence.reconciled_cost,
        created_at=evidence.occurred_at,
    )


@given("a complete provider evidence and matching usage event")
def complete_evidence_pair(tmp_path: Path, context: ScenarioContext) -> None:
    evidence = _evidence()
    context.values["database"] = tmp_path / "provider-evidence.sqlite3"
    context.values["evidence"] = evidence
    context.values["event"] = _event(evidence)


@when("the provider evidence pair is persisted and the ledger is reconstructed")
def persist_and_reconstruct(context: ScenarioContext) -> None:
    database = context.values["database"]
    evidence = context.values["evidence"]
    event = context.values["event"]
    SQLiteUsageLedger(database).record_evidence(evidence, event)
    context.values["ledger"] = SQLiteUsageLedger(database)


@then("the same evidence and usage event replay without duplication")
def evidence_pair_replays(context: ScenarioContext) -> None:
    ledger = context.values["ledger"]
    evidence = context.values["evidence"]
    event = context.values["event"]
    assert ledger.get_evidence(_TENANT_ID, evidence.request_id) == evidence
    assert ledger.record_evidence(evidence, event) == (evidence, event)
    assert ledger.list_for_job(_TENANT_ID, _JOB_ID) == (event,)


@when("the same provider request is replayed with a different output digest")
def conflicting_evidence_replay(context: ScenarioContext) -> None:
    evidence = context.values["evidence"]
    database = context.values["database"]
    ledger = SQLiteUsageLedger(database)
    ledger.record_evidence(evidence, context.values["event"])
    conflicting = _evidence(output_sha256="c" * 64)
    with pytest.raises(UsageConflict):
        ledger.record_evidence(conflicting, _event(conflicting))
    context.values["ledger"] = ledger


@then("the provider evidence conflict is rejected")
def provider_evidence_conflict_is_rejected(context: ScenarioContext) -> None:
    ledger = context.values["ledger"]
    assert ledger.list_for_job(_TENANT_ID, _JOB_ID)
