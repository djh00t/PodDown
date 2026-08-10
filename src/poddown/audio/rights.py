"""Fail-closed voice-consent policy for audio rendering."""

from dataclasses import dataclass

from poddown.audio.contracts import RenderRequest


class RightsDeniedError(ValueError):
    """Raised when voice consent does not authorize a render request."""


@dataclass(frozen=True)
class VoiceConsent:
    """Evidence-backed consent for an asset and explicit provider allow-list."""

    voice_asset_id: str
    evidence_id: str
    allowed_providers: frozenset[str]
    valid: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.voice_asset_id, str) or not self.voice_asset_id:
            raise ValueError("voice_asset_id must be a non-empty string")
        if not isinstance(self.evidence_id, str):
            raise ValueError("evidence_id must be a string")
        if not isinstance(self.allowed_providers, frozenset) or any(
            not isinstance(provider, str) or not provider
            for provider in self.allowed_providers
        ):
            raise ValueError(
                "allowed_providers must be a frozenset of non-empty strings"
            )
        if type(self.valid) is not bool:
            raise ValueError("valid must be a boolean")


def require_render_rights(request: RenderRequest, consent: VoiceConsent | None) -> None:
    """Require complete, current, matching consent before any renderer dispatch."""
    if consent is None:
        raise RightsDeniedError("missing voice consent")
    if consent.valid is not True:
        raise RightsDeniedError("invalid voice consent")
    if not consent.evidence_id:
        raise RightsDeniedError("missing consent evidence")
    if consent.voice_asset_id != request.voice_asset_id:
        raise RightsDeniedError("voice asset does not match consent")
    if request.provider not in consent.allowed_providers:
        raise RightsDeniedError("provider is not allowed by consent")
