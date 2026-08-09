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
from poddown.audio.diagnostics import (
    AudioDiagnostics,
    AudioDiagnosticsError,
    diagnose_wav,
)
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.orchestration import (
    LocalEpisodeWorkflowService,
    LocalOrchestrationError,
    TemporalEpisodeWorkflowService,
)
from poddown.audio.render import DurableRenderService, RenderRejectedError
from poddown.audio.rights import RightsDeniedError, VoiceConsent, require_render_rights
from poddown.audio.selection import (
    CandidateQuality,
    CandidateSelectionError,
    rank_candidates,
    select_candidate,
)
from poddown.audio.workflow import (
    EpisodeRenderWorkflow,
    EpisodeWorkflowInput,
    EpisodeWorkflowResult,
    MalformedAudioError,
    RightsFailureError,
    SegmentDecision,
    SegmentWorkflowInput,
    TransientActivityError,
    WorkflowContractError,
    WorkflowFailure,
    activity_key_for,
    workflow_id_for,
)

__all__ = [
    "ArtifactRef",
    "AudioRenderer",
    "AudioDiagnostics",
    "AudioDiagnosticsError",
    "CandidateQuality",
    "CandidateSelectionError",
    "DeterministicLocalRenderer",
    "DurableRenderService",
    "EpisodeRenderWorkflow",
    "EpisodeWorkflowInput",
    "EpisodeWorkflowResult",
    "LocalEpisodeWorkflowService",
    "LocalOrchestrationError",
    "MalformedAudioError",
    "ProviderCostEvent",
    "RenderCandidate",
    "RenderedAudio",
    "RenderOutcome",
    "RenderRequest",
    "RenderRejectedError",
    "RightsDeniedError",
    "RightsFailureError",
    "SegmentDecision",
    "SegmentWorkflowInput",
    "TemporalEpisodeWorkflowService",
    "TransientActivityError",
    "VoiceConsent",
    "WorkflowContractError",
    "WorkflowFailure",
    "activity_key_for",
    "diagnose_wav",
    "require_render_rights",
    "rank_candidates",
    "select_candidate",
    "workflow_id_for",
]
