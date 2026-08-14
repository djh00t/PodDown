# C05 Provider Evidence Report

Base SHA: `4102601`

## RED

```sh
PYTHONPATH=src ../../../.venv/bin/pytest -q tests/bdd/test_provider_evidence.py tests/unit/providers/test_provider_evidence.py tests/contract/providers/test_provider_evidence.py
```

Expected result before `ProviderEvidence` existed: 26 failures, all because the
public model was absent. The worktree has no local virtual environment; the
shared project environment was used with the worktree source path.

## GREEN

```sh
PYTHONPATH=src ../../../.venv/bin/pytest -q tests/bdd/test_provider_evidence.py tests/unit/providers/test_provider_evidence.py tests/contract/providers/test_provider_evidence.py
PYTHONPATH=src ../../../.venv/bin/ruff check src/poddown/providers/contracts.py tests/bdd/test_provider_evidence.py tests/unit/providers/test_provider_evidence.py tests/contract/providers/test_provider_evidence.py
PYTHONPATH=src ../../../.venv/bin/mypy --strict src/poddown/providers/contracts.py
git diff --check
```

Result: 27 focused tests passed; Ruff and strict mypy passed; whitespace check passed.

## Residual risk

No provider adapter persists this metadata yet; that durable integration belongs to
later provider work. No dependencies, live providers, or broad suite were run.

## Fix round 1

Base SHA: `0b7c29898ab34441b43bd12c52b47b91c7717492`

### RED output

```text
5 failed, 42 passed in 0.21s
```

The failures covered a non-singleton UTC timezone, the absent `ProviderUsage`
conversion helper, lowercase currency, and whitespace/overlong usage keys.

### GREEN

```sh
PYTHONPATH=src ../../../.venv/bin/pytest -q tests/bdd/test_provider_evidence.py tests/unit/providers/test_provider_evidence.py tests/contract/providers/test_provider_evidence.py
PYTHONPATH=src ../../../.venv/bin/ruff check src/poddown/providers/contracts.py tests/bdd/test_provider_evidence.py tests/unit/providers/test_provider_evidence.py tests/contract/providers/test_provider_evidence.py
PYTHONPATH=src ../../../.venv/bin/mypy --strict src/poddown/providers/contracts.py
git diff --check
```

Result: 47 focused tests passed; Ruff and strict mypy passed; whitespace check passed.
