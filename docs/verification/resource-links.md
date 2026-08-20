# Resource-link verification

The API exposes episode-scoped `audio`, `transcript`, and `manifest` resource
links only through an injected provider. The response is validated for the
authorized tenant/project/episode, resource name, UUIDv7 identities, non-empty
URL, future UTC expiry, and lowercase SHA-256 checksum.

Missing providers return `503`, missing resources return `404`, and malformed,
expired, or mismatched provider output returns a redacted `503`. Local
compatibility headers and verified OIDC scope use the same tenant/project and
episode lookup before the provider is invoked. Signed resource retrieval
rejects tampered URLs without returning bytes.

Focused local evidence:

```text
tests/bdd/test_api_resources.py
tests/bdd/test_resource_links.py
tests/bdd/test_http_agent_gateway.py
tests/integration/test_api_resources.py
tests/unit/test_resource_links.py
tests/unit/test_agent_mcp_resource_links.py
tests/unit/test_http_agent_gateway.py
33 passed in 25.47s
```

The signer requires HTTPS outside explicit local mode, a bounded TTL, and a
process-configured secret. This is local API/gateway evidence only; MinIO-backed
retrieval, deployed OIDC authentication, and production UAT remain open.
