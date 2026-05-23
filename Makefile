HOST ?= 127.0.0.1
PORT ?= 8000
UVICORN_LOG_LEVEL ?= info

.PHONY: server server-reload db-up test-unit lint check

server:
	PYTHONUNBUFFERED=1 mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL)

server-reload:
	PYTHONUNBUFFERED=1 mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

db-up:
	docker compose up -d postgres

test-unit:
	mise exec -- uv run pytest -m unit

lint:
	mise exec -- uv run ruff check .

check: lint test-unit
