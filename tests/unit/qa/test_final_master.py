"""Unit coverage for final-master transcription and fidelity QA."""

import asyncio
import wave
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

import pytest

from poddown.audio.diagnostics import AudioDiagnostics, diagnose_wav
from poddown.audio.mastering import MasteredAudio, MasteringProfile, MasteringProvenance
from poddown.domain import ProviderUsage
from poddown.providers.contracts import TranscriptResult
from poddown.providers.http import ProviderRateLimited, ProviderRequestFailed
from poddown.qa.final_master import (
    FinalMasterGate,
    FinalMasterQaError,
    FinalMasterQaService,
    FinalMasterQaTransientError,
)


def wav_bytes(
    *, sample_rate: int = 44_100, samples: tuple[int, ...] = (1_000,) * 882
) -> bytes:
    """Build deterministic mono PCM WAV bytes without an external encoder."""
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(
            b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples)
        )
    return stream.getvalue()


PROFILE = MasteringProfile(max_duration_seconds=10.0, version="spoken-word-v1")
MASTER_WAV = wav_bytes()


def mastered_audio(
    *,
    audio: bytes = MASTER_WAV,
    profile_version: str = PROFILE.version,
    inspection_profile: MasteringProfile = PROFILE,
) -> MasteredAudio:
    """Build fully populated verified-master evidence for a focused override."""
    diagnostics = diagnose_wav(
        audio,
        expected_sample_rate_hz=inspection_profile.sample_rate_hz,
        expected_channels=inspection_profile.channels,
        min_duration_seconds=inspection_profile.min_duration_seconds,
        max_duration_seconds=inspection_profile.max_duration_seconds,
    )
    return MasteredAudio(
        episode_id="episode-1",
        episode_version="v1",
        wav_bytes=audio,
        mp3_bytes=b"ID3-deterministic-master",
        diagnostics=diagnostics,
        provenance=MasteringProvenance(
            profile_version=profile_version,
            ffmpeg_executable="fake-ffmpeg",
            ffmpeg_version="fake-1.0",
            command=("fake-ffmpeg", "-i", "input.wav"),
            filters=("volume=1.0",),
            input_checksums=("a" * 64,),
            wav_checksum=sha256(audio).hexdigest(),
            mp3_checksum=sha256(b"ID3-deterministic-master").hexdigest(),
            wav_metadata=diagnostics.to_dict(),
            mp3_metadata={"codec_name": "mp3"},
            commands=(("fake-ffmpeg", "-i", "input.wav"),),
        ),
    )


@dataclass
class FakeTranscriber:
    """Return controlled asynchronous provider evidence while recording audio."""

    text: str = "PodDown verifies final master"
    checksum: str | None = None
    error: Exception | None = None
    evidence: object | None = None
    calls: list[bytes] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        assert self.calls is not None
        self.calls.append(audio)
        if self.error is not None:
            raise self.error
        if self.evidence is not None:
            return self.evidence  # type: ignore[return-value]
        return TranscriptResult(
            text=self.text,
            words=(),
            provider="fake-transcriber",
            model="fake-model-v1",
            usage=ProviderUsage(12, 4),
            request_id="fake-request-1",
            checksum=self.checksum or sha256(audio).hexdigest(),
            cost=Decimal("0.0042"),
        )


@dataclass(frozen=True)
class MalformedTranscriptEvidence:
    """Provider-shaped invalid evidence that cannot be constructed as a contract."""

    text: str = " "


def evaluate(
    transcriber: FakeTranscriber,
    *,
    master: MasteredAudio | None = None,
    profile: MasteringProfile = PROFILE,
    critical_tokens: tuple[str, ...] = ("PodDown", "final master"),
):
    """Run the real final-master boundary with a deterministic provider fake."""
    return asyncio.run(
        FinalMasterQaService(transcriber).evaluate(
            master or mastered_audio(), profile, critical_tokens
        )
    )


def test_gate_serializes_only_schema_compatible_fields():
    """Catch gate serialization that leaks typed implementation evidence."""
    gate = FinalMasterGate(
        name="audio",
        passed=True,
        evidence={"sample_rate_hz": 44_100},
    )

    assert gate.to_dict() == {
        "name": "audio",
        "passed": True,
        "evidence": {"sample_rate_hz": 44_100},
    }


def test_passing_master_result_serializes_schema_record_and_bound_evidence():
    """Catch a successful master QA result that omits schema or provider evidence."""
    master = mastered_audio()
    transcriber = FakeTranscriber()

    result = evaluate(transcriber, master=master)

    assert transcriber.calls == [master.wav_bytes]
    assert result.passed is True
    assert result.fidelity.accuracy == 1.0
    assert result.master_checksum == sha256(master.wav_bytes).hexdigest()
    assert result.transcript.checksum == result.master_checksum
    assert result.usage == ProviderUsage(12, 4)
    assert result.cost == Decimal("0.0042")
    assert result.to_dict() == {
        "subject_id": "episode-1",
        "stage": "master",
        "passed": True,
        "critical_token_accuracy": 1.0,
        "gates": [gate.to_dict() for gate in result.gates],
        "scores": {"critical_token_accuracy": 1.0},
    }


def test_missing_critical_token_returns_failed_master_record():
    """Catch a fidelity gate that treats partial critical-token coverage as passing."""
    result = evaluate(FakeTranscriber(text="PodDown verifies narration"))

    assert result.passed is False
    assert result.fidelity.accuracy == 0.5
    assert result.to_dict()["passed"] is False
    assert result.to_dict()["critical_token_accuracy"] == 0.5


def test_transcript_for_different_bytes_fails_before_fidelity_evaluation():
    """Catch scoring of a transcript whose checksum is not bound to the master."""
    transcriber = FakeTranscriber(checksum=sha256(b"other-master").hexdigest())

    with pytest.raises(FinalMasterQaError, match="checksum"):
        evaluate(transcriber)


@pytest.mark.parametrize(
    "master, profile",
    [
        (mastered_audio(profile_version="spoken-word-v0"), PROFILE),
        (
            mastered_audio(
                audio=wav_bytes(sample_rate=48_000),
                inspection_profile=MasteringProfile(
                    sample_rate_hz=48_000, max_duration_seconds=10.0
                ),
            ),
            PROFILE,
        ),
    ],
)
def test_invalid_profile_or_media_fails_before_transcriber_dispatch(master, profile):
    """Catch preflight validation that dispatches invalid master audio to a provider."""
    transcriber = FakeTranscriber()

    with pytest.raises(FinalMasterQaError, match="profile|media|sample rate"):
        evaluate(transcriber, master=master, profile=profile)

    assert transcriber.calls == []


@pytest.mark.parametrize(
    "provider_error",
    [TimeoutError("provider timeout"), ProviderRateLimited("provider rate limited")],
)
def test_retryable_provider_errors_map_to_transient_final_master_errors(provider_error):
    """Catch provider timeouts or rate limits incorrectly marked terminal."""
    with pytest.raises(FinalMasterQaTransientError):
        evaluate(FakeTranscriber(error=provider_error))


def test_terminal_provider_error_maps_to_non_retryable_final_master_error():
    """Catch terminal provider failures incorrectly exposed as retryable work."""
    with pytest.raises(FinalMasterQaError):
        evaluate(FakeTranscriber(error=ProviderRequestFailed("bad request")))


def test_malformed_or_empty_transcript_evidence_is_terminal():
    """Catch malformed provider evidence being accepted or retried indefinitely."""
    with pytest.raises(FinalMasterQaError, match="evidence|transcript"):
        evaluate(FakeTranscriber(evidence=MalformedTranscriptEvidence()))


def test_equivalent_master_evidence_has_stable_serialization():
    """Catch nondeterministic ordering or serialization for equivalent QA evidence."""
    first_master = mastered_audio()
    equivalent_master = replace(
        first_master,
        diagnostics=AudioDiagnostics(**first_master.diagnostics.to_dict()),
    )

    first = evaluate(FakeTranscriber(), master=first_master)
    equivalent = evaluate(FakeTranscriber(), master=equivalent_master)

    assert first.master_checksum == equivalent.master_checksum
    assert first.to_dict() == equivalent.to_dict()
