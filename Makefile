.PHONY: help up up-d down logs logs-app logs-db psql shell restart build rebuild test test-unit test-integration lint format typecheck migrate migrate-new migrate-down seed clean nuke db-create db-init db-reset db-shell cov cov-html cov-clear keygen webhooks refresh-banks backfill-banks

# ─── Defaults ───────────────────────────────────────────────────────
COMPOSE         := docker compose
COMPOSE_TEST    := docker compose -f docker-compose.test.yml
APP_SERVICE     := app
DB_SERVICE      := db

# Use the project's virtualenv when it exists, otherwise whatever
# `python` is on PATH.
PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; \
	elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe; \
	else echo python; fi)

# Local PostgreSQL connection settings. The password is never stored
# here — set PGPASSWORD in your shell (or use ~/.pgpass).
PGHOST     ?= localhost
PGPORT     ?= 5432
PGUSER     ?= postgres
PGDATABASE ?= autopay
PGTESTDB   ?= autopay_test

# ─── Help ───────────────────────────────────────────────────────────
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ─── Local dev ──────────────────────────────────────────────────────
up:  ## Start app + db in foreground
	$(COMPOSE) up

up-d:  ## Start app + db in background
	$(COMPOSE) up -d

down:  ## Stop app + db
	$(COMPOSE) down

logs:  ## Tail logs from all services
	$(COMPOSE) logs -f

logs-app:  ## Tail logs from app only
	$(COMPOSE) logs -f $(APP_SERVICE)

logs-db:  ## Tail logs from db only
	$(COMPOSE) logs -f $(DB_SERVICE)

psql:  ## Open psql shell inside db container
	$(COMPOSE) exec $(DB_SERVICE) psql -U $${POSTGRES_USER:-postgres} -d $${POSTGRES_DB:-autopay}

shell:  ## Open bash inside app container
	$(COMPOSE) exec $(APP_SERVICE) /bin/bash

restart:  ## Restart app only
	$(COMPOSE) restart $(APP_SERVICE)

# ─── Build ──────────────────────────────────────────────────────────
build:  ## Build images
	$(COMPOSE) build

rebuild:  ## Rebuild without cache
	$(COMPOSE) build --no-cache

# ─── Tests ──────────────────────────────────────────────────────────
# The suite always targets a throwaway *test* database (see
# tests/conftest.py). Override it with TEST_DATABASE_URL.
test:  ## Run full test suite
	$(COMPOSE_TEST) run --rm app-test

test-unit:  ## Run unit tests only
	$(COMPOSE_TEST) run --rm app-test pytest tests/unit -v

test-integration:  ## Run integration tests only
	$(COMPOSE_TEST) run --rm app-test pytest tests/integration -v

cov:  ## Run tests with coverage report
	$(COMPOSE_TEST) run --rm app-test pytest --cov=app --cov-report=term-missing --cov-report=html

cov-html: cov  ## Open the HTML coverage report
	@echo "Coverage report: htmlcov/index.html"
	@if command -v xdg-open >/dev/null 2>&1; then xdg-open htmlcov/index.html; \
	elif command -v open >/dev/null 2>&1; then open htmlcov/index.html; \
	else start htmlcov/index.html 2>/dev/null || echo "(open htmlcov/index.html manually)"; fi

cov-clear:  ## Remove coverage artifacts
	rm -rf .coverage htmlcov .pytest_cache

# ─── Quality ────────────────────────────────────────────────────────
lint:  ## Run ruff
	ruff check .

format:  ## Auto-format code
	ruff format .

typecheck:  ## Run mypy
	mypy app

# ─── Migrations ─────────────────────────────────────────────────────
migrate:  ## Apply all pending migrations
	$(COMPOSE) run --rm $(APP_SERVICE) alembic upgrade head

migrate-new:  ## Create new migration (usage: make migrate-new msg="add foo")
	$(COMPOSE) run --rm $(APP_SERVICE) alembic revision --autogenerate -m "$(msg)"

migrate-down:  ## Roll back one migration
	$(COMPOSE) run --rm $(APP_SERVICE) alembic downgrade -1

# ─── Seed / data ────────────────────────────────────────────────────
seed:  ## Load dev seed data
	$(COMPOSE) run --rm $(APP_SERVICE) python -m scripts.seed

refresh-banks:  ## Pull GET /bank into the DB and rewrite the committed snapshot
	$(COMPOSE) run --rm $(APP_SERVICE) python -m scripts.refresh_banks

backfill-banks:  ## Report bills whose bank_code holds a name instead of a code
	$(COMPOSE) run --rm $(APP_SERVICE) python -m scripts.backfill_bank_codes

# ─── Local DB helpers (run uvicorn outside Docker) ─────────────────
# These talk to the PostgreSQL instance on PGHOST. Put the password in
# the PGPASSWORD environment variable (or ~/.pgpass) — it is never
# stored in this file.
db-create:  ## Create the local database
	psql -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d postgres -c 'CREATE DATABASE $(PGDATABASE);'

db-init:  ## Load schema.sql into the local database and mark it migrated
	psql -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d $(PGDATABASE) -f schema.sql
	alembic upgrade head

db-reset:  ## Drop + recreate the local database (DESTROYS DATA)
	psql -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d postgres -c 'DROP DATABASE IF EXISTS $(PGDATABASE);' -c 'CREATE DATABASE $(PGDATABASE);'
	$(MAKE) db-init

db-shell:  ## Open psql against the local database
	psql -h $(PGHOST) -p $(PGPORT) -U $(PGUSER) -d $(PGDATABASE)

# ─── Cleanup ────────────────────────────────────────────────────────
clean:  ## Remove containers + images
	$(COMPOSE) down --rmi local

nuke:  ## Remove containers, images, AND volumes (DESTROYS DB DATA)
	$(COMPOSE) down --rmi local -v

# ─── Helpers ────────────────────────────────────────────────────────
keygen:  ## Generate Fernet + JWT secrets and print them (so you can paste into .env)
	@echo "BVN_ENCRYPTION_KEY (paste into .env):"
	@$(PYTHON) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
	@echo ""
	@echo "JWT_SECRET_KEY (paste into .env):"
	@$(PYTHON) -c "import secrets; print(secrets.token_urlsafe(64))"
	@echo ""
	@echo "TELEGRAM_SECRET_TOKEN (paste into .env):"
	@$(PYTHON) -c "import secrets; print(secrets.token_hex(16))"

ngrok:  ## Expose the local app on a public ngrok URL (for webhook testing)
	@if command -v ngrok >/dev/null 2>&1; then \
		echo "[ngrok] starting tunnel to http://localhost:8000"; \
		ngrok http 8000; \
	else \
		echo "ngrok not found. Install it from https://ngrok.com/download"; \
	fi

webhooks:  ## Print the webhook URLs you need to configure
	@echo "Paystack webhook URL (paste into https://dashboard.paystack.com/#/settings/developer):"
	@echo "  http://localhost:8000/webhooks/paystack  (dev)"
	@echo "  https://<your-app-host>/webhooks/paystack  (prod)"
	@echo ""
	@echo "Telegram webhook URL (set as WEBHOOK_URL in .env):"
	@echo "  http://localhost:8000/telegram/webhook  (dev)"
	@echo "  https://<your-app-host>/telegram/webhook  (prod)"
