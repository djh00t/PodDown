---
poddown:
  profile: reference-demo-dialogue-v1
  episode_id: reference-demo-episode-v1
  duration_minutes: 12
  source_locale: en-AU
---
# PodDown reference demo: field calibration

## Source block: block-calibration

- turn_id: ref-turn-001
- source_block_anchor: block-calibration
- claim_anchor: claim-calibration-001
- speaker_id: ref-host
- claim: |
    On 2026-07-31, the LiDAR team measured a C1 calibration result of 99.7% across the 1.2 km test corridor.

## Source block: block-disagreement

- turn_id: ref-turn-002
- source_block_anchor: block-disagreement
- claim_anchor: claim-disagreement-001
- speaker_id: ref-analyst
- claim: |
    I disagree: the C1 result is not a guarantee that the corridor is not affected by rain or reflective surfaces.

## Source block: block-decision

- turn_id: ref-turn-003
- source_block_anchor: block-decision
- claim_anchor: claim-decision-001
- speaker_id: ref-host
- claim: |
    We should publish the source-bound result, state the uncertainty, and not claim a live-provider validation.
