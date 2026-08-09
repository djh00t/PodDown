"""Public immutable durable-audio contracts and rights policy."""

from poddown.audio.activities import (
    ActivityHandler,
    QualityEvaluator,
    build_durable_render_activity,
    build_transcription_quality_evaluator,
    deterministic_quality_evaluator,
)
from poddown.audio.contracts import (
    ArtifactRef,
    AudioRenderer,
    ProviderCostEvent,
    RenderCandidate,
    RenderedAudio,
    RenderOutcome,
    RenderRequest,
    TranscriptionCostEvent,
    TranscriptionRecord,
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
    TranscriptionFailureError,
    TranscriptionTransientError,
    TransientActivityError,
    WorkflowContractError,
    WorkflowFailure,
    activity_key_for,
    workflow_id_for,
)
from poddown.providers.contracts import Transcriber, TranscriptResult, TranscriptWord

__all__ = [
    "ArtifactRef",
    "ActivityHandler",
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
    "TranscriptionCostEvent",
    "TranscriptionRecord",
    "QualityEvaluator",
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
    "TranscriptResult",
    "TranscriptWord",
    "Transcriber",
    "TranscriptionFailureError",
    "TranscriptionTransientError",
    "VoiceConsent",
    "WorkflowContractError",
    "WorkflowFailure",
    "activity_key_for",
    "build_durable_render_activity",
    "build_transcription_quality_evaluator",
    "diagnose_wav",
    "deterministic_quality_evaluator",
    "require_render_rights",
    "rank_candidates",
    "select_candidate",
    "workflow_id_for",
]
