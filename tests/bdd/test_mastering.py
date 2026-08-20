"""Executable acceptance tests for deterministic episode mastering."""

import wave
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO

from pytest_bdd import given, scenarios, then, when

from poddown.audio.mastering import (
    MasteringError,
    MasteringProfile,
    MasteringRequest,
    MasteringSegment,
    MasteringService,
    _ffmpeg_timeout_for_wav,
)

scenarios("../features/mastering.feature")

PROFILE = MasteringProfile(
    sample_rate_hz=44100,
    channels=1,
    silence_ms=100,
    crossfade_ms=0,
    min_duration_seconds=0.01,
    max_duration_seconds=10.0,
    version="spoken-word-v1",
)


@dataclass(frozen=True)
class FakeFfmpegResult:
    """Small structural result returned by the injected deterministic runner."""

    wav_bytes: bytes
    mp3_bytes: bytes
    executable: str = "fake-ffmpeg"
    version: str = "fake-7.0"
    command: tuple[str, ...] = ("fake-ffmpeg", "-filter_complex", "concat")
    filters: tuple[str, ...] = ("concat",)
    commands: tuple[tuple[str, ...], ...] = (
        ("fake-ffmpeg", "-filter_complex", "concat"),
    )
    mp3_metadata: dict[str, object] | None = field(
        default_factory=lambda: {
            "codec_name": "mp3",
            "sample_rate": "44100",
            "channels": "1",
            "duration": "0.02",
        }
    )


@dataclass
class FakeRunner:
    """Deterministic runner double whose calls retain the dispatched order."""

    result: FakeFfmpegResult
    error: Exception | None = None
    calls: list[tuple[MasteringSegment, ...]] = field(default_factory=list)
    expected_profile: MasteringProfile | None = None

    def run(
        self,
        *,
        segments: tuple[MasteringSegment, ...],
        profile: MasteringProfile,
    ) -> FakeFfmpegResult:
        """Return the configured ffmpeg-like result or raise the configured error."""
        if self.expected_profile is not None:
            assert profile == self.expected_profile
        self.calls.append(segments)
        if self.error is not None:
            raise self.error
        return self.result


def _wav_bytes(*, sample: int) -> bytes:
    """Build 20 ms of deterministic 44.1 kHz mono PCM using only stdlib tools."""
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(sample.to_bytes(2, "little", signed=True) * 882)
    return stream.getvalue()


def _wav_bytes_for_frames(
    *, sample: int, frames: int, sample_rate: int = 44100
) -> bytes:
    """Build a PCM fixture with the requested deterministic frame count."""
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(sample.to_bytes(2, "little", signed=True) * frames)
    return stream.getvalue()


SEGMENT_ONE_WAV = _wav_bytes(sample=1200)
SEGMENT_TWO_WAV = _wav_bytes(sample=-1200)
MASTERED_WAV = _wav_bytes(sample=600)
MASTERED_MP3 = b"ID3fake-deterministic-mastered-mp3"
RUNNER_RESULT = FakeFfmpegResult(MASTERED_WAV, MASTERED_MP3)


def _request(*segments: MasteringSegment) -> MasteringRequest:
    return MasteringRequest(
        episode_id="episode-001",
        episode_version="v1",
        segments=segments,
        profile=PROFILE,
    )


def _checksums(*audio_bytes: bytes) -> tuple[str, ...]:
    return tuple(sha256(value).hexdigest() for value in audio_bytes)


@given("two valid segments supplied out of position order")
def valid_segments_out_of_position_order(context):
    context.values["runner"] = FakeRunner(RUNNER_RESULT)
    context.values["request"] = _request(
        MasteringSegment(20, "segment-020", SEGMENT_TWO_WAV),
        MasteringSegment(10, "segment-010", SEGMENT_ONE_WAV),
    )


@given("a mastering request containing malformed audio")
def mastering_request_with_malformed_audio(context):
    context.values["runner"] = FakeRunner(RUNNER_RESULT)
    context.values["request"] = _request(
        MasteringSegment(10, "segment-010", b"not a WAV file"),
    )


@given("a valid mastering request and a failing runner")
def valid_request_and_failing_runner(context):
    context.values["runner"] = FakeRunner(
        RUNNER_RESULT,
        error=RuntimeError("ffmpeg exited with status 1"),
    )
    context.values["request"] = _request(
        MasteringSegment(10, "segment-010", SEGMENT_ONE_WAV),
    )


@given("a valid mastering request and a runner without MP3 metadata")
def valid_request_and_runner_without_mp3_metadata(context):
    context.values["runner"] = FakeRunner(
        FakeFfmpegResult(MASTERED_WAV, MASTERED_MP3, mp3_metadata=None)
    )
    context.values["request"] = _request(
        MasteringSegment(10, "segment-010", SEGMENT_ONE_WAV),
    )


@given("two equivalent mastering requests")
def equivalent_mastering_requests(context):
    segments = (
        MasteringSegment(10, "segment-010", SEGMENT_ONE_WAV),
        MasteringSegment(20, "segment-020", SEGMENT_TWO_WAV),
    )
    context.values["runner"] = FakeRunner(RUNNER_RESULT)
    context.values["first_request"] = _request(*segments)
    context.values["second_request"] = _request(*segments)


@given("a short segment and a longer master duration requirement")
def short_segment_and_longer_master_duration_requirement(context):
    short_segment = _wav_bytes(sample=1200)
    completed_master = _wav_bytes_for_frames(sample=600, frames=4_410)
    profile = MasteringProfile(
        min_duration_seconds=0.1,
        max_duration_seconds=1.0,
        min_segment_duration_seconds=0.01,
        max_segment_duration_seconds=0.05,
    )
    context.values["runner"] = FakeRunner(
        FakeFfmpegResult(
            completed_master,
            MASTERED_MP3,
            mp3_metadata={
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": "1",
                "duration": "0.1",
            },
        )
    )
    context.values["request"] = MasteringRequest(
        "episode-001",
        "v1",
        (MasteringSegment(10, "segment-010", short_segment),),
        profile,
    )


@given("short and long assembled WAV durations")
def short_and_long_assembled_wav_durations(context):
    context.values["short_wav"] = _wav_bytes_for_frames(sample=1, frames=1)
    context.values["long_wav"] = _wav_bytes_for_frames(
        sample=1,
        frames=650,
        sample_rate=1,
    )


@when("the FFmpeg timeouts are calculated")
def ffmpeg_timeouts_are_calculated(context):
    context.values["short_timeout"] = _ffmpeg_timeout_for_wav(
        context.values["short_wav"]
    )
    context.values["long_timeout"] = _ffmpeg_timeout_for_wav(context.values["long_wav"])


@when("the episode is mastered")
def episode_is_mastered(context):
    service = MasteringService(runner=context.values["runner"])
    try:
        context.values["result"] = service.master(context.values["request"])
    except MasteringError as error:
        context.values["error"] = error


@when("both episodes are mastered")
def both_episodes_are_mastered(context):
    service = MasteringService(runner=context.values["runner"])
    context.values["first_result"] = service.master(context.values["first_request"])
    context.values["second_result"] = service.master(context.values["second_request"])


@then("deterministic WAV and MP3 outputs are returned")
def deterministic_wav_and_mp3_outputs_are_returned(context):
    result = context.values["result"]
    assert result.wav_bytes == MASTERED_WAV
    assert result.mp3_bytes == MASTERED_MP3


@then("the runner receives segments in stable position order")
def runner_receives_stable_position_order(context):
    (dispatched_segments,) = context.values["runner"].calls
    assert tuple(segment.segment_id for segment in dispatched_segments) == (
        "segment-010",
        "segment-020",
    )


@then("provenance records the profile, runner, ordered inputs, and output checksums")
def provenance_records_deterministic_mastering_details(context):
    provenance = context.values["result"].provenance
    assert provenance.profile_version == "spoken-word-v1"
    assert provenance.ffmpeg_executable == "fake-ffmpeg"
    assert provenance.ffmpeg_version == "fake-7.0"
    assert provenance.command == ("fake-ffmpeg", "-filter_complex", "concat")
    assert provenance.filters == ("concat",)
    assert provenance.input_checksums == _checksums(SEGMENT_ONE_WAV, SEGMENT_TWO_WAV)
    assert provenance.wav_checksum == sha256(MASTERED_WAV).hexdigest()
    assert provenance.mp3_checksum == sha256(MASTERED_MP3).hexdigest()


@then("mastering fails before the runner is called")
def mastering_fails_before_runner_is_called(context):
    assert isinstance(context.values["error"], MasteringError)
    assert context.values["runner"].calls == []


@then("the runner failure is raised as a mastering error")
def runner_failure_is_raised_as_mastering_error(context):
    assert isinstance(context.values["error"], MasteringError)
    assert len(context.values["runner"].calls) == 1


@then("mastering fails closed for missing MP3 inspection")
def mastering_fails_closed_for_missing_mp3_inspection(context):
    assert isinstance(context.values["error"], MasteringError)
    assert len(context.values["runner"].calls) == 1


@then("their WAV and MP3 output checksums are equal")
def equivalent_inputs_have_equal_output_checksums(context):
    first = context.values["first_result"]
    second = context.values["second_result"]
    assert first.provenance.wav_checksum == second.provenance.wav_checksum
    assert first.provenance.mp3_checksum == second.provenance.mp3_checksum
    assert first.provenance.wav_checksum == sha256(first.wav_bytes).hexdigest()
    assert first.provenance.mp3_checksum == sha256(first.mp3_bytes).hexdigest()


@then("the short segment is dispatched and the longer master is returned")
def short_segment_is_dispatched_and_longer_master_is_returned(context):
    (dispatched_segments,) = context.values["runner"].calls
    assert dispatched_segments[0].segment_id == "segment-010"
    assert context.values["result"].diagnostics.duration_seconds == 0.1


@then("the long episode timeout is greater than the short episode timeout")
def long_episode_timeout_is_greater(context):
    assert context.values["long_timeout"] > context.values["short_timeout"]
