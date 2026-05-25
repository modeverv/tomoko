HOST ?= 127.0.0.1
PORT ?= 8000
EDGE_KITCHEN_PORT ?= 8001
CENTRAL_CONFIG ?= config/central_realtime.toml
EDGE_KITCHEN_CONFIG ?= config/edge_kitchen.toml
UVICORN_LOG_LEVEL ?= info
TOMOKO_LOG_LEVEL ?= INFO
TOMOKO_LOG_FILE ?= logs/server.log
TOMOKO_DEBUG_LOG_FILE ?= logs/server-debug.log
EDGE_KITCHEN_LOG_FILE ?= logs/edge-kitchen.log
SESSION_SUMMARY_LOG_FILE ?= logs/session-summarizer.log
PERSONA_UPDATE_LOG_FILE ?= logs/persona-updater.log
THINKER_LOG_FILE ?= logs/thinker.log
JOURNALIST_LOG_FILE ?= logs/journalist.log
COMPOSE ?= docker compose --project-directory . -f docker/docker-compose.yml
DB_DUMP_DIR ?= logs/db-dumps
DB_DUMP_FILE ?= $(DB_DUMP_DIR)/tomoko-$(shell date +%Y%m%d-%H%M%S).sql
SESSION_SUMMARY_LIMIT ?= 10
SESSION_SUMMARY_INTERVAL_SEC ?= 30
PERSONA_UPDATE_LIMIT ?= 10
PERSONA_UPDATE_INTERVAL_SEC ?= 60
THINKER_CANDIDATE_INTERVAL_SEC ?= 60
THINKER_ARRIVAL_INTERVAL_SEC ?= 180
JOURNALIST_INTERVAL_SEC ?= 3600
JOURNALIST_DATE ?=

.PHONY: deps download-models download-optional-models server server-reload server-debug gateway gateway-reload edge-kitchen edge-kitchen-reload
.PHONY: session-summarizer session-summarizer-once
.PHONY: persona-updater persona-updater-once thinker thinker-once journalist journalist-once
.PHONY: background-once background-watch background-dry-run
.PHONY: db-up db-stop db-down db-dump test-unit bench-stt soak-stt lint check

deps:
	mise exec -- uv sync

download-models:
	mise exec -- uv run python _tools/download_models.py

download-optional-models:
	mise exec -- uv run python _tools/download_models.py --include-optional

server:
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL)

server-reload:
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

server-debug:
	mkdir -p logs
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=DEBUG TOMOKO_LOG_FILE= mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level info --reload 2>&1 | tee -a $(TOMOKO_DEBUG_LOG_FILE)

gateway:
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL)

gateway-reload:
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

edge-kitchen:
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(EDGE_KITCHEN_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(EDGE_KITCHEN_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(EDGE_KITCHEN_PORT) --log-level $(UVICORN_LOG_LEVEL)

edge-kitchen-reload:
	PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(EDGE_KITCHEN_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(EDGE_KITCHEN_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(EDGE_KITCHEN_PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

session-summarizer:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(SESSION_SUMMARY_LOG_FILE) mise exec -- uv run python background-process/summarize_pending_sessions.py --config $(CENTRAL_CONFIG) --limit $(SESSION_SUMMARY_LIMIT) --watch --interval-sec $(SESSION_SUMMARY_INTERVAL_SEC)

session-summarizer-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(SESSION_SUMMARY_LOG_FILE) mise exec -- uv run python background-process/summarize_pending_sessions.py --config $(CENTRAL_CONFIG) --limit $(SESSION_SUMMARY_LIMIT)

persona-updater:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(PERSONA_UPDATE_LOG_FILE) mise exec -- uv run python background-process/update_persona_snapshots.py --config $(CENTRAL_CONFIG) --limit $(PERSONA_UPDATE_LIMIT) --watch --interval-sec $(PERSONA_UPDATE_INTERVAL_SEC)

persona-updater-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(PERSONA_UPDATE_LOG_FILE) mise exec -- uv run python background-process/update_persona_snapshots.py --config $(CENTRAL_CONFIG) --limit $(PERSONA_UPDATE_LIMIT)

thinker:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(THINKER_LOG_FILE) mise exec -- uv run python background-process/run_thinker.py \
		--config $(CENTRAL_CONFIG) \
		--watch \
		--candidate-interval-sec $(THINKER_CANDIDATE_INTERVAL_SEC) \
		--arrival-interval-sec $(THINKER_ARRIVAL_INTERVAL_SEC)

thinker-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(THINKER_LOG_FILE) mise exec -- uv run python background-process/run_thinker.py \
		--config $(CENTRAL_CONFIG) \
		--once \
		--candidate-interval-sec $(THINKER_CANDIDATE_INTERVAL_SEC) \
		--arrival-interval-sec $(THINKER_ARRIVAL_INTERVAL_SEC)

journalist:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(JOURNALIST_LOG_FILE) mise exec -- uv run python background-process/run_journalist.py \
		--config $(CENTRAL_CONFIG) \
		--watch \
		--interval-sec $(JOURNALIST_INTERVAL_SEC) \
		$(if $(JOURNALIST_DATE),--date $(JOURNALIST_DATE),)

journalist-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(JOURNALIST_LOG_FILE) mise exec -- uv run python background-process/run_journalist.py \
		--config $(CENTRAL_CONFIG) \
		--once \
		$(if $(JOURNALIST_DATE),--date $(JOURNALIST_DATE),)

background-once: session-summarizer-once persona-updater-once thinker-once journalist-once

background-watch:
	@echo "Run long-lived processes in separate terminals:"
	@echo "  make session-summarizer"
	@echo "  make persona-updater"
	@echo "  make thinker"
	@echo "  make journalist"

background-dry-run:
	$(MAKE) -n gateway edge-kitchen session-summarizer session-summarizer-once persona-updater persona-updater-once thinker thinker-once journalist journalist-once

db-up:
	$(COMPOSE) up -d postgres

db-stop:
	$(COMPOSE) stop postgres

db-down:
	$(COMPOSE) down

db-dump:
	mkdir -p $(DB_DUMP_DIR)
	docker exec tomoko-postgres pg_dump -U tomoko -d tomoko > $(DB_DUMP_FILE)
	@echo "wrote $(DB_DUMP_FILE)"

test-unit:
	mise exec -- uv run pytest -m unit

bench-stt:
	mise exec -- uv run pytest tests/perf/test_stt_latency.py -m perf -s --tb=short

soak-stt:
	mise exec -- uv run python _tools/soak_stt_backends.py

lint:
	mise exec -- uv run ruff check .

check: lint test-unit
