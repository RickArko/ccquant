# Local setup. dbt_packages/ is gitignored — `dbt deps` is part of install.
# Dashboard publish: documentation/Dashboard_Deploy.md
.DEFAULT_GOAL := help

FLY            ?= $(or $(shell command -v fly 2>/dev/null),$(shell command -v flyctl 2>/dev/null),$(wildcard $(HOME)/.fly/bin/fly),fly)
FLY_APP        ?= ccquant-btc
FLY_REGION     ?= ord
FLY_DOMAIN     ?= btc.rickarko.com
DASHBOARD_OUT  ?= deploy/public/index.html
DASHBOARD_LOCAL ?= data/export/market_tracker.html
PORT           ?= 8080
LAUNCH_LABEL   ?= com.ccquant.dashboard-refresh
LAUNCH_AGENTS  ?= $(HOME)/Library/LaunchAgents
LAUNCH_PLIST   ?= $(LAUNCH_AGENTS)/$(LAUNCH_LABEL).plist
LAUNCH_UID     ?= $(shell id -u)

.PHONY: help install dbt-deps \
	dashboard dashboard.stage dashboard.check dashboard.serve \
	dashboard.deploy dashboard.refresh dashboard.schedule dashboard.unschedule \
	fly.app fly.certs fly.deploy fly.status fly.logs fly.smoke

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "; printf "\nccquant — make targets\n\n"} \
	     /^[a-zA-Z_.-]+:.*?## / {printf "  %-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nVariables: FLY_APP=%s FLY_DOMAIN=%s DASHBOARD_OUT=%s\n\n" \
		"$(FLY_APP)" "$(FLY_DOMAIN)" "$(DASHBOARD_OUT)"

install: ## uv sync --all-extras --all-groups + pre-commit + dbt deps + kernel
	uv sync --all-extras --all-groups
	uv run pre-commit install
	$(MAKE) dbt-deps
	uv run python -m ipykernel install --user --name=ccquant --display-name="Python (ccquant)"

dbt-deps: ## install Hub packages into dbt/dbt_packages (required before dbt build)
	uv run dbt deps --project-dir dbt --profiles-dir dbt

dashboard: ## Write local Market Tracker HTML ($(DASHBOARD_LOCAL))
	uv run ccquant dashboard --no-open --out "$(DASHBOARD_LOCAL)"

dashboard.stage: ## Generate staged HTML for the Fly image ($(DASHBOARD_OUT))
	mkdir -p deploy/public
	uv run ccquant dashboard --no-open --out "$(DASHBOARD_OUT)"

dashboard.check: ## Secret-scan + size/marker gate on staged HTML
	uv run python scripts/dashboard_check.py "$(DASHBOARD_OUT)"

dashboard.serve: dashboard.stage ## Build+run the nginx image locally on $(PORT)
	@command -v docker >/dev/null 2>&1 || { \
	  printf 'docker is required for make dashboard.serve; fallback: python3 -m http.server --directory deploy/public %s\n' "$(PORT)" >&2; \
	  exit 1; \
	}
	docker build -f deploy/Dockerfile -t ccquant-btc:local .
	docker run --rm -p $(PORT):8080 ccquant-btc:local

dashboard.deploy: fly.deploy ## Alias: stage + check + fly deploy

dashboard.refresh: ## Lean tail-sync (no wallets/tweets/depth/MEV) then publish to Fly
	bash scripts/dashboard_refresh.sh

dashboard.schedule: ## Install launchd job (02:15 and 18:15 local) for dashboard.refresh
	@mkdir -p "$(LAUNCH_AGENTS)"
	@sed -e 's|__CCQUANT_ROOT__|$(CURDIR)|g' -e 's|__HOME__|$(HOME)|g' \
		deploy/com.ccquant.dashboard-refresh.plist.in > "$(LAUNCH_PLIST)"
	@launchctl bootout "gui/$(LAUNCH_UID)/$(LAUNCH_LABEL)" >/dev/null 2>&1 || true
	launchctl bootstrap "gui/$(LAUNCH_UID)" "$(LAUNCH_PLIST)"
	@printf 'scheduled %s (02:15 and 18:15 local). Logs: data/logs/dashboard-refresh.log\n' "$(LAUNCH_LABEL)"

dashboard.unschedule: ## Remove the launchd dashboard.refresh job
	@launchctl bootout "gui/$(LAUNCH_UID)/$(LAUNCH_LABEL)" >/dev/null 2>&1 || true
	@rm -f "$(LAUNCH_PLIST)"
	@printf 'unscheduled %s\n' "$(LAUNCH_LABEL)"

fly.app: ## Create Fly app (idempotent if $(FLY_APP) already exists)
	@if $(FLY) status --app $(FLY_APP) >/dev/null 2>&1; then \
	  printf 'already exists: %s\n' "$(FLY_APP)"; \
	else \
	  $(FLY) apps create $(FLY_APP) -o personal -y; \
	fi

fly.certs: ## Add HTTPS cert for $(FLY_DOMAIN) and print DNS records
	@$(FLY) certs add $(FLY_DOMAIN) -a $(FLY_APP) || true
	$(FLY) certs show $(FLY_DOMAIN) -a $(FLY_APP)

fly.deploy: ## Stage HTML, run dashboard.check, then fly deploy (check cannot be skipped)
	$(MAKE) dashboard.stage
	$(MAKE) dashboard.check
	$(FLY) deploy --app $(FLY_APP)

fly.status: ## Fly app status
	$(FLY) status --app $(FLY_APP)

fly.logs: ## Tail Fly logs
	$(FLY) logs --app $(FLY_APP)

fly.smoke: ## Curl https://$(FLY_DOMAIN)/healthz and the Market Tracker title
	curl --fail --silent --show-error "https://$(FLY_DOMAIN)/healthz" | grep -qx 'ok'
	curl --fail --silent --show-error "https://$(FLY_DOMAIN)/" | grep -q 'ccquant — Market Tracker'
	@printf 'smoke ok: https://%s/\n' "$(FLY_DOMAIN)"
