# LOG.md

## 2026-06-18 セッション2

### やること（開始時に書く）
- root `PLAN.md` を上から順番に実装する。
- まず V2.0 の root control plane と v2 用ディレクトリを作り、その上に DTO / DB schema / runtime helper / process scaffold / evaluation hook までを段階的に積む。
- 外部実機依存の Apple Speech / VOICEVOX / Calendar / OCR / live conversation smoke は、コードと smoke hook を先に用意し、実行できない検証は明示して残す。

### やったこと
- root `README.md` / `MEMORY.md` / `Makefile` / `config/v2.toml` と v2 用 `server/` / `client/` / `tests/` / `scripts/` / `background-process/` / `reports/` を作った。
- `server/shared/models.py` に v2 DTO を集約し、hot loop 例外は VAD 側 primitive のまま扱う実装にした。
- `server/shared/schemas.py` / `notify.py` / `db.py` / `process.py` / `logging.py` を作り、small schema、fixed-line parser、id-only NOTIFY、psycopg pool helper、heartbeat、JSONL logger を用意した。
- `docker/postgres/init/100_v2_core.sql` を追加し、v2 core table と `v2_notify_id(channel_name, event_id)` を定義した。
- hot-path browser shell、VAD pre-roll、streaming STT observation 変換、tomoko-process の session/floor/prompt core、model/TTS fake execution pathを実装した。
- short reaction、initiative motivation、user status、info acquire、summary、candidate generation、prompt cancellation、floor holding、follow-up、stop arbitration、evaluation logging/report の deterministic scaffold を実装した。
- `make v2-runtime` / `v2-stop` / `v2-info-once` / `v2-initiative-sim` / `v2-floor-bench` / `v2-report-latest` を追加した。
- `_docs/latency.md` に v2 scaffold smoke と live first audio 未測定であることを追記した。

### 詰まったこと・解決したこと
- root `MEMORY.md` が存在しなかったため、v1 `MEMORY.md` と root `LOG.md` を参照してから V2.0 として root `MEMORY.md` を作成した。
- scripts を `python scripts/foo.py` で実行すると `server` package が import path に乗らなかったため、`scripts/__init__.py` を追加し Make target を `python -m scripts...` に変更した。
- FastAPI shell は `index.html` だけでは `/client/main.js` が 404 になるため、`/client` static mount を追加した。
- integration test は `TEST_DATABASE_URL` が未設定のため skip になる。実 DB schema の insert/select/FK/NOTIFY 確認は DB 起動後に実行する。
- V2.20 の 10 分 live conversation smoke は外部 runtime 依存のため未実行。readiness hook と report hook までは実装済み。

### 検証
- `make check`
  - unit: 28 passed, 1 deselected
  - ruff: passed
- `uv run pytest -m integration -q`
  - 1 skipped, 28 deselected (`TEST_DATABASE_URL` 未設定)
- `make -n v2-runtime v2-stop`
  - hot-path / tomoko / info / user-status / summary / think の tmux 起動順と Ctrl-C 停止順を確認した。
- `make v2-info-once`
  - sample calendar DTO map を出力した。
- `make v2-initiative-sim`
  - synthetic high-pressure scenario で 4 秒以降 `would_initiate=true` になることを確認した。
- `make v2-floor-bench`
  - 600/800/1000/1200/1500ms pause の holding decision を出力した。
- `uv run python -m server.runtime readiness`
  - DB / LLM / VOICEVOX / Apple Speech / OCR の readiness expectations を出力した。
- `make v2-report-latest`
  - `reports/v2-latest.html` を生成した。
- `git diff --check`
  - passed
- `git diff -- v1`
  - no diff
- `uv run uvicorn server.hot_path.app:app --host 127.0.0.1 --port 8020`
  - 起動済み。`/` と `/client/main.js` の HTTP smoke が通った。

### 次のセッションでやること
- DB を起動して `TEST_DATABASE_URL=... uv run pytest -m integration -q` を実行する。
- Apple Speech / VOICEVOX / LLM runtime を起動した状態で V2.20 の 10 分 live conversation smoke を行い、first content / first audio / total latency を `_docs/latency.md` に追記する。

## 2026-06-18 セッション1

### やること（開始時に書く）
- v2 を始めるため、v1 の `PLAN.md` / `MEMORY.md` / `LOG.md` と root の v2 設計メモを読み、v2 の実装手順を root `PLAN.md` に書く。
- root にはまだ `PLAN.md` / `LOG.md` / `MEMORY.md` が無いため、v1 の記録を参照元として扱い、v2 用の `PLAN.md` と `LOG.md` を作る。

### やったこと
- v1 の `MEMORY.md` / `LOG.md` / `PLAN.md`、root `ARCHITECTURE.md`、`_docs/v2.md`、`_docs/v2-alpha.md`、`_docs/v2-2.md`、`_docs/thinkerv2.md`、`_docs/evaluation.md` を確認した。
- root `PLAN.md` を新規作成し、v1 から継承する知見、v2 の process map、Phase V2.0 から V2.20 までの実装手順と完了条件を書いた。
- root `LOG.md` を新規作成し、このセッションの開始記録と完了記録を残した。

### 詰まったこと・解決したこと
- root には `PLAN.md` / `LOG.md` / `MEMORY.md` が存在しなかったため、AGENTS.md の作業開始手順は v1 側の記録を参照して満たし、v2 用には root `PLAN.md` / `LOG.md` を新規作成した。
- 今回は計画ドキュメントのみの作業で、v2 実装コードはまだ無いため unit test は実行していない。

### 検証
- `git diff --check -- PLAN.md LOG.md`
  - passed
- `wc -l PLAN.md LOG.md`
  - `PLAN.md` 586 lines / `LOG.md` 25 lines

### 次のセッションでやること
- `PLAN.md` の Phase V2.0 に従い、root `README.md` / `MEMORY.md` / v2 用ディレクトリ / root Makefile を作る。
