"""Executable acceptance tests for independent final-master QA."""

import asyncio
import json
import wave
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from importlib import import_module
from io import BytesIO

from pytest_bdd import given, parsers, scenarios, then, when

from poddown.audio.diagnostics import diagnose_wav
from poddown.audio.mastering import MasteredAudio, MasteringProfile, MasteringProvenance
from poddown.audio.workflow import (
    TranscriptionFailureError,
    TranscriptionTransientError,
)
from poddown.domain import ProviderUsage
from poddown.providers.contracts import TranscriptResult
from poddown.providers.http import ProviderRateLimited, ProviderRequestFailed

scenarios("../features/final_master_qa.feature")

PROFILE = MasteringProfile(
    sample_rate_hz=44_100,
    channels=1,
    min_duration_seconds=0.01,
    max_duration_seconds=10.0,
    version="spoken-word-v1",
)
QA_SCHEMA_FIELDS = {
    "subject_id",
    "stage",
    "passed",
    "critical_token_accuracy",
    "gates",
    "scores",
}


def _wav_bytes(*, sample: int = 600, sample_rate_hz: int = 44_100) -> bytes:
    """Build 20 ms of deterministic mono PCM WAV bytes using the stdlib."""
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate_hz)
        output.writeframes(sample.to_bytes(2, "little", signed=True) * 882)
    return stream.getvalue()


MASTER_WAV = _wav_bytes()


def _mastered_audio(wav_bytes: bytes = MASTER_WAV) -> MasteredAudio:
    diagnostics = diagnose_wav(
        wav_bytes,
        expected_sample_rate_hz=PROFILE.sample_rate_hz,
        expected_channels=PROFILE.channels,
    )
    checksum = sha256(wav_bytes).hexdigest()
    return MasteredAudio(
        episode_id="episode-001",
        episode_version="v1",
        wav_bytes=wav_bytes,
        mp3_bytes=b"ID3deterministic-master",
        diagnostics=diagnostics,
        provenance=MasteringProvenance(
            profile_version=PROFILE.version,
            ffmpeg_executable="fake-ffmpeg",
            ffmpeg_version="fake-7.0",
            command=("fake-ffmpeg",),
            filters=("aresample=44100",),
            input_checksums=(checksum,),
            wav_checksum=checksum,
            mp3_checksum=sha256(b"ID3deterministic-master").hexdigest(),
            wav_metadata=diagnostics.to_dict(),
            mp3_metadata={"codec_name": "mp3"},
        ),
    )


def _transcript(*, text: str, checksum: str) -> TranscriptResult:
    return TranscriptResult(
        text=text,
        words=(),
        provider="fake-transcriber",
        model="fake-v1",
        usage=ProviderUsage(input_units=1, output_units=4),
        request_id="request-001",
        checksum=checksum,
        cost=Decimal("0.001"),
        mode="test",
    )


@dataclass
class FakeTranscriber:
    """Provider-boundary fake retaining the exact bytes it receives."""

    result: TranscriptResult | None = None
    error: Exception | None = None
    calls: list[bytes] = field(default_factory=list)

    async def transcribe(self, audio: bytes) -> TranscriptResult:
        self.calls.append(audio)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _qa_module():
    try:
        return import_module("poddown.qa.final_master")
    except ModuleNotFoundError as error:
        raise AssertionError("Final-master QA is not implemented") from error


def _evaluate(context) -> None:
    qa = _qa_module()
    service = qa.FinalMasterQaService(transcriber=context.values["transcriber"])
    context.values["result"] = asyncio.run(
        service.evaluate(
            mastered_audio=context.values["master"],
            profile=PROFILE,
            critical_tokens=context.values["critical_tokens"],
        )
    )


@given(parsers.parse('a valid final master and a matching transcription of "{text}"'))
def valid_master_with_matching_transcription(context, text):
    master = _mastered_audio()
    context.values["master"] = master
    context.values["transcriber"] = FakeTranscriber(
        result=_transcript(text=text, checksum=sha256(master.wav_bytes).hexdigest())
    )


@given("a valid final master and a transcription with a different checksum")
def valid_master_with_wrong_checksum(context):
    master = _mastered_audio()
    context.values["master"] = master
    context.values["transcriber"] = FakeTranscriber(
        result=_transcript(
            text="C1 arrives", checksum=sha256(b"other audio").hexdigest()
        )
    )


@given("a malformed final master")
def malformed_final_master(context):
    diagnostics = diagnose_wav(MASTER_WAV, expected_sample_rate_hz=44_100)
    context.values["master"] = MasteredAudio(
        episode_id="episode-001",
        episode_version="v1",
        wav_bytes=b"not a WAV file",
        mp3_bytes=b"ID3deterministic-master",
        diagnostics=diagnostics,
        provenance=_mastered_audio().provenance,
    )
    context.values["transcriber"] = FakeTranscriber(
        result=_transcript(text="C1", checksum=sha256(b"not a WAV file").hexdigest())
    )


@given(parsers.parse("a valid final master and a transcriber that raises {failure}"))
def valid_master_with_provider_failure(context, failure):
    errors = {
        "timeout": TimeoutError("provider timed out"),
        "rate limit": ProviderRateLimited("provider rate limited"),
        "terminal error": ProviderRequestFailed("provider rejected request"),
    }
    context.values["master"] = _mastered_audio()
    context.values["transcriber"] = FakeTranscriber(error=errors[failure])


@given(
    parsers.parse(
        'two equivalent final masters with matching transcription of "{text}"'
    )
)
def equivalent_masters_with_matching_transcription(context, text):
    first_master = _mastered_audio()
    second_master = _mastered_audio()
    checksum = sha256(MASTER_WAV).hexdigest()
    context.values["first_master"] = first_master
    context.values["second_master"] = second_master
    context.values["first_transcriber"] = FakeTranscriber(
        result=_transcript(text=text, checksum=checksum)
    )
    context.values["second_transcriber"] = FakeTranscriber(
        result=_transcript(text=text, checksum=checksum)
    )


@given(parsers.parse('its first critical token is "{token}"'))
def first_critical_token(context, token):
    context.values["critical_tokens"] = (token,)


@given(parsers.parse('its second critical token is "{token}"'))
def second_critical_token(context, token):
    context.values["critical_tokens"] += (token,)


@given(parsers.parse('its critical tokens are "{token}"'))
def one_critical_token(context, token):
    context.values["critical_tokens"] = (token,)


@when("final-master QA is evaluated")
def final_master_qa_is_evaluated(context):
    try:
        _evaluate(context)
    except (
        TranscriptionFailureError,
        TranscriptionTransientError,
        ValueError,
    ) as error:
        context.values["error"] = error


@when("both final-master QA evaluations are performed")
def both_final_master_qa_evaluations_are_performed(context):
    qa = _qa_module()
    first = asyncio.run(
        qa.FinalMasterQaService(
            transcriber=context.values["first_transcriber"]
        ).evaluate(
            mastered_audio=context.values["first_master"],
            profile=PROFILE,
            critical_tokens=context.values["critical_tokens"],
        )
    )
    second = asyncio.run(
        qa.FinalMasterQaService(
            transcriber=context.values["second_transcriber"]
        ).evaluate(
            mastered_audio=context.values["second_master"],
            profile=PROFILE,
            critical_tokens=context.values["critical_tokens"],
        )
    )
    context.values["first_result"] = first
    context.values["second_result"] = second


@then(parsers.parse('the final-master QA record {outcome} at stage "{stage}"'))
def final_master_qa_record_has_outcome(context, outcome, stage):
    result = context.values["result"]
    assert result.passed is (outcome == "passes")
    assert result.to_dict()["stage"] == stage


@then(parsers.parse("final-master critical-token accuracy is {accuracy:f}"))
def final_master_critical_token_accuracy(context, accuracy):
    assert context.values["result"].critical_token_accuracy == accuracy


@then("final-master checksum evidence is bound to the exact master bytes")
def checksum_evidence_is_bound_to_master(context):
    result = context.values["result"]
    checksum = sha256(context.values["master"].wav_bytes).hexdigest()
    assert result.master_checksum == checksum
    assert result.transcript.checksum == checksum


@then("the transcriber receives the exact master once")
def transcriber_receives_exact_master_once(context):
    assert context.values["transcriber"].calls == [context.values["master"].wav_bytes]


@then("final-master QA raises a terminal transcription failure")
def final_master_qa_raises_terminal_transcription_failure(context):
    assert isinstance(context.values["error"], TranscriptionFailureError)


@then("final-master QA rejects the master before transcription dispatch")
def final_master_qa_rejects_before_transcription_dispatch(context):
    assert isinstance(context.values["error"], ValueError)
    assert context.values["transcriber"].calls == []


@then(parsers.parse("final-master QA raises a {failure} failure"))
def final_master_qa_raises_mapped_failure(context, failure):
    expected = {
        "retryable": TranscriptionTransientError,
        "terminal": TranscriptionFailureError,
        "terminal transcription": TranscriptionFailureError,
    }
    assert isinstance(context.values["error"], expected[failure])


@then("their serialized final-master QA records are identical")
def serialized_final_master_qa_records_are_identical(context):
    first = json.dumps(context.values["first_result"].to_dict(), sort_keys=True)
    second = json.dumps(context.values["second_result"].to_dict(), sort_keys=True)
    assert first == second


@then("the serialized record contains only QA schema fields")
def serialized_record_contains_only_qa_schema_fields(context):
    assert set(context.values["first_result"].to_dict()) == QA_SCHEMA_FIELDS
