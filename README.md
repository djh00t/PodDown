# PodDown Studio

The default reference demo renders host-local speech as
`local-system-tts-demo`. It needs `ffmpeg` and `ffprobe`, plus macOS `say` or
Linux `espeak-ng` (falling back to `espeak`). It never needs API keys, provider
spend, or a network call. Install the media and speech tooling before running it:

```bash
# macOS (`say` is included with macOS)
brew install ffmpeg

# Debian/Ubuntu
sudo apt-get update && sudo apt-get install --no-install-recommends -y ffmpeg espeak-ng
```

## Host-local reference demo

Run the complete local episode with host speech and no provider credentials,
provider spend, external Temporal, payment, or publication adapters:

```bash
uv run poddown-demo --output /tmp/poddown-reference-demo
uv run poddown-demo --output /tmp/poddown-reference-demo --resume
```

Or use Make, optionally overriding the output directory:

```bash
make demo DEMO_OUTPUT=/tmp/poddown-reference-demo
```

`make demo` explicitly selects `local-speech`. The result JSON identifies
`local-system-tts-demo`; the synthesized bytes can vary by operating system,
installed voice, and speech-engine version. `--resume` safely reuses matching
persisted local evidence. Missing `ffmpeg`, `ffprobe`, or a supported speech
engine fails closed before package publication.

The output directory contains `result.json`, `status.json`, `usage.json`,
`publication.json`, immutable package evidence, and the nine required package
files. The MP3 is written under the published package tree at
`published/tenants/<tenant>/projects/<project>/reference-demo/<version>/episode.mp3`.
The MCP preview evidence is side-effect-free:
`{"result":{"side_effect":"none"}}`.

For an offline structural fixture instead of host speech, choose the deterministic
renderer explicitly:

```bash
uv run poddown-demo --audio-mode deterministic --output /tmp/poddown-reference-demo
```

This reports `deterministic-local-demo` and is useful for stable test structure;
it is not listenable local-speech or live-provider evidence. Live ElevenLabs
rendering is separate and explicit: it requires `ELEVENLABS_API_KEY`, an approved
provider voice mapping, rights/consent evidence, and spending authorization.

The wheel bundles the versioned reference fixtures, so the same command works
from an installed package as well as this checkout. This local demo publishes
the synthetic-presenter disclosure in `show-notes.md`; it does not claim spoken
or external-platform disclosure.

For the existing MCP stdio interface, run `uv run poddown-mcp` and invoke the
`poddown_preview` tool before any render or publication decision.
