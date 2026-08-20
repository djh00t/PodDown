# Authenticated API verification

The local integration suite exercises the real OIDC principal verifier through
FastAPI. A signed JWT supplies issuer, audience, subject, tenant, and project
scope; requests do not supply compatibility scope headers. Invalid signatures,
issuer/audience values, algorithms, and scope are rejected before protected
episode or resource operations.

The current authenticated boundary slice passed:

```text
tests/bdd/test_authenticated_api.py
tests/bdd/test_authentication.py
tests/bdd/test_api_auth_scope.py
tests/bdd/test_oidc_bearer_validation.py
tests/integration/test_authenticated_oidc_api.py
tests/integration/test_api_auth_scope.py
tests/unit/test_api_auth.py
tests/unit/test_api_scope.py
43 passed in 32.07s
```

Header-based compatibility remains available only in explicit local mode. The
production API mode requires configured OIDC issuer, audience, algorithm, and
verification key references; key values are not persisted or emitted in
records. This is local verifier-to-API evidence only. External issuer metadata,
key rotation, hosted runtime, and customer authenticated UAT remain required.
