"""Unit coverage for deterministic provider transport resilience."""

import asyncio

import pytest

import poddown.providers.http as provider_http
from poddown.providers.http import (
    HttpRequest,
    HttpResponse,
    ProviderRateLimited,
    require_success,
)


def test_rate_limit_classification_retains_a_numeric_retry_after_delay() -> None:
    """Catch loss of the provider's safe retry delay before a retry is scheduled."""
    response = HttpResponse(429, {"Retry-After": "3"}, b"")

    try:
        require_success(response, "test-provider")
    except ProviderRateLimited as error:
        assert error.retry_after_seconds == 3.0
    else:
        raise AssertionError("a rate-limited response must fail")


class _Clock:
    """Deterministic time and sleep implementation for transport tests."""

    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        """Return the controlled current time."""
        return self.now

    async def sleep(self, delay: float) -> None:
        """Record sleep and advance controlled time without waiting."""
        self.delays.append(delay)
        self.now += delay


class _ScriptedTransport:
    """Offline transport that records requests and consumes known outcomes."""

    def __init__(self, outcomes: list[HttpResponse | BaseException]) -> None:
        self._outcomes = outcomes
        self.requests: list[HttpRequest] = []

    async def request(self, request: HttpRequest) -> HttpResponse:
        """Dispatch one deterministic transport outcome."""
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _request() -> HttpRequest:
    """Build one secret-free provider request."""
    return HttpRequest("POST", "https://provider.example.test/operation", {})


def test_retries_rate_limits_using_retry_after_without_exceeding_attempt_bound() -> (
    None
):
    """Catch retry schedules that ignore server delay or send one extra request."""
    clock = _Clock()
    transport = _ScriptedTransport(
        [
            HttpResponse(429, {"Retry-After": "3", "x-request-id": "first"}, b""),
            HttpResponse(200, {"x-request-id": "second"}, b"ok"),
        ]
    )

    resilient = provider_http.ResilientTransport(
        transport,
        max_attempts=2,
        initial_backoff_seconds=1,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )
    response = asyncio.run(resilient.request(_request()))

    assert response.attempt_count == 2
    assert response.headers["x-request-id"] == "second"
    assert clock.delays == [3.0]
    assert len(transport.requests) == 2


def test_does_not_retry_terminal_provider_failures() -> None:
    """Catch accidental redispatch after malformed or permission-style HTTP failure."""
    transport = _ScriptedTransport([HttpResponse(403, {}, b"denied")])
    resilient = provider_http.ResilientTransport(transport, max_attempts=3)

    with pytest.raises(provider_http.ProviderRequestFailed):
        asyncio.run(resilient.request(_request()))

    assert len(transport.requests) == 1


def test_sanitizes_injected_transport_exception_without_retrying() -> None:
    """Catch raw transport exception details escaping the provider boundary."""
    exception_text = (
        "GET https://provider.example.test/private?token=secret "
        "Authorization: Bearer secret"
    )
    transport = _ScriptedTransport([RuntimeError(exception_text)])
    resilient = provider_http.ResilientTransport(transport, max_attempts=3)

    with pytest.raises(provider_http.ProviderRequestFailed) as error:
        asyncio.run(resilient.request(_request()))

    assert str(error.value) == "provider request failed"
    assert exception_text not in str(error.value)
    assert "https://provider.example.test" not in str(error.value)
    assert "Authorization" not in str(error.value)
    assert len(transport.requests) == 1


def test_open_circuit_fails_closed_and_recovers_after_cooldown() -> None:
    """Catch circuit state that dispatches early or never returns to service."""
    clock = _Clock()
    transport = _ScriptedTransport(
        [
            TimeoutError("first"),
            TimeoutError("second"),
            HttpResponse(200, {"x-request-id": "recovered"}, b"ok"),
        ]
    )
    resilient = provider_http.ResilientTransport(
        transport,
        max_attempts=1,
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=5,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(resilient.request(_request()))
    with pytest.raises(TimeoutError):
        asyncio.run(resilient.request(_request()))
    with pytest.raises(provider_http.ProviderCircuitOpen) as error:
        asyncio.run(resilient.request(_request()))
    assert error.value.retry_after_seconds == 5.0
    assert len(transport.requests) == 2

    clock.now = 5.0
    response = asyncio.run(resilient.request(_request()))

    assert response.headers["x-request-id"] == "recovered"
    assert response.attempt_count == 1
    assert len(transport.requests) == 3
