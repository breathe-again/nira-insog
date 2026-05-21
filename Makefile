# Shortcuts for the local dev stack.
# Usage:
#   make up        — start everything (rebuild if needed)
#   make down      — stop everything
#   make logs      — tail logs from all services
#   make logs-api  — tail just the API logs
#   make ps        — show running containers
#   make rebuild   — rebuild images from scratch and start
#   make clean     — stop everything AND remove the postgres volume (destructive)
#   make sh-api    — shell into the api container
#   make sh-db     — psql into the postgres container

COMPOSE := docker compose

.PHONY: up down logs logs-api logs-frontend ps rebuild clean sh-api sh-db health refresh-frontend test test-unit test-models smoke backup-db restore-db snapshot list-backups migrate-uploads where-is-data

up:
	$(COMPOSE) up --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

logs-api:
	$(COMPOSE) logs -f api

logs-frontend:
	$(COMPOSE) logs -f frontend

ps:
	$(COMPOSE) ps

rebuild:
	$(COMPOSE) build --no-cache
	$(COMPOSE) up

clean:
	$(COMPOSE) down -v

# Refresh just the frontend node_modules (useful after editing package.json).
# Stops the frontend container, removes the named node_modules volume, rebuilds,
# and brings everything back up. Postgres data is preserved.
refresh-frontend:
	$(COMPOSE) stop frontend || true
	$(COMPOSE) rm -f frontend || true
	docker volume rm $$(docker volume ls -q | grep node-modules) 2>/dev/null || true
	$(COMPOSE) build --no-cache frontend
	$(COMPOSE) up -d frontend
	@echo "Frontend refreshed. Tail logs with: make logs-frontend"

sh-api:
	$(COMPOSE) exec api bash

sh-db:
	$(COMPOSE) exec postgres psql -U nira -d nira_insig

health:
	@echo "Liveness (no deps):"
	@curl -s http://localhost:8000/health || echo "(API not reachable)"
	@echo
	@echo "Readiness (postgres + redis):"
	@curl -s http://localhost:8000/api/health || echo "(API not reachable)"
	@echo

# ----- Testing -----
# Run the full pytest suite inside the api container against the live stack.
# Assumes `make up` is already running.
test:
	$(COMPOSE) exec -T api pytest -v tests/

# Just the unit tests — fast, no stack required (uses the api container only).
test-unit:
	$(COMPOSE) exec -T api pytest -v tests/test_models.py

# Pure-Python understanding-layer tests (parsers, vendors, anomalies).
# Runs in <1s, no Postgres / Redis needed. Useful for fast iteration.
test-services:
	$(COMPOSE) exec -T api pytest -v tests/unit/

# Even faster smoke check using curl — useful when you don't want to wait for pytest to import.
smoke:
	@echo "1) Liveness …"
	@curl -fsS http://localhost:8000/health && echo
	@echo "2) Readiness …"
	@curl -fsS http://localhost:8000/api/health && echo
	@echo "3) Upload a CSV …"
	@printf 'date,amount\n2026-05-19,1000\n' > /tmp/nira-smoke.csv
	@curl -fsS -F "file=@/tmp/nira-smoke.csv" http://localhost:8000/api/documents | python3 -m json.tool
	@echo "OK"
	@rm -f /tmp/nira-smoke.csv

# ----- Persistence: backups, restore, snapshots -----

# Where is your data on this Mac?
where-is-data:
	@echo "Uploaded files (visible in Finder):"
	@echo "  ./.local-data/uploads/"
	@if [ -d .local-data/uploads ]; then \
	  echo "  Currently: $$(find .local-data/uploads -type f 2>/dev/null | wc -l | tr -d ' ') file(s) totaling $$(du -sh .local-data/uploads 2>/dev/null | cut -f1)"; \
	fi
	@echo ""
	@echo "Postgres database (Docker-managed volume):"
	@docker volume inspect nira-insig_nira-pgdata --format '  Path on this Mac: {{.Mountpoint}}' 2>/dev/null || echo "  Not yet created — run 'make up' once first."
	@echo ""
	@echo "Backups:"
	@if [ -d .local-data/backups ] && [ -n "$$(ls -A .local-data/backups 2>/dev/null)" ]; then \
	  ls -lh .local-data/backups/ | tail -n +2; \
	else \
	  echo "  No backups yet. Run 'make snapshot' to create one."; \
	fi

# Gzipped pg_dump of the current database → .local-data/backups/postgres-TIMESTAMP.sql.gz
backup-db:
	@mkdir -p .local-data/backups
	@TS=$$(date +%Y%m%d-%H%M%S); \
	OUT=".local-data/backups/postgres-$$TS.sql.gz"; \
	echo "Dumping database → $$OUT"; \
	$(COMPOSE) exec -T postgres pg_dump -U nira nira_insig | gzip > "$$OUT"; \
	echo "Done. Size: $$(du -h $$OUT | cut -f1)"

# Restore the DB from a backup file:  make restore-db FILE=.local-data/backups/postgres-XYZ.sql.gz
restore-db:
	@if [ -z "$(FILE)" ]; then echo "Usage: make restore-db FILE=path/to/backup.sql.gz"; exit 1; fi
	@if [ ! -f "$(FILE)" ]; then echo "Not found: $(FILE)"; exit 1; fi
	@echo "Restoring from $(FILE) …"
	@echo "WARNING: this drops and recreates the public schema."
	@read -p "Continue? [y/N] " yn; [ "$$yn" = "y" ] || [ "$$yn" = "Y" ] || { echo "Aborted."; exit 1; }
	$(COMPOSE) exec -T postgres psql -U nira -d nira_insig -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
	gunzip -c "$(FILE)" | $(COMPOSE) exec -T postgres psql -U nira nira_insig
	@echo "Restore complete."

# Take a timestamped snapshot of BOTH the database AND the uploads folder.
snapshot:
	@mkdir -p .local-data/backups
	@TS=$$(date +%Y%m%d-%H%M%S); \
	DIR=".local-data/backups/snap-$$TS"; \
	mkdir -p "$$DIR"; \
	echo "Snapshotting → $$DIR/"; \
	$(COMPOSE) exec -T postgres pg_dump -U nira nira_insig | gzip > "$$DIR/postgres.sql.gz"; \
	if [ -d .local-data/uploads ] && [ -n "$$(ls -A .local-data/uploads 2>/dev/null)" ]; then \
	  tar -czf "$$DIR/uploads.tar.gz" -C .local-data uploads; \
	  echo "  uploads.tar.gz   $$(du -h $$DIR/uploads.tar.gz | cut -f1)"; \
	else \
	  echo "  (no uploads to snapshot)"; \
	fi; \
	echo "  postgres.sql.gz  $$(du -h $$DIR/postgres.sql.gz | cut -f1)"; \
	echo "Done."

list-backups:
	@echo "Backups in .local-data/backups/:"
	@if [ -d .local-data/backups ] && [ -n "$$(ls -A .local-data/backups 2>/dev/null)" ]; then \
	  ls -lh .local-data/backups/; \
	else \
	  echo "  (none yet — run 'make backup-db' or 'make snapshot')"; \
	fi

# One-shot migration: copy uploaded files from the OLD nira-uploads Docker volume
# into the new bind-mount folder. Run this once if you had data in the old setup.
migrate-uploads:
	@mkdir -p .local-data/uploads
	@echo "Copying files from old nira-uploads volume → ./.local-data/uploads/ …"
	@docker run --rm \
	  -v nira-insig_nira-uploads:/old \
	  -v "$$PWD/.local-data/uploads:/new" \
	  alpine sh -c 'cp -a /old/. /new/ 2>/dev/null && echo "Copied: $$(find /new -type f | wc -l) file(s)"' \
	  || echo "Old volume not found — nothing to migrate (you're on a fresh setup)."
