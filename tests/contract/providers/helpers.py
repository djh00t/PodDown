"""Provider-contract fixtures that contain structurally valid audio."""

import wave
from io import BytesIO

from pydantic import SecretStr

from poddown.providers.http import ProviderSettings


def settings(key: str = "test-secret") -> ProviderSettings:
    """Build explicitly redacted settings for offline contract tests."""
    return ProviderSettings(api_key=SecretStr(key))


def mono_pcm_wave(sample_rate: int, frames: int = 8) -> bytes:
    """Create a minimal valid mono 16-bit PCM WAV fixture."""
    target = BytesIO()
    with wave.open(target, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x00\x00" * frames)
    return target.getvalue()
