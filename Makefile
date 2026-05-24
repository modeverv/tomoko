HOST ?= 127.0.0.1
PORT ?= 8000
UVICORN_LOG_LEVEL ?= info
TOMOKO_LOG_LEVEL ?= INFO
TOMOKO_LOG_FILE ?= logs/server.log
TOMOKO_DEBUG_LOG_FILE ?= logs/server-debug.log
COMPOSE ?= docker compose --project-directory . -f docker/docker-compose.yml
DB_DUMP_DIR ?= logs/db-dumps
DB_DUMP_FILE ?= $(DB_DUMP_DIR)/tomoko-$(shell date +%Y%m%d-%H%M%S).sql

.PHONY: deps server server-reload server-debug db-up db-stop db-down db-dump test-unit bench-stt lint check

deps:
	mise exec -- uv sync

server:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL)

server-reload:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

server-debug:
	mkdir -p logs
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=DEBUG TOMOKO_LOG_FILE= mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level info --reload 2>&1 | tee -a $(TOMOKO_DEBUG_LOG_FILE)

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

lint:
	mise exec -- uv run ruff check .

check: lint test-unit
