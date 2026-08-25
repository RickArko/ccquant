# Local setup. dbt_packages/ is gitignored — `dbt deps` is part of install.
.DEFAULT_GOAL := help

.PHONY: help install dbt-deps

help:
	@echo "make install    uv sync --all-extras --all-groups + pre-commit + dbt deps + kernel"
	@echo "make dbt-deps   install Hub packages into dbt/dbt_packages (required before dbt build)"

install:
	uv sync --all-extras --all-groups
	uv run pre-commit install
	$(MAKE) dbt-deps
	uv run python -m ipykernel install --user --name=ccquant --display-name="Python (ccquant)"

dbt-deps:
	uv run dbt deps --project-dir dbt --profiles-dir dbt
