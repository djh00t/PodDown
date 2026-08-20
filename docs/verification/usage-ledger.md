# D12 usage and cost ledger

`UsageLedgerRepository` appends immutable `UsageEvent` evidence through a DB-API
connection. A `(tenant_id, provider, provider_request_id)` replay returns the
original event only when all immutable evidence matches; a mismatch returns
`UsageReplayConflict` without exposing driver details.

Migration `013` leaves the prior catalog unchanged. It derives `project_id`
from the existing tenant-scoped publication relationship and derives `job_id`
only for an unambiguous tenant/project/episode render job. Its `NOT VALID`
checks and foreign keys require scope for new rows while retaining legacy rows
whose job relationship cannot be proven.

Amounts remain `Decimal` values in memory and use plain fixed-point strings at
the database boundary. Estimated and reconciled amounts are stored separately;
the ledger does not calculate or infer provider billing.
