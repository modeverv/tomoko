.PHONY: test-unit lint check db-up db-stop v2-hot-path v2-tomoko v2-think v2-info v2-info-once v2-user-status v2-summary v2-runtime v2-stop v2-initiative-sim v2-floor-bench v2-report-latest

PYTHON := uv run python
PYTEST := uv run pytest
RUFF := uv run ruff
COMPOSE := docker compose -f docker/docker-compose.yml
V2_TMUX_SESSION := tomoko-v2-runtime

test-unit:
	$(PYTEST) -m unit

lint:
	$(RUFF) check server scripts background-process tests

check: test-unit lint

db-up:
	$(COMPOSE) up -d postgres

db-stop:
	$(COMPOSE) stop postgres

v2-hot-path:
	$(PYTHON) -m server.runtime process hot-path

v2-tomoko:
	$(PYTHON) -m server.runtime process tomoko

v2-think:
	$(PYTHON) -m server.runtime process think

v2-info:
	$(PYTHON) -m server.runtime process info

v2-info-once:
	$(PYTHON) -m server.runtime info-once

v2-user-status:
	$(PYTHON) -m server.runtime process user-status

v2-summary:
	$(PYTHON) -m server.runtime process summary

v2-runtime:
	tmux new-session -d -s $(V2_TMUX_SESSION) -n hot-path '$(PYTHON) -m server.runtime process hot-path'
	tmux new-window -t $(V2_TMUX_SESSION) -n tomoko '$(PYTHON) -m server.runtime process tomoko'
	tmux new-window -t $(V2_TMUX_SESSION) -n info '$(PYTHON) -m server.runtime process info'
	tmux new-window -t $(V2_TMUX_SESSION) -n user-status '$(PYTHON) -m server.runtime process user-status'
	tmux new-window -t $(V2_TMUX_SESSION) -n summary '$(PYTHON) -m server.runtime process summary'
	tmux new-window -t $(V2_TMUX_SESSION) -n think '$(PYTHON) -m server.runtime process think'

v2-stop:
	-tmux send-keys -t $(V2_TMUX_SESSION):hot-path C-c
	-tmux send-keys -t $(V2_TMUX_SESSION):tomoko C-c
	-tmux send-keys -t $(V2_TMUX_SESSION):info C-c
	-tmux send-keys -t $(V2_TMUX_SESSION):user-status C-c
	-tmux send-keys -t $(V2_TMUX_SESSION):summary C-c
	-tmux send-keys -t $(V2_TMUX_SESSION):think C-c
	sleep 1
	-tmux kill-session -t $(V2_TMUX_SESSION)

v2-initiative-sim:
	$(PYTHON) -m scripts.v2_initiative_sim

v2-floor-bench:
	$(PYTHON) -m scripts.v2_floor_bench

v2-report-latest:
	$(PYTHON) -m scripts.v2_report_latest
