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
TURN_EMBEDDER_LOG_FILE ?= logs/turn-embedder.log
PERSONA_UPDATE_LOG_FILE ?= logs/persona-updater.log
THINKER_LOG_FILE ?= logs/thinker.log
JOURNALIST_LOG_FILE ?= logs/journalist.log
TURN_TAKING_LOG_FILE ?= logs/turn-taking-worker.log
TURN_TAKING_V2_LOG_FILE ?= logs/turn-taking-v2-worker.log
TURN_TAKING_HOST ?= 127.0.0.1
TURN_TAKING_PORT ?= 8765
TURN_TAKING_MODEL ?= mlx-community/gemma-4-e2b-it-4bit
MAAI_MATERIAL_WAV ?= _tools/materials/maai.wav
MAAI_MATERIAL_START_SEC ?= 0
MAAI_MATERIAL_DURATION_SEC ?= 30
MAAI_MATERIAL_SWAP_CHANNELS ?=
MONITOR_HOST ?= 127.0.0.1
MONITOR_PORT ?= 8770
BACKEND_TRACE_LOG_FILE ?= logs/backend-trace.jsonl
SYSTEM_METRICS_LOG_FILE ?= logs/system-metrics.jsonl
SYSTEM_METRICS_PROVIDER ?= mactop
SYSTEM_METRICS_COMMAND ?= mactop
SYSTEM_METRICS_INTERVAL_SEC ?= 2
WS_LATENCY_URL ?= ws://$(HOST):$(PORT)/ws
WS_LATENCY_TEXT ?= トモコ、短く返事して。
WS_LATENCY_SCENARIO ?= three-turn
WS_LATENCY_SILENCE_MS ?= 1200
WS_LATENCY_INTER_TURN_PAUSE_MS ?= 1500
WORLD_OBSERVATION_LOG_FILE ?= logs/world-observations.log
WORLD_OBSERVATION_WORK ?= informations/work
WORLD_OBSERVATION_ARCHIVED ?= informations/archived
WORLD_OBSERVATION_FAILED ?= informations/failed
WORLD_OBSERVATION_DATE ?= $(shell date +%Y-%m-%d)
WORLD_OBSERVATION_MCP_TIMEOUT_SEC ?= 600
WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC ?= 600
WORLD_OBSERVATION_INTERPRET_LIMIT ?= 10
WORLD_OBSERVATION_INTERPRET_INTERVAL_SEC ?= 300
GCAL_URLS_FILE ?= config/gcal_urls.txt
GCAL_DAYS_BEFORE ?= 1
GCAL_DAYS_AHEAD ?= 30
COMPOSE ?= docker compose --project-directory . -f docker/docker-compose.yml
DB_DUMP_DIR ?= logs/db-dumps
DB_DUMP_FILE ?= $(DB_DUMP_DIR)/tomoko-$(shell date +%Y%m%d-%H%M%S).sql
SESSION_SUMMARY_LIMIT ?= 10
SESSION_SUMMARY_INTERVAL_SEC ?= 30
TURN_EMBEDDER_LIMIT ?= 50
TURN_EMBEDDER_INTERVAL_SEC ?= 60
PERSONA_UPDATE_LIMIT ?= 1
PERSONA_UPDATE_INTERVAL_SEC ?= 60
THINKER_CANDIDATE_INTERVAL_SEC ?= 60
THINKER_ARRIVAL_INTERVAL_SEC ?= 180
JOURNALIST_INTERVAL_SEC ?= 3600
JOURNALIST_DATE ?=
SCREEN_SESSION ?= tomoko-runtime
SCREEN_SHELL ?= zsh
TMUX_SESSION ?= tomoko-runtime
TMUX_SHELL ?= zsh
TMUX_MOUSE ?= on
TMUX_RUNTIME_READY_TIMEOUT_SEC ?= 600
TMUX_RUNTIME_READY_INTERVAL_SEC ?= 2
TMUX_LLM_READY_URLS ?= http://127.0.0.1:8081/v1/models http://127.0.0.1:8082/v1/models
TMUX_VOICEVOX_READY_URL ?= http://127.0.0.1:50122/version

.PHONY: deps prepare download-models download-optional-models server server-reload server-debug gateway gateway-reload edge-kitchen edge-kitchen-reload
.PHONY: session-summarizer session-summarizer-once turn-embedder turn-embedder-once
.PHONY: persona-seed-initial persona-updater persona-updater-once thinker thinker-once journalist journalist-once turn-taking-worker turn-taking-worker-once turn-taking-v2-worker
.PHONY: information-collect-world information-ingest information-ingest-once information-ingest-dry-run information-interpret-once information-interpret gcal
.PHONY: background-once background-watch background-dry-run screen-runtime screen-runtime-full screen-attach screen-stop screen-list tmux-runtime tmux-run tmux-attach tmux-stop tmux-list
.PHONY: db-up db-stop db-down db-dump test-unit bench-stt soak-stt soak-voice-stack smoke-maai-tap smoke-maai-real smoke-maai-dialogue smoke-maai-material smoke-research-mcp smoke-research-session smoke-ws-voice-latency log-report monitor system-monitor lint check analyze-v2 analyze-v2-latest analyze-v2-list analyze-v2-html
.PHONY: daily llm-run llm-stop voicevox-run shadow-bench shadow-bench-report shadow-bench-full

deps:
	mise exec -- uv sync

prepare:
	mise exec -- uv run python _tools/prepare_runtime.py --config $(CENTRAL_CONFIG)

download-models:
	mise exec -- uv run python _tools/download_models.py

download-optional-models:
	mise exec -- uv run python _tools/download_models.py --include-optional

server:
	TOMOKO_MAAI_BACKCHANNEL_ENABLED=1 PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL)

server-reload:
	TOMOKO_MAAI_BACKCHANNEL_ENABLED=1 PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TOMOKO_LOG_FILE) mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level $(UVICORN_LOG_LEVEL) --reload

server-debug:
	mkdir -p logs
	TOMOKO_MAAI_BACKCHANNEL_ENABLED=1 PYTHONUNBUFFERED=1 TOMOKO_CONFIG=$(CENTRAL_CONFIG) TOMOKO_LOG_LEVEL=DEBUG TOMOKO_LOG_FILE= mise exec -- uv run uvicorn server.edge.main:app --host $(HOST) --port $(PORT) --log-level info --reload 2>&1 | tee -a $(TOMOKO_DEBUG_LOG_FILE)

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
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(SESSION_SUMMARY_LOG_FILE) mise exec -- uv run python background-process/summarize_pending_sessions.py --config $(CENTRAL_CONFIG) --limit 500

turn-embedder:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TURN_EMBEDDER_LOG_FILE) mise exec -- uv run python background-process/embed_conversation_turns.py --config $(CENTRAL_CONFIG) --limit $(TURN_EMBEDDER_LIMIT) --watch --interval-sec $(TURN_EMBEDDER_INTERVAL_SEC)

turn-embedder-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TURN_EMBEDDER_LOG_FILE) mise exec -- uv run python background-process/embed_conversation_turns.py --config $(CENTRAL_CONFIG) --limit $(TURN_EMBEDDER_LIMIT)

persona-updater:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(PERSONA_UPDATE_LOG_FILE) mise exec -- uv run python background-process/update_persona_snapshots.py --config $(CENTRAL_CONFIG) --limit $(PERSONA_UPDATE_LIMIT) --watch --interval-sec $(PERSONA_UPDATE_INTERVAL_SEC)

persona-updater-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(PERSONA_UPDATE_LOG_FILE) mise exec -- uv run python background-process/update_persona_snapshots.py --config $(CENTRAL_CONFIG) --limit $(PERSONA_UPDATE_LIMIT)

persona-seed-initial:
	mise exec -- uv run python _tools/seed_initial_persona_snapshot.py --config $(CENTRAL_CONFIG) --replace

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

turn-taking-worker:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TURN_TAKING_LOG_FILE) TOMOKO_TURN_TAKING_MODEL=$(TURN_TAKING_MODEL) mise exec -- uv run python background-process/run_turn_taking_worker.py \
		--host $(TURN_TAKING_HOST) \
		--port $(TURN_TAKING_PORT) \
		--model $(TURN_TAKING_MODEL)

turn-taking-worker-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TURN_TAKING_LOG_FILE) TOMOKO_TURN_TAKING_MODEL=$(TURN_TAKING_MODEL) mise exec -- uv run python background-process/run_turn_taking_worker.py \
		--once \
		--disable-llm \
		--sample-text "うん"

turn-taking-v2-worker:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(TURN_TAKING_V2_LOG_FILE) mise exec -- uv run python background-process/run_turn_taking_v2_worker.py \
		--config $(CENTRAL_CONFIG)

information-collect-world:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(WORLD_OBSERVATION_LOG_FILE) TOMOKO_WORLD_OBSERVATION_MCP_TIMEOUT_SEC=$(WORLD_OBSERVATION_MCP_TIMEOUT_SEC) TOMOKO_WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC=$(WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC) mise exec -- uv run python _tools/collect_world_observation.py \
		--date $(WORLD_OBSERVATION_DATE) \
		--output-dir $(WORLD_OBSERVATION_WORK)

information-ingest-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(WORLD_OBSERVATION_LOG_FILE) mise exec -- uv run python background-process/ingest_world_observations.py \
		--config $(CENTRAL_CONFIG) \
		--once \
		--path $(WORLD_OBSERVATION_WORK) \
		--archive-root $(WORLD_OBSERVATION_ARCHIVED) \
		--failed-root $(WORLD_OBSERVATION_FAILED)

information-ingest: information-ingest-once

information-ingest-dry-run:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(WORLD_OBSERVATION_LOG_FILE) mise exec -- uv run python background-process/ingest_world_observations.py \
		--config $(CENTRAL_CONFIG) \
		--dry-run \
		--path $(WORLD_OBSERVATION_WORK) \
		--archive-root $(WORLD_OBSERVATION_ARCHIVED) \
		--failed-root $(WORLD_OBSERVATION_FAILED)

information-interpret-once:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(WORLD_OBSERVATION_LOG_FILE) mise exec -- uv run python background-process/interpret_world_observations.py \
		--config $(CENTRAL_CONFIG) \
		--once \
		--limit $(WORLD_OBSERVATION_INTERPRET_LIMIT)

information-interpret:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) TOMOKO_LOG_FILE=$(WORLD_OBSERVATION_LOG_FILE) mise exec -- uv run python background-process/interpret_world_observations.py \
		--config $(CENTRAL_CONFIG) \
		--watch \
		--limit $(WORLD_OBSERVATION_INTERPRET_LIMIT) \
		--interval-sec $(WORLD_OBSERVATION_INTERPRET_INTERVAL_SEC)

gcal:
	PYTHONUNBUFFERED=1 TOMOKO_LOG_LEVEL=$(TOMOKO_LOG_LEVEL) mise exec -- uv run python background-process/import_gcal.py \
		--config $(CENTRAL_CONFIG) \
		--urls-file $(GCAL_URLS_FILE) \
		--days-before $(GCAL_DAYS_BEFORE) \
		--days-ahead $(GCAL_DAYS_AHEAD)

background-once: persona-seed-initial session-summarizer-once turn-embedder-once persona-updater-once information-ingest-once information-interpret-once gcal thinker-once journalist-once

background-watch:
	@echo "Run long-lived processes in separate terminals:"
	@echo "  make session-summarizer"
	@echo "  make turn-embedder"
	@echo "  make persona-seed-initial"
	@echo "  make persona-updater"
	@echo "  make thinker"
	@echo "  make journalist"
	@echo "  make turn-taking-worker"
	@echo "  make information-interpret"
	@echo "  make gcal"

background-dry-run:
	$(MAKE) -n gateway edge-kitchen session-summarizer session-summarizer-once turn-embedder turn-embedder-once persona-seed-initial persona-updater persona-updater-once information-collect-world information-ingest-dry-run information-ingest-once information-interpret-once information-interpret gcal thinker thinker-once journalist journalist-once turn-taking-worker turn-taking-worker-once

screen-runtime:
	@command -v screen >/dev/null || { echo "screen is required"; exit 1; }
	@mkdir -p logs
	@if screen -list | grep -q "[.]$(SCREEN_SESSION)[[:space:]]"; then \
		echo "screen session already exists: $(SCREEN_SESSION)"; \
		echo "attach with: make screen-attach"; \
		exit 1; \
	fi
	screen -dmS $(SCREEN_SESSION) -t server $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make server-debug'
	screen -S $(SCREEN_SESSION) -X screen -t turn-taking $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make turn-taking-worker'
	screen -S $(SCREEN_SESSION) -X screen -t thinker $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make thinker'
	screen -S $(SCREEN_SESSION) -X screen -t summarizer $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make session-summarizer'
	screen -S $(SCREEN_SESSION) -X screen -t embedder $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make turn-embedder'
	screen -S $(SCREEN_SESSION) -X screen -t persona $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make persona-updater'
	@echo "started screen session: $(SCREEN_SESSION)"
	@echo "attach with: make screen-attach"

screen-runtime-full: screen-runtime
	screen -S $(SCREEN_SESSION) -X screen -t journalist $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make journalist'
	screen -S $(SCREEN_SESSION) -X screen -t information $(SCREEN_SHELL) -lc 'cd "$(CURDIR)" && exec make information-interpret'
	@echo "added full background workers to screen session: $(SCREEN_SESSION)"

screen-attach:
	screen -r $(SCREEN_SESSION)

screen-stop:
	screen -S $(SCREEN_SESSION) -X quit

screen-list:
	screen -ls

tmux-runtime:
	@command -v tmux >/dev/null || { echo "tmux is required"; echo "install with: brew install tmux"; exit 1; }
	@mkdir -p logs
	@if tmux has-session -t $(TMUX_SESSION) 2>/dev/null; then \
		echo "tmux session already exists: $(TMUX_SESSION)"; \
		echo "attach with: make tmux-attach"; \
		exit 1; \
	fi
	tmux new-session -d -s $(TMUX_SESSION) -n llm-run 'cd "$(CURDIR)" && DFLASH_TMUX_SESSION="$(TMUX_SESSION)" DFLASH_TMUX_EMBED=1 DFLASH_TMUX_MOUSE="$(TMUX_MOUSE)" make llm-run; exec $(TMUX_SHELL) -l'
	tmux set-option -t $(TMUX_SESSION) mouse $(TMUX_MOUSE)
	tmux new-window -t $(TMUX_SESSION): -n voicevox 'cd "$(CURDIR)" && make voicevox-run; exit_code=$$?; echo; echo "voicevox-run exited with status $$exit_code; keeping window open."; while :; do sleep 3600; done'
	tmux new-window -t $(TMUX_SESSION): -n server 'cd "$(CURDIR)" && TOMOKO_RUNTIME_WAIT_TIMEOUT_SEC=$(TMUX_RUNTIME_READY_TIMEOUT_SEC) TOMOKO_RUNTIME_WAIT_INTERVAL_SEC=$(TMUX_RUNTIME_READY_INTERVAL_SEC) TOMOKO_RUNTIME_LLM_READY_URLS="$(TMUX_LLM_READY_URLS)" TOMOKO_RUNTIME_VOICEVOX_READY_URL="$(TMUX_VOICEVOX_READY_URL)" _tools/wait_runtime_dependencies.sh && exec make server-debug'
	tmux new-window -t $(TMUX_SESSION): -n v2-shadow 'cd "$(CURDIR)" && exec make turn-taking-v2-worker'
	@echo "started tmux session: $(TMUX_SESSION)"
	make tmux-attach

run: tmux-runtime
stop: tmux-stop

tmux-attach:
	@command -v tmux >/dev/null || { echo "tmux is required"; echo "install with: brew install tmux"; exit 1; }
	@tmux has-session -t $(TMUX_SESSION) 2>/dev/null || { echo "tmux session not found: $(TMUX_SESSION)"; echo "start with: make tmux-runtime"; exit 1; }
	@tmux set-option -t $(TMUX_SESSION) mouse $(TMUX_MOUSE)
	tmux attach -t $(TMUX_SESSION)

tmux-stop:
	@make llm-stop
	@if command -v tmux >/dev/null && tmux has-session -t $(TMUX_SESSION) 2>/dev/null; then \
		tmux send-keys -t $(TMUX_SESSION):llm-run C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):llm-31b C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):llm-26b C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):server C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):voicevox C-c 2>/dev/null || true; \
		tmux send-keys -t $(TMUX_SESSION):v2-shadow C-c 2>/dev/null || true; \
		sleep 1; \
		tmux kill-session -t $(TMUX_SESSION); \
		echo "stopped tmux session: $(TMUX_SESSION)"; \
	else \
		echo "tmux session not found: $(TMUX_SESSION)"; \
	fi

tmux-list:
	tmux list-sessions

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

soak-voice-stack:
	mise exec -- uv run python _tools/soak_voice_stack_scenarios.py

smoke-maai-tap:
	mise exec -- uv run python _tools/smoke_maai_tap_session.py

smoke-maai-real:
	mise exec -- uv run python _tools/smoke_maai_tap_session.py --use-maai

smoke-maai-dialogue:
	mise exec -- uv run python _tools/smoke_maai_dialogue.py

smoke-maai-material:
	mise exec -- uv run python _tools/smoke_maai_material.py --input $(MAAI_MATERIAL_WAV) --start-sec $(MAAI_MATERIAL_START_SEC) --duration-sec $(MAAI_MATERIAL_DURATION_SEC) $(MAAI_MATERIAL_SWAP_CHANNELS)

smoke-research-mcp:
	mise exec -- uv run python _tools/smoke_research_mcp_flow.py

smoke-research-session:
	mise exec -- uv run python _tools/smoke_research_tomoro_session_flow.py

smoke-ws-voice-latency:
	mise exec -- uv run python _tools/smoke_ws_voice_latency.py --url $(WS_LATENCY_URL) --text "$(WS_LATENCY_TEXT)" --scenario $(WS_LATENCY_SCENARIO) --silence-ms $(WS_LATENCY_SILENCE_MS) --inter-turn-pause-ms $(WS_LATENCY_INTER_TURN_PAUSE_MS)

log-report:
	mise exec -- uv run python _tools/analyze_server_debug_log.py --input $(TOMOKO_DEBUG_LOG_FILE) --output logs/server-debug-report.html

monitor:
	mise exec -- uv run python _tools/monitor_dashboard.py --host $(MONITOR_HOST) --port $(MONITOR_PORT) --server-log $(TOMOKO_DEBUG_LOG_FILE) --backend-trace $(BACKEND_TRACE_LOG_FILE) --system-metrics $(SYSTEM_METRICS_LOG_FILE) --config $(CENTRAL_CONFIG)

system-monitor:
	mise exec -- uv run python _tools/system_metrics.py --provider $(SYSTEM_METRICS_PROVIDER) --command $(SYSTEM_METRICS_COMMAND) --output $(SYSTEM_METRICS_LOG_FILE) --interval-sec $(SYSTEM_METRICS_INTERVAL_SEC)

lint:
	mise exec -- uv run ruff check .

check: lint test-unit

daily: gcal journalist-once persona-updater-once turn-embedder-once session-summarizer-once information-collect-world information-ingest-once information-interpret-once

llm-run:
	bash _tools/run_llm.sh

llm-stop:
	bash _tools/run_llm_stop.sh

voicevox-run:
	bash _tools/run_voicevox.sh

analyze-v2:
	mise exec -- uv run python -m server.tools.analyze_turn_taking_v2 --session-id "$(SESSION_ID)" $(if $(MAIN_LOG),--main $(MAIN_LOG),) $(if $(V2_LOG),--v2 $(V2_LOG),) $(if $(OUT_REPORT),--out $(OUT_REPORT),)

## Turn-taking v2 分析: 最新セッションを自動取得して分析レポートを生成する
## 使い方: make analyze-v2-latest
analyze-v2-latest:
	mise exec -- uv run python -m server.tools.analyze_turn_taking_v2_latest $(if $(MAIN_LOG),--main $(MAIN_LOG),) $(if $(V2_LOG),--v2 $(V2_LOG),) $(if $(OUT_DIR),--out-dir $(OUT_DIR),)

## Turn-taking v2 ログに含まれる全セッションIDを一覧表示する
## 使い方: make analyze-v2-list
analyze-v2-list:
	mise exec -- uv run python -m server.tools.analyze_turn_taking_v2_latest --list-sessions $(if $(MAIN_LOG),--main $(MAIN_LOG),) $(if $(V2_LOG),--v2 $(V2_LOG),)

## Turn-taking v2: HTMLインタラクティブタイムライン生成
## 発話ごとに メイン推論開始 vs シャドウワーカーLLMシグナル のタイミングを可視化する
## 使い方:
##   make analyze-v2-html               # 最新セッション (MD + HTML)
##   make analyze-v2-html TOP=3         # 最新3セッション
##   make analyze-v2-html SESSION_ID=<id>  # 指定セッション
analyze-v2-html:
	$(if $(SESSION_ID),\
	  mise exec -- uv run python -m server.tools.analyze_turn_taking_v2 \
	    --session-id "$(SESSION_ID)" --html \
	    $(if $(MAIN_LOG),--main $(MAIN_LOG),) \
	    $(if $(V2_LOG),--v2 $(V2_LOG),) \
	    $(if $(OUT_REPORT),--out $(OUT_REPORT),),\
	  mise exec -- uv run python -m server.tools.analyze_turn_taking_v2_latest \
	    --html \
	    $(if $(TOP),--top $(TOP),) \
	    $(if $(MAIN_LOG),--main $(MAIN_LOG),) \
	    $(if $(V2_LOG),--v2 $(V2_LOG),) \
	    $(if $(OUT_DIR),--out-dir $(OUT_DIR),))

## ============================================================
## Shadow Bench — Turn-taking v2 shadow worker timing benchmark
## ============================================================
## seeds/utterances.txt からランダムに N 本を選び、say で音声化
## → partial 転写列シミュレーション → shadow evaluator 評価
## → タイミング差分析レポートを stdout + JSONL で出力
##
## 使い方:
##   make shadow-bench           # 10本でテスト
##   make shadow-bench N=20      # 20本
##   make shadow-bench N=5 SEED=42  # 乱数シード固定
##   make shadow-bench-report    # 最新ログを HTML レポートに変換
##   make shadow-bench-full N=30 # bench + report を一括実行
N ?= 10
SEED ?=
SHADOW_CUT_MODE ?= mix

shadow-bench:
	mise exec -- uv run python scripts/shadow_bench.py \
	  -n $(N) \
	  --cut-mode $(SHADOW_CUT_MODE) \
	  $(if $(SEED),--seed $(SEED),)

shadow-bench-report:
	mise exec -- uv run python scripts/shadow_bench_report.py --open

shadow-bench-full:
	mise exec -- uv run python scripts/shadow_bench.py \
	  -n $(N) \
	  --cut-mode $(SHADOW_CUT_MODE) \
	  $(if $(SEED),--seed $(SEED),) && \
	mise exec -- uv run python scripts/shadow_bench_report.py --open
