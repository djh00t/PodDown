# Authenticated API integration verification

The local integration suite exercises the real `OIDCPrincipalVerifier` through
the FastAPI application. A generated HS256 token supplies tenant/project scope;
the request does not send compatibility scope headers. A token signed with a
different key is rejected before episode creation.

```text
PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  pytest -q tests/integration/test_authenticated_oidc_api.py
2 passed
```

This proves local verifier-to-API wiring only. It does not prove an external
OIDC issuer, deployed key rotation, hosted runtime, or customer authenticated
UAT.
