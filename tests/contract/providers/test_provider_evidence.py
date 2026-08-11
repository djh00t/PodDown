"""Public provider-evidence contract coverage."""

import inspect

import poddown.providers.contracts as contracts


def test_provider_evidence_freezes_the_production_closure_wire_surface() -> None:
    """Catch contract expansion that would retain raw provider request content."""
    evidence_type = getattr(contracts, "ProviderEvidence", None)

    assert evidence_type is not None
    assert tuple(inspect.signature(evidence_type).parameters) == (
        "operation",
        "provider",
        "request_id",
        "model",
        "input_sha256",
        "output_sha256",
        "usage",
        "currency",
        "estimated_cost",
        "reconciled_cost",
        "latency_ms",
        "retry_count",
        "occurred_at",
        "evidence_kind",
    )


def test_transcription_result_constructor_and_alias_remain_compatible() -> None:
    """Catch the new evidence contract changing the established transcription API."""
    assert contracts.TranscriptionResult is contracts.TranscriptResult
    assert tuple(inspect.signature(contracts.TranscriptResult).parameters) == (
        "text",
        "words",
        "provider",
        "model",
        "usage",
        "request_id",
        "checksum",
        "cost",
        "confidence",
        "mode",
    )
