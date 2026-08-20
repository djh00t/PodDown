# O20 clean-checkout verifier

`scripts/verify_clean_checkout.py` is the repeatable verifier for a clean
checkout and the offline reference paths. It supports separately labelled
deterministic-local and host-local speech verification; neither path claims
provider ASR or live-provider fidelity.

Without a demo run it checks the Git root, clean status, required fixtures, and
Python compilation:

```bash
python scripts/verify_clean_checkout.py --json
```

To run the deterministic demo and its resume check, place output outside the
checkout:

```bash
python scripts/verify_clean_checkout.py \
  --demo-output /tmp/poddown-clean-verifier \
  --json
```

The verifier requires the explicit `deterministic-local-demo` mode, zero cost,
100% critical-token accuracy, all nine package artifacts, failed-segment
regeneration, an exact manifest digest, and unchanged manifest identity after
resume. It rejects provider-ASR or provider claims in deterministic evidence.

For the listenable host-local path, use:

```bash
python scripts/verify_clean_checkout.py \
  --audio-mode local-speech \
  --demo-output /tmp/poddown-clean-host-local \
  --json
```

That mode requires exactly one published `episode.mp3`, validates it with local
FFprobe as mono 44.1 kHz MP3 with a positive duration, and checks the published
provenance for `host-local-tts-v1`, `local-system-tts-demo`, passing QA, and
zero-cost evidence. A resumed result may have no newly failed segment; when
failure evidence is present, it must identify the regenerated segments. It does
not validate provider-ASR or external publication.

The verifier itself does not establish live provider fidelity, hosted service
recovery, authenticated UAT, or external publication.

## Fresh local evidence

On 2026-08-15, the exact clean `main` checkout at `7ce7d6f` passed direct
read-only checks for clean status, required fixtures, and Python compilation.
The verifier's deterministic demo/resume run also passed from that checkout:
critical-token accuracy was `1.0`, the immutable package manifest was
`359928a752bf710ad63b02234ff7c9112fe06fac8147189b235ffc2f5603ad25`, and
resume replayed `306` takes. The verifier script and this evidence are present
only in the preserved reconciliation worktree at this point; they are not
merged `main` evidence. This is deterministic-local evidence only; it does not
upgrade host-local listening, live-provider, hosted, authenticated, or
publication status.

The host-local verifier acceptance/unit slice passed `14 passed`. A fresh
host-local run from the clean checkout rendered all persisted segments but
exposed the fixed 120-second FFmpeg timeout during mastering. Resuming that
same output with the existing reconciliation correction completed and passed
the host-local verifier at
`/tmp/poddown-clean-host-local-closure-20260815`: mono 44.1 kHz MP3,
650.031 seconds, MP3 SHA-256
`b21260de0fbe9e0d63ce8ad7b35d21dd0ab7983efdc3633459d907ee3a20dd15`, package
manifest digest
`9a4491559be76cb1144c1364c6d1c950a377d165bb73fa74e25c25cbbc190f0a`,
host-local renderer provenance, passing QA, zero cost, 100% script-derived
critical-token accuracy, 306 render requests, and 306 replayed takes. This is
clean-fixture plus reconciliation-overlay evidence; the duration-scaled
FFmpeg correction and verifier are not merged into `main`, so release/UAT and
the main-branch correction remain pending.
