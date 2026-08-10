# PodDown Studio

The reference demo uses the deterministic local mastering adapter backed by
`ffmpeg` and `ffprobe`. Install the media tooling before running it:

```bash
# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt-get update && sudo apt-get install --no-install-recommends -y ffmpeg
```

## Deterministic-local reference demo

Run the complete offline reference episode without provider credentials, provider
spend, external Temporal, payment, or publication adapters:

```bash
uv run poddown-demo --output /tmp/poddown-reference-demo
uv run poddown-demo --output /tmp/poddown-reference-demo --resume
```

Or use Make, optionally overriding the output directory:

```bash
make demo DEMO_OUTPUT=/tmp/poddown-reference-demo
```

The result JSON identifies `deterministic-local-demo`; it is not live-provider
evidence. The output directory contains `result.json`, `status.json`,
`usage.json`, `publication.json`, immutable package evidence, and the nine
required package files. The MCP preview evidence is side-effect-free:
`{"result":{"side_effect":"none"}}`.

For the existing MCP stdio interface, run `uv run poddown-mcp` and invoke the
`poddown_preview` tool before any render or publication decision.
