# D17: NATS JetStream outbox adapter

The durable PostgreSQL outbox relay uses an explicitly injected
`NatsJetStreamClient`; `nats_runtime.NatsPyJetStreamClient` is the concrete
nats-py boundary. URLs and credentials are never persisted or embedded in
records. Ordinary unit and BDD coverage use in-memory transports.

The relay creates or validates stream `PODDOWN_OUTBOX_V1` and publishes on
`poddown.outbox.v1.<tenant-id>.<event-type>`. Each immutable outbox event carries
a caller-owned `event_id`, which is sent as the `Nats-Msg-Id` header so JetStream
can deduplicate repeated publisher attempts.

Publishing validates stream setup before each bounded relay attempt and uses
the stable `Nats-Msg-Id` header for JetStream deduplication. The lower-level
`NatsJetStreamOutbox` transport port also supports replay through a
caller-named durable consumer: it pulls only `poddown.outbox.v1.<tenant-id>.>`;
the decoded delivery tenant is checked before the handler runs or
acknowledgement is sent, and acknowledgement follows handler success.

On 2026-08-16, the opt-in integration smoke passed 2 tests against the pinned
NATS 2.10.20 image in a disposable tmpfs-backed container. It verified stream
creation, tenant-scoped subject construction, duplicate suppression, and the
stable `Nats-Msg-Id` header. This is local transport evidence; PostgreSQL/NATS
outage recovery and cross-process replay evidence remain unverified.
