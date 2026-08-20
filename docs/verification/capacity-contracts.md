# O10–O11 capacity and fault-boundary verification

The local admission contract rejects work before dispatch when bounded limits
for in-flight work, queue age, or estimated cost are exceeded. Reasons are
stable and redacted; source, transcript, audio, and provider payloads are not
returned in rejection details.

The current combined O06–O11 contract run passed 32 tests, including 7
capacity/fault-boundary tests covering exact-limit admission, concurrency
exhaustion, queue-age overflow, cost ceilings, malformed limits, and invalid
observations.

This is deterministic admission evidence, not a production load test or a
measured deployment threshold. Live provider outage/budget drills and
deployment-scale load testing remain required.
