"""Separate-process evidence for filesystem-backed render claims."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from poddown.audio.contracts import RenderedAudio, RenderRequest
from poddown.audio.local import DeterministicLocalRenderer
from poddown.audio.render import DurableRenderService
from poddown.audio.rights import VoiceConsent
from poddown.audio.storage import FilesystemArtifactStore, FilesystemRenderRecordStore

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


class ProcessRecordingRenderer:
    """Record provider entry before yielding to expose a cross-process race."""

    capabilities = DeterministicLocalRenderer.capabilities

    def __init__(self, marker_path: Path) -> None:
        self._delegate = DeterministicLocalRenderer()
        self._marker_path = marker_path

    async def render(self, request: RenderRequest) -> RenderedAudio:
        with self._marker_path.open("a", encoding="utf-8") as marker:
            marker.write(f"{request.idempotency_key}\n")
        await asyncio.sleep(0.1)
        return await self._delegate.render(request)


def _request() -> RenderRequest:
    return RenderRequest(
        episode_id="multiprocess-render-episode",
        episode_version="v1",
        segment_id="segment-1",
        speaker_id="host",
        expected_spoken_text="Separate workers claim one durable render key.",
        voice_asset_id="voice-host-v1",
        provider="local",
        model="local-deterministic-v1",
    )


def _process_entry(storage_root: str, marker_path: str, result_queue: Any) -> None:
    """Render one key in a fresh process and report only serializable evidence."""
    try:
        outcome = asyncio.run(_render_once(Path(storage_root), Path(marker_path)))
    except BaseException as error:
        result_queue.put(("error", type(error).__name__, str(error)))
    else:
        result_queue.put(("ok", outcome.replayed))


async def _render_once(storage_root: Path, marker_path: Path):
    artifacts = FilesystemArtifactStore(storage_root / "artifacts")
    records = FilesystemRenderRecordStore(storage_root / "records", artifacts)
    return (
        await DurableRenderService(artifacts, records).render_takes(
            _request(),
            VoiceConsent(
                "voice-host-v1", "consent-multiprocess-1", frozenset({"local"})
            ),
            ProcessRecordingRenderer(marker_path),
        )
    )[0]


@pytest.mark.skipif(os.name != "posix", reason="filesystem claims use POSIX flock")
def test_filesystem_claim_serializes_separate_worker_processes(tmp_path):
    """Separate workers dispatch one key once and one worker replays it."""
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    marker_path = tmp_path / "provider-dispatches.log"
    processes = [
        context.Process(
            target=_process_entry,
            args=(str(tmp_path), str(marker_path), result_queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert not process.is_alive()
        assert process.exitcode == 0

    messages = [result_queue.get(timeout=5) for _ in processes]
    assert all(message[0] == "ok" for message in messages), messages
    assert sorted(message[1] for message in messages) == [False, True]
    assert marker_path.read_text(encoding="utf-8").splitlines() == [
        _request().idempotency_key
    ]
