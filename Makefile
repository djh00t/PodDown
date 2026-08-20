.PHONY: clean install build test lint docs check check-full quality-gates publish demo

DEMO_OUTPUT ?= /tmp/poddown-reference-demo

clean:
	rm -rf build dist htmlcov docs/api .coverage .pytest_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +

install:
	uv sync --locked --all-groups

build:
	rm -rf dist
	uv build

test:
	# Keep ambient distributions from changing the repository's pytest plugin set.
	PYDANTIC_DISABLE_PLUGINS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run pytest -p pytest_bdd.plugin -p pytest_cov.plugin -m "not live_provider" --cov=poddown --cov-branch --cov-report=term-missing

check: test lint

check-full: test

quality-gates: lint build docs

lint:
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run mypy

docs:
	uv run pdoc poddown --output-directory docs/api

publish: build
	uv publish

demo:
	uv run poddown-demo --output "$(DEMO_OUTPUT)"
