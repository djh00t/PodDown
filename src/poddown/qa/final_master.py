"""Final-master transcription, fidelity, and quality-gate verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from types import MappingProxyType
from typing import cast

from poddown.audio.diagnostics import (
    AudioDiagnostics,
    AudioDiagnosticsError,
    diagnose_wav,
)
from poddown.audio.mastering import MasteredAudio, MasteringProfile, MasteringProvenance
from poddown.audio.workflow import (
    TranscriptionFailureError,
    TranscriptionTransientError,
)
from poddown.domain import FidelityResult, ProviderUsage
from poddown.providers.contracts import Transcriber, TranscriptResult
from poddown.providers.http import ProviderRateLimited, ProviderRequestFailed


class FinalMasterQaError(TranscriptionFailureError):
    """Raised when final-master evidence is malformed or fails a terminal gate."""


class FinalMasterQaTransientError(TranscriptionTransientError):
    """Raised when final-master transcription can safely be retried."""


@dataclass(frozen=True)
class FinalMasterGate:
    """One schema-compatible final-master quality gate and its evidence."""

    name: str
    passed: bool
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise FinalMasterQaError("final-master gate name must be non-empty")
        if type(self.passed) is not bool:
            raise FinalMasterQaError("final-master gate result must be boolean")
        if not isinstance(self.evidence, Mapping):
            raise FinalMasterQaError("final-master gate evidence must be a mapping")
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def to_dict(self) -> dict[str, object]:
        """Return only the fields permitted by the QA result schema."""
        return {
            "name": self.name,
            "passed": self.passed,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class FinalMasterQaResult:
    """Immutable final-master outcome with provider evidence kept in memory."""

    subject_id: str
    master_checksum: str
    transcript: TranscriptResult
    fidelity: FidelityResult
    gates: tuple[FinalMasterGate, ...]
    diagnostics: AudioDiagnostics

    @property
    def passed(self) -> bool:
        """Return whether every final-master gate passed."""
        return self.fidelity.passed and all(gate.passed for gate in self.gates)

    @property
    def critical_token_accuracy(self) -> float:
        """Return the critical-token score used by the QA schema."""
        return self.fidelity.accuracy

    @property
    def usage(self) -> ProviderUsage:
        """Return provider usage recorded by the bound transcript."""
        return self.transcript.usage

    @property
    def cost(self) -> Decimal:
        """Return provider cost recorded by the bound transcript."""
        return self.transcript.cost

    def to_dict(self) -> dict[str, object]:
        """Return the stable schema-compatible QA record only."""
        accuracy = self.critical_token_accuracy
        return {
            "subject_id": self.subject_id,
            "stage": "master",
            "passed": self.passed,
            "critical_token_accuracy": accuracy,
            "gates": [gate.to_dict() for gate in self.gates],
            "scores": {"critical_token_accuracy": accuracy},
        }


class FinalMasterQaService:
    """Preflight and transcribe one exact deterministic final master."""

    def __init__(self, transcriber: Transcriber) -> None:
        if not hasattr(transcriber, "transcribe"):
            raise FinalMasterQaError("final-master transcriber is not configured")
        self._transcriber = transcriber

    async def evaluate(
        self,
        mastered_audio: MasteredAudio,
        profile: MasteringProfile,
        critical_tokens: tuple[str, ...],
    ) -> FinalMasterQaResult:
        """Return final-master QA or fail closed before unsafe dispatch."""
        checksum, diagnostics = self._preflight(
            mastered_audio, profile, critical_tokens
        )
        transcript = await self._transcribe(mastered_audio.wav_bytes)
        if transcript.checksum != checksum:
            raise FinalMasterQaError(
                "final-master transcription checksum does not match master"
            )

        from poddown.qa.fidelity import evaluate_critical_tokens

        fidelity = evaluate_critical_tokens(critical_tokens, transcript.text)
        gates = (
            FinalMasterGate(
                name="audio",
                passed=diagnostics.peak_amplitude <= profile.max_peak_amplitude
                and diagnostics.clipping_ratio <= profile.max_clipping_ratio,
                evidence=cast(dict[str, object], diagnostics.to_dict()),
            ),
            FinalMasterGate(
                name="critical_tokens",
                passed=fidelity.passed,
                evidence={
                    "accuracy": fidelity.accuracy,
                    "rerender_scope": fidelity.rerender_scope,
                },
            ),
        )
        return FinalMasterQaResult(
            subject_id=mastered_audio.episode_id,
            master_checksum=checksum,
            transcript=transcript,
            fidelity=fidelity,
            gates=gates,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _preflight(
        mastered_audio: MasteredAudio,
        profile: MasteringProfile,
        critical_tokens: tuple[str, ...],
    ) -> tuple[str, AudioDiagnostics]:
        if not isinstance(mastered_audio, MasteredAudio):
            raise FinalMasterQaError("final-master media is malformed")
        if not isinstance(profile, MasteringProfile):
            raise FinalMasterQaError("final-master profile is malformed")
        if not isinstance(critical_tokens, tuple) or any(
            not isinstance(token, str) or not token.strip() for token in critical_tokens
        ):
            raise FinalMasterQaError("final-master critical tokens are malformed")
        if not isinstance(mastered_audio.provenance, MasteringProvenance):
            raise FinalMasterQaError("final-master provenance is malformed")
        if mastered_audio.provenance.profile_version != profile.version:
            raise FinalMasterQaError(
                "final-master profile version does not match master provenance"
            )
        if (
            not isinstance(mastered_audio.wav_bytes, bytes)
            or not mastered_audio.wav_bytes
        ):
            raise FinalMasterQaError("final-master media is malformed")

        checksum = sha256(mastered_audio.wav_bytes).hexdigest()
        if mastered_audio.provenance.wav_checksum != checksum:
            raise FinalMasterQaError("final-master media checksum is not bound")
        try:
            diagnostics = diagnose_wav(
                mastered_audio.wav_bytes,
                expected_sample_rate_hz=profile.sample_rate_hz,
                expected_channels=profile.channels,
                min_duration_seconds=profile.min_duration_seconds,
                max_duration_seconds=profile.max_duration_seconds,
            )
        except AudioDiagnosticsError as error:
            raise FinalMasterQaError(
                "final-master media/profile preflight failed"
            ) from error
        if (
            diagnostics.peak_amplitude > profile.max_peak_amplitude
            or diagnostics.clipping_ratio > profile.max_clipping_ratio
        ):
            raise FinalMasterQaError("final-master media quality gates failed")
        return checksum, diagnostics

    async def _transcribe(self, audio: bytes) -> TranscriptResult:
        try:
            transcript = await self._transcriber.transcribe(audio)
        except (ProviderRateLimited, TimeoutError) as error:
            raise FinalMasterQaTransientError(
                "final-master transcription provider is temporarily unavailable"
            ) from error
        except (ProviderRequestFailed, ValueError, TypeError) as error:
            raise FinalMasterQaError(
                "final-master transcription provider returned terminal failure"
            ) from error
        except Exception as error:
            raise FinalMasterQaError(
                "final-master transcription provider returned terminal failure"
            ) from error
        if not isinstance(transcript, TranscriptResult) or not transcript.text.strip():
            raise FinalMasterQaError("final-master transcription evidence is malformed")
        return transcript


__all__ = [
    "FinalMasterGate",
    "FinalMasterQaError",
    "FinalMasterQaResult",
    "FinalMasterQaService",
    "FinalMasterQaTransientError",
]
