"""Public immutable durable-audio contracts and rights policy."""

from poddown.audio.contracts import (
    ArtifactRef,
    AudioRenderer,
    ProviderCostEvent,
    RenderCandidate,
    RenderedAudio,
    RenderOutcome,
    RenderRequest,
)
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService, RenderRejectedError
from poddown.audio.rights import RightsDeniedError, VoiceConsent, require_render_rights

__all__ = [
    "ArtifactRef",
    "AudioRenderer",
    "DeterministicLocalRenderer",
    "DurableRenderService",
    "ProviderCostEvent",
    "RenderCandidate",
    "RenderedAudio",
    "RenderOutcome",
    "RenderRequest",
    "RenderRejectedError",
    "RightsDeniedError",
    "VoiceConsent",
    "require_render_rights",
]
