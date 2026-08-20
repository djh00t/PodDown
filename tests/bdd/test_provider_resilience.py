"""BDD bindings for bounded provider transport resilience."""

import asyncio

import pytest
from pytest_bdd import given, scenarios, then, when

import poddown.providers.http as provider_http
from poddown.providers.http import (
    HttpRequest,
    HttpResponse,
    ProviderRateLimited,
    require_success,
)

scenarios("../features/provider_resilience.feature")


@given("a provider rate-limit response with Retry-After 3 seconds")
def rate_limited_response(context) -> None:
    """Provide a sanitized provider rate-limit response."""
    context.values["response"] = HttpResponse(429, {"Retry-After": "3"}, b"")


@when("the provider response is classified")
def classify_provider_response(context) -> None:
    """Classify the response through the real provider HTTP boundary."""
    response = context.values["response"]
    assert isinstance(response, HttpResponse)
    with pytest.raises(ProviderRateLimited) as error:
        require_success(response, "test-provider")
    context.values["error"] = error.value


@then("the rate-limit error exposes a retry delay of 3 seconds")
def retry_delay_is_observable(context) -> None:
    """Expose server backoff metadata without retaining provider payloads."""
    error = context.values["error"]
    assert isinstance(error, ProviderRateLimited)
    assert error.retry_after_seconds == 3.0


class _Clock:
    """Deterministic clock and sleep recorder for resilience scenarios."""

    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def monotonic(self) -> float:
        """Return the deterministic current time."""
        return self.now

    async def sleep(self, delay: float) -> None:
        """Advance deterministic time without wall-clock waiting."""
        self.delays.append(delay)
        self.now += delay


class _ScriptedTransport:
    """Offline transport that returns or raises a supplied response sequence."""

    def __init__(self, outcomes: list[HttpResponse | BaseException]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def request(self, request: HttpRequest) -> HttpResponse:
        """Record dispatch and return the next deterministic outcome."""
        assert request.url == "https://provider.example.test/operation"
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _request() -> HttpRequest:
    """Build a sanitized provider request for BDD scenarios."""
    return HttpRequest("POST", "https://provider.example.test/operation", {})


@given("a provider transport that times out before succeeding")
def timeout_before_success(context) -> None:
    """Provide an offline transient failure followed by success."""
    context.values["clock"] = _Clock()
    context.values["transport"] = _ScriptedTransport(
        [
            TimeoutError("timeout"),
            HttpResponse(200, {"x-request-id": "request-2"}, b"ok"),
        ]
    )


@when("the resilient provider transport dispatches the request")
def dispatch_resilient_request(context) -> None:
    """Use the public transport wrapper with deterministic timing."""
    clock = context.values["clock"]
    transport = context.values["transport"]
    assert isinstance(clock, _Clock)
    assert isinstance(transport, _ScriptedTransport)
    resilient = provider_http.ResilientTransport(
        transport,
        max_attempts=2,
        initial_backoff_seconds=1,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )
    context.values["response"] = asyncio.run(resilient.request(_request()))


@then("the request succeeds after 2 attempts and one 1-second delay")
def request_succeeds_after_bounded_retry(context) -> None:
    """Assert the observable bounded dispatch result rather than mock calls alone."""
    response = context.values["response"]
    clock = context.values["clock"]
    assert isinstance(response, HttpResponse)
    assert isinstance(clock, _Clock)
    assert response.attempt_count == 2
    assert response.headers["x-request-id"] == "request-2"
    assert clock.delays == [1.0]


@given("a provider circuit opened by consecutive transient failures")
def opened_provider_circuit(context) -> None:
    """Open a circuit through real bounded transient dispatches."""
    clock = _Clock()
    transport = _ScriptedTransport([TimeoutError("one"), TimeoutError("two")])
    resilient = provider_http.ResilientTransport(
        transport,
        max_attempts=1,
        circuit_failure_threshold=2,
        circuit_cooldown_seconds=10,
        clock=clock.monotonic,
        sleep=clock.sleep,
    )
    for _ in range(2):
        with pytest.raises(TimeoutError):
            asyncio.run(resilient.request(_request()))
    context.values.update(clock=clock, resilient=resilient, transport=transport)


@when("another provider request is dispatched before cooldown")
def dispatch_while_circuit_open(context) -> None:
    """Try the public transport wrapper while the circuit remains open."""
    resilient = context.values["resilient"]
    assert isinstance(resilient, provider_http.ResilientTransport)
    with pytest.raises(provider_http.ProviderCircuitOpen):
        asyncio.run(resilient.request(_request()))


@then("the circuit rejects the request without provider dispatch")
def circuit_rejects_without_dispatch(context) -> None:
    """Prove fail-closed behavior has no hidden transport call."""
    transport = context.values["transport"]
    assert isinstance(transport, _ScriptedTransport)
    assert transport.calls == 2
