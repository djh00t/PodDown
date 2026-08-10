---
poddown:
  profile: technical-dialogue
  episode_id: episode-robotics-mapping-v1
  duration_minutes: 12
  target_date: 2026-07-31
  source_blocks: 4
  source_turns: 4
  source_locale: en-AU
---
# Robotics mapping source corpus

## Source block: block-robotics-overview

- turn_id: t-robotics-001
- source_block_anchor: block-robotics-overview
- claim_anchor: claim-robotics-001
- speaker_id: spk-archivist-1
- claim: |
    On 2026-07-31 at 09:00 UTC, the mobile platform maps a 12-minute field pass.
    The mapping rig references **LiDAR** and **C1** and keeps obstacle fusion at 13.8 hertz.

## Source block: block-robotics-localization

- turn_id: t-robotics-002
- source_block_anchor: block-robotics-localization
- claim_anchor: claim-lidar-frequency
- speaker_id: spk-controls-2
- claim: |
    During the test window, EKF localization reports 99.7% positional consistency.
    The same pass also samples 1.2 km and 0.4 meters vibration response.

## Source block: block-robotics-controls

- turn_id: t-robotics-003
- source_block_anchor: block-robotics-controls
- claim_anchor: claim-robotics-002
- speaker_id: spk-controls-2
- claim: |
    The controller is not silent and it is not stable in high-frequency tests.
    The control stack intentionally avoids unsupported shortcuts and never mutates source numbers.

## Source block: block-robotics-disagreement

- turn_id: t-robotics-004
- source_block_anchor: block-robotics-disagreement
- claim_anchor: claim-robotics-004
- speaker_id: spk-archivist-1
- claim: |
    I disagree with the earlier simplification that this architecture can split the render path
    into independent streams without temporal synchronization.
