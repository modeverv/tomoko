HOST ?= 127.0.0.1
PORT ?= 8000
EDGE_KITCHEN_PORT ?= 8001
UVICORN_LOG_LEVEL ?= info
TOMOKO_LOG_LEVEL ?= INFO
TOMOKO_LOG_FILE ?= logs/server.log
TOMOKO_DEBUG_LOG_FILE ?= logs/server-debug.log
V2_RUNTIME_LOG_FILE ?= logs/v2-runtime.jsonl
V2_REPORT_LATEST ?= reports/v2-latest.html
V2_CONFIG ?= config/v2.toml
COMPOSE ?= docker compose --project-directory . -f docker/docker-compose.yml
DB_DUMP_DIR ?= logs/db-dumps
DB_DUMP_FILE ?= $(DB_DUMP_DIR)/tomoko-v2-$(shell date +%Y%m%d-%H%M%S).sql
PYTHON ?= uv run python
PYTEST ?= uv run pytest
RUFF ?= uv run ruff
TMUX_SESSION ?= tomoko-v2-runtime
TMUX_MOUSE ?= on
TMUX_RUNTIME_READY_TIMEOUT_SEC ?= 600
TMUX_RUNTIME_READY_INTERVAL_SEC ?= 2
TOMOKO_V2_LLM_READY_URLS ?= http://127.0.0.1:8081/v1/models http://127.0.0.1:8082/v1/models
TOMOKO_V2_VOICEVOX_READY_URL ?= http://127.0.0.1:50122/version
TOMOKO_V2_LLM_URL ?= http://127.0.0.1:8082
TOMOKO_V2_LLM_MODEL ?= gemma-4-26b-a4b-it-mlx
TOMOKO_V2_VOICEVOX_URL ?= http://127.0.0.1:50122
TOMOKO_V2_VOICEVOX_SPEED ?= 1.5
VOICEVOX_COMMAND ?= /Users/seijiro/Sync/sync_work/by-llms/async-voicevox/run_streaming_voicevox.command
DFLASH_31B_MODEL ?= mlx-community/gemma-4-31b-it-4bit
DFLASH_31B_DRAFT ?= z-lab/gemma-4-31B-it-DFlash
DFLASH_31B_PORT ?= 8081
DFLASH_26B_MODEL ?= v1/loras/lora/fused_model
DFLASH_26B_DRAFT ?= z-lab/gemma-4-26B-A4B-it-DFlash
DFLASH_26B_PORT ?= 8082
WS_LATENCY_URL ?= ws://$(HOST):$(PORT)/ws
WS_LATENCY_TEXT ?= トモコ、短く返事して。
WS_LATENCY_VOICE ?= Kyoko

.PHONY: deps prepare download-models download-optional-models
.PHONY: server server-reload server-debug gateway gateway-reload edge-kitchen edge-kitchen-reload
.PHONY: v2-hot-path v2-tomoko v2-think v2-info v2-info-once v2-user-status v2-summary
.PHONY: session-summarizer session-summarizer-once turn-embedder turn-embedder-once persona-seed-initial persona-updater persona-updater-once
.PHONY: thinker thinker-once thinker2 thinker2-once thinker2-capture thinker2-capture-once journalist journalist-once
.PHONY: information-collect-world information-ingest information-ingest-once information-ingest-dry-run information-interpret-once information-interpret gcal
.PHONY: background-once background-watch background-dry-run
.PHONY: tmux-runtime tmux-run tmux-attach tmux-stop tmux-list run stop a
.PHONY: v2-runtime v2-stop v2-runtime-ready llm-run llm-stop voicevox-run v2-ocr-smoke ocr-smoke
.PHONY: v2-initiative-sim v2-floor-bench v2-report-latest v2-scheduler-report v2-llm-tts-smoke v2-conversation-smoke v2-scheduler-conversation-smoke v2-say-latency-smoke v2-scheduler-say-latency-smoke
.PHONY: db-up db-stop db-down db-dump test-unit test-integration lint check smoke-ws-voice-latency log-report monitor system-monitor

deps:
	uv sync

prepare: v2-runtime-ready

download-models:
	@echo "v2 runtime model defaults:"
	@echo "  main LLM: $(DFLASH_26B_MODEL) + $(DFLASH_26B_DRAFT)"
	@echo "  summary LLM: $(DFLASH_31B_MODEL) + $(DFLASH_31B_DRAFT)"
	@echo "  OCR: screencapture + tesseract"

download-optional-models: download-models

server gateway v2-hot-path:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) $(PYTHON) -m uvicorn server.hot_path.app:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL)

server-reload gateway-reload:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) $(PYTHON) -m uvicorn server.hot_path.app:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

server-debug v2-hot-path-debug:
	mkdir -p logs
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=DEBUG TOMOKO_LOG_FILE= $(PYTHON) -m uvicorn server.hot_path.app:app --host $(HOST) --port $(PORT) --log-level info --reload 2>&1 | tee -a $(TOMOKO_DEBUG_LOG_FILE)

edge-kitchen:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=logs/edge-kitchen.log $(PYTHON) -m uvicorn server.hot_path.app:app --host $(HOST) --port $(EDGE_KITCHEN_PORT) --log-level $(UVICORN_LOG_LEVEL)

edge-kitchen-reload:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=logs/edge-kitchen.log $(PYTHON) -m uvicorn server.hot_path.app:app --host $(HOST) --port $(EDGE_KITCHEN_PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

v2-tomoko:
	$(PYTHON) -m server.runtime process tomoko

v2-think thinker thinker2:
	$(PYTHON) -m server.runtime process think

v2-info information-interpret:
	$(PYTHON) -m server.runtime process info

v2-user-status thinker2-capture:
	$(PYTHON) -m server.runtime process user-status

v2-summary session-summarizer turn-embedder persona-updater journalist:
	$(PYTHON) -m server.runtime process summary

v2-info-once information-collect-world information-ingest-once information-ingest information-interpret-once gcal:
	$(PYTHON) -m server.runtime info-once

session-summarizer-once turn-embedder-once persona-updater-once thinker-once thinker2-once thinker2-capture-once journalist-once persona-seed-initial:
	$(PYTHON) -m server.runtime info-once >/dev/null
	@echo "$@ completed as v2 no-op/smoke hook"

information-ingest-dry-run:
	$(PYTHON) -m server.runtime info-once

background-once: persona-seed-initial session-summarizer-once turn-embedder-once persona-updater-once information-collect-world information-ingest-once information-interpret-once gcal thinker-once thinker2-once journalist-once

background-watch:
	@echo "Run long-lived v2 processes in separate terminals:"
	@echo "  make v2-tomoko"
	@echo "  make v2-info"
	@echo "  make v2-user-status"
	@echo "  make v2-summary"
	@echo "  make v2-think"

background-dry-run:
	$(MAKE) -n server-debug v2-tomoko v2-info v2-user-status v2-summary v2-think v2-info-once v2-ocr-smoke

llm-run:
	DFLASH_31B_MODEL="$(DFLASH_31B_MODEL)" DFLASH_31B_DRAFT="$(DFLASH_31B_DRAFT)" DFLASH_31B_PORT="$(DFLASH_31B_PORT)" DFLASH_26B_MODEL="$(DFLASH_26B_MODEL)" DFLASH_26B_DRAFT="$(DFLASH_26B_DRAFT)" DFLASH_26B_PORT="$(DFLASH_26B_PORT)" bash scripts/run_llm.sh

llm-stop:
	bash scripts/run_llm_stop.sh

voicevox-run:
	VOICEVOX_COMMAND="$(VOICEVOX_COMMAND)" bash scripts/run_voicevox.sh

v2-runtime-ready:
	TOMOKO_RUNTIME_WAIT_TIMEOUT_SEC=$(TMUX_RUNTIME_READY_TIMEOUT_SEC) TOMOKO_RUNTIME_WAIT_INTERVAL_SEC=$(TMUX_RUNTIME_READY_INTERVAL_SEC) TOMOKO_V2_LLM_READY_URLS="$(TOMOKO_V2_LLM_READY_URLS)" TOMOKO_V2_VOICEVOX_READY_URL="$(TOMOKO_V2_VOICEVOX_READY_URL)" bash scripts/wait_runtime_dependencies.sh

v2-runtime tmux-runtime:
	@command -v tmux >/dev/null || { echo "tmux is required"; echo "install with: brew install tmux"; exit 1; }
	@mkdir -p logs
	@if tmux has-session -t $(TMUX_SESSION) 2>/dev/null; then \
		echo "tmux session already exists: $(TMUX_SESSION)"; \
		echo "attach with: make tmux-attach"; \
		exit 1; \
	fi
	tmux new-session -d -s $(TMUX_SESSION) -n llm-run 'cd "$(CURDIR)" && DFLASH_TMUX_SESSION="$(TMUX_SESSION)" DFLASH_TMUX_EMBED=1 DFLASH_TMUX_MOUSE="$(TMUX_MOUSE)" make llm-run; exec zsh -l'
	tmux set-option -t $(TMUX_SESSION) mouse $(TMUX_MOUSE)
	tmux new-window -t $(TMUX_SESSION): -n voicevox 'cd "$(CURDIR)" && make voicevox-run; exit_code=$$?; echo; echo "voicevox-run exited with status $$exit_code; keeping window open."; while :; do sleep 3600; done'
	tmux new-window -t $(TMUX_SESSION): -n hot-path 'cd "$(CURDIR)" && make v2-runtime-ready && exec make server-debug'
	tmux new-window -t $(TMUX_SESSION): -n tomoko 'cd "$(CURDIR)" && exec make v2-tomoko'
	tmux new-window -t $(TMUX_SESSION): -n info 'cd "$(CURDIR)" && exec make v2-info'
	tmux new-window -t $(TMUX_SESSION): -n user-status 'cd "$(CURDIR)" && exec make v2-user-status'
	tmux new-window -t $(TMUX_SESSION): -n summary 'cd "$(CURDIR)" && exec make v2-summary'
	tmux new-window -t $(TMUX_SESSION): -n think 'cd "$(CURDIR)" && exec make v2-think'
	@echo "started tmux session: $(TMUX_SESSION)"

tmux-run: tmux-runtime
run: tmux-runtime
stop: tmux-stop
a: tmux-attach

tmux-attach:
	@command -v tmux >/dev/null || { echo "tmux is required"; echo "install with: brew install tmux"; exit 1; }
	@tmux has-session -t $(TMUX_SESSION) 2>/dev/null || { echo "tmux session not found: $(TMUX_SESSION)"; echo "start with: make tmux-runtime"; exit 1; }
	@tmux set-option -t $(TMUX_SESSION) mouse $(TMUX_MOUSE)
	tmux attach -t $(TMUX_SESSION)

v2-stop tmux-stop:
	@make llm-stop
	@if command -v tmux >/dev/null && tmux has-session -t $(TMUX_SESSION) 2>/dev/null; then \
		tmux send-keys -t $(TMUX_SESSION):llm-run C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):llm-31b C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):llm-26b C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):hot-path C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):voicevox C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):tomoko C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):info C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):user-status C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):summary C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):think C-c 2>/dev/null || true; \
		sleep 1; \
		tmux kill-session -t $(TMUX_SESSION); \
		echo "stopped tmux session: $(TMUX_SESSION)"; \
	else \
		echo "tmux session not found: $(TMUX_SESSION)"; \
	fi

tmux-list:
	tmux list-sessions

v2-ocr-smoke ocr-smoke:
	$(PYTHON) -m scripts.v2_ocr_smoke

v2-llm-tts-smoke:
	TOMOKO_V2_LLM_URL="$(TOMOKO_V2_LLM_URL)" TOMOKO_V2_LLM_MODEL="$(TOMOKO_V2_LLM_MODEL)" TOMOKO_V2_VOICEVOX_URL="$(TOMOKO_V2_VOICEVOX_URL)" TOMOKO_V2_VOICEVOX_SPEED="$(TOMOKO_V2_VOICEVOX_SPEED)" $(PYTHON) -m scripts.v2_runtime_smoke

v2-conversation-smoke:
	TOMOKO_V2_FAKE_RUNTIME=1 $(PYTHON) -m scripts.v2_ws_conversation_smoke --fake-runtime --start-processes

v2-scheduler-conversation-smoke:
	$(PYTHON) -m scripts.v2_scheduler_conversation_smoke

v2-say-latency-smoke:
	$(PYTHON) -m scripts.v2_say_latency_smoke --url "$(WS_LATENCY_URL)" --text "$(WS_LATENCY_TEXT)" --voice "$(WS_LATENCY_VOICE)"

v2-scheduler-say-latency-smoke:
	$(PYTHON) -m scripts.v2_scheduler_say_latency_smoke --url "$(WS_LATENCY_URL)" --text "$(WS_LATENCY_TEXT)" --voice "$(WS_LATENCY_VOICE)"

v2-initiative-sim:
	$(PYTHON) -m scripts.v2_initiative_sim

v2-floor-bench:
	$(PYTHON) -m scripts.v2_floor_bench

v2-report-latest:
	$(PYTHON) -m scripts.v2_report_latest

v2-scheduler-report:
	$(PYTHON) -m scripts.v2_scheduler_report

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
	$(PYTEST) -m unit

test-integration:
	$(PYTEST) -m integration

lint:
	$(RUFF) check server scripts background-process tests

check: lint test-unit

smoke-ws-voice-latency:
	@echo "v2 websocket smoke should use $(WS_LATENCY_URL) with text: $(WS_LATENCY_TEXT)"

log-report:
	$(PYTHON) -m server.runtime report-latest

monitor:
	$(PYTHON) -m server.runtime report-latest

system-monitor:
	$(PYTHON) -m server.runtime readiness
