"""Minimal injected HTTP contracts shared by provider adapters."""

import math
import wave
from asyncio import sleep as asyncio_sleep
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from io import BytesIO
from time import monotonic
from typing import Any, Protocol

from pydantic import SecretStr


class ProviderRateLimited(RuntimeError):
    """A provider rejected a request because its rate limit was reached."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        """Retain only a validated server retry delay for bounded backoff."""
        super().__init__(message)
        self.retry_after_seconds: float | None = retry_after_seconds


class ProviderRequestFailed(RuntimeError):
    """A terminal provider request failure safe to expose to callers."""


class ProviderCircuitOpen(RuntimeError):
    """A provider circuit is open after bounded transient failures."""

    def __init__(self, retry_after_seconds: float) -> None:
        """Expose only the cooldown remaining before the next permitted dispatch."""
        super().__init__("provider circuit is open")
        self.retry_after_seconds: float = retry_after_seconds


@dataclass(frozen=True)
class ProviderSettings:
    """Validated, redacted provider secrets and bounded dispatch controls."""

    api_key: SecretStr
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        validate_api_credential(self.api_key.get_secret_value())
        if (
            not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 300
        ):
            raise ValueError("provider timeout must be finite and between 0 and 300")

    @classmethod
    def from_environment(
        cls,
        env_name: str,
        environment: Mapping[str, str],
        timeout_seconds: float = 30.0,
    ) -> "ProviderSettings":
        """Load one credential from an injected environment mapping."""
        value = environment.get(env_name, "")
        return cls(
            api_key=SecretStr(validate_api_credential(value)),
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class HttpRequest:
    """Provider HTTP request independent of any HTTP client library."""

    method: str
    url: str
    headers: dict[str, str]
    json: dict[str, Any] | None = None
    form: dict[str, str] = field(default_factory=dict)
    files: dict[str, tuple[str, bytes, str]] = field(default_factory=dict)
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class HttpResponse:
    """Provider HTTP response independent of any HTTP client library."""

    status: int
    headers: dict[str, str]
    body: bytes
    attempt_count: int = 1


class AsyncHttpTransport(Protocol):
    """Injected async transport used by provider contract and production code."""

    async def request(self, request: HttpRequest) -> HttpResponse:
        """Execute one HTTP request without applying hidden retries."""


class ResilientTransport:
    """Apply bounded transient retry and circuit-breaking to an injected transport."""

    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        max_attempts: int = 1,
        initial_backoff_seconds: float = 0.1,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio_sleep,
    ) -> None:
        """Configure deterministic, bounded resilience without hidden dispatches."""
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if not math.isfinite(initial_backoff_seconds) or initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must be finite and non-negative")
        if (
            not isinstance(circuit_failure_threshold, int)
            or circuit_failure_threshold < 1
        ):
            raise ValueError("circuit_failure_threshold must be a positive integer")
        if not math.isfinite(circuit_cooldown_seconds) or circuit_cooldown_seconds <= 0:
            raise ValueError("circuit_cooldown_seconds must be finite and positive")
        self._transport = transport
        self._max_attempts = max_attempts
        self._initial_backoff_seconds = initial_backoff_seconds
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_cooldown_seconds = circuit_cooldown_seconds
        self._clock = clock
        self._sleep = sleep
        self._consecutive_transient_failures = 0
        self._opened_at: float | None = None

    async def request(self, request: HttpRequest) -> HttpResponse:
        """Dispatch with bounded retry and no retry for terminal failures."""
        self._reject_if_circuit_open()
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._transport.request(request)
                require_success(response, "provider")
            except (ProviderRateLimited, TimeoutError) as error:
                if attempt == self._max_attempts:
                    self._record_transient_failure()
                    raise
                await self._sleep(self._retry_delay(error, attempt))
            except Exception:
                self._reset_circuit()
                raise ProviderRequestFailed("provider request failed") from None
            else:
                self._reset_circuit()
                return replace(response, attempt_count=attempt)
        raise AssertionError("bounded provider retry loop exhausted unexpectedly")

    def _reject_if_circuit_open(self) -> None:
        """Fail closed until the configured cooldown has completely elapsed."""
        if self._opened_at is None:
            return
        remaining = self._circuit_cooldown_seconds - (self._clock() - self._opened_at)
        if remaining > 0:
            raise ProviderCircuitOpen(remaining)
        self._reset_circuit()

    def _record_transient_failure(self) -> None:
        """Open the circuit after the configured number of failed operations."""
        self._consecutive_transient_failures += 1
        if self._consecutive_transient_failures >= self._circuit_failure_threshold:
            self._opened_at = self._clock()

    def _reset_circuit(self) -> None:
        """Clear transient-failure state after success or terminal failure."""
        self._consecutive_transient_failures = 0
        self._opened_at = None

    def _retry_delay(self, error: BaseException, attempt: int) -> float:
        """Use server Retry-After when present, otherwise exponential backoff."""
        backoff = self._initial_backoff_seconds * (2 ** (attempt - 1))
        retry_after = (
            error.retry_after_seconds
            if isinstance(error, ProviderRateLimited)
            else None
        )
        return float(max(backoff, retry_after or 0.0))


def validate_api_credential(value: str) -> str:
    """Reject absent or well-known placeholder credentials before dispatch."""
    credential = value.strip()
    if not credential or credential.casefold() in {
        "changeme",
        "replace-me",
        "your-api-key",
    }:
        raise ValueError("provider credential is missing or a placeholder")
    return credential


def require_success(response: HttpResponse, provider: str) -> None:
    """Classify an HTTP response without exposing credentials or response bodies."""
    if response.status == 429:
        raise ProviderRateLimited(
            f"{provider} rate limited the request",
            retry_after_seconds=_retry_after_seconds(response.headers),
        )
    if response.status in {408, 500, 502, 503, 504}:
        raise TimeoutError(f"{provider} transient provider failure")
    if not 200 <= response.status < 300:
        raise ProviderRequestFailed(
            f"{provider} request failed with status {response.status}"
        )


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """Return a finite non-negative numeric Retry-After delay when supplied."""
    value = next(
        (
            header_value
            for header_name, header_value in headers.items()
            if header_name.casefold() == "retry-after"
        ),
        None,
    )
    if value is None:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    return delay if math.isfinite(delay) and delay >= 0 else None


def require_wave_audio(audio: bytes, provider: str, expected_sample_rate: int) -> None:
    """Require non-empty mono PCM WAV at the declared sample rate."""
    try:
        with wave.open(BytesIO(audio), "rb") as stream:
            frame_count = stream.getnframes()
            channels = stream.getnchannels()
            sample_width = stream.getsampwidth()
            frames = stream.readframes(frame_count)
            valid = (
                stream.getcomptype() == "NONE"
                and channels == 1
                and sample_width in {2, 3, 4}
                and stream.getframerate() == expected_sample_rate
                and frame_count > 0
                and len(frames) == frame_count * channels * sample_width
            )
    except (EOFError, wave.Error):
        valid = False
    if not valid:
        raise ValueError(f"{provider} returned malformed or incompatible audio")
