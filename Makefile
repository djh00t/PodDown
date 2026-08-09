.PHONY: clean install build test lint docs check check-full quality-gates publish

clean:
	rm -rf build dist htmlcov docs/api .coverage .pytest_cache
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +

install:
	uv sync --locked --all-groups

build:
	uv build

test:
	uv run pytest -m "not live_provider" --cov=poddown --cov-branch --cov-report=term-missing

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
