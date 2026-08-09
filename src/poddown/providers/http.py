"""Minimal injected HTTP contracts shared by provider adapters."""

import math
import wave
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Protocol

from pydantic import SecretStr


class ProviderRateLimited(RuntimeError):
    """A provider rejected a request because its rate limit was reached."""


class ProviderRequestFailed(RuntimeError):
    """A terminal provider request failure safe to expose to callers."""


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


class AsyncHttpTransport(Protocol):
    """Injected async transport used by provider contract and production code."""

    async def request(self, request: HttpRequest) -> HttpResponse:
        """Execute one HTTP request without applying hidden retries."""


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
        raise ProviderRateLimited(f"{provider} rate limited the request")
    if response.status in {408, 500, 502, 503, 504}:
        raise TimeoutError(f"{provider} transient provider failure")
    if not 200 <= response.status < 300:
        raise ProviderRequestFailed(
            f"{provider} request failed with status {response.status}"
        )


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
