## 2026-06-06 セッション4

### やること（開始時に書く）
- macOS LaunchAgent で `make daily` を一日一回起動するための repo-local wrapper と plist template を追加する
- 人間が `~/Library/LaunchAgents` へコピーして使えるように、コピー手順と手動 kickstart 手順を repo 配下へ残す
- `make daily` が長時間化しても重複起動しないように wrapper 側で lock を持たせる

### やったこと
- `_tools/run_daily_launchagent.sh` を追加し、repo root 解決、PATH 設定、`mise` presence check、`make daily` 実行、`logs/daily-launchagent.log` への記録をまとめた
- `_tools/launchagents/com.tomoko.daily.plist` を追加し、07:30 local time に user LaunchAgent として wrapper を起動する template にした
- `_tools/launchagents/README.md` に `~/Library/LaunchAgents` への copy / bootstrap / enable / kickstart / bootout 手順を追加した
- `tests/unit/test_launchagent_daily_artifacts.py` を追加し、wrapper / plist / README の運用 contract を固定した

### 詰まったこと・解決したこと
- macOS 標準環境では `flock` が無い場合があるため、重複起動防止は `${TMPDIR:-/tmp}/tomoko-daily.lock` の atomic directory lock にした
- launchd が plist の stdout/stderr path を開く時点では repo の `logs/` 作成前になりうるため、plist の bootstrap log は `/tmp`、詳細 log は wrapper 内で `logs/daily-launchagent.log` に出す形にした

### 検証
- focused unit: `uv run pytest -m unit tests/unit/test_launchagent_daily_artifacts.py -q`
  - 3 passed

### 次のセッションでやること
- 人間が plist を `~/Library/LaunchAgents` へコピーした後、`launchctl kickstart -k gui/$(id -u)/com.tomoko.daily` で一回起動し、`logs/daily-launchagent.log` と world observation / gcal / journalist の結果を確認する

## 2026-06-06 セッション3

### やること（開始時に書く）
- Tomoko 側の `voicevox_tsumugi_chunked` で pending 1 chunk 遅延を消す
- PR1823 `audio/wav` stream の complete WAV 再包装は維持したまま、`segment_length` を 0.6 秒から 0.2 秒へ短縮して first chunk latency を比較する
- focused unit / ruff を通し、可能なら PR1823 launcher 直叩き smoke の first chunk / total / 音質を `_docs/latency.md` に記録する

### やったこと
- PR1823 `audio/wav` stream path の Tomoko 側 pending 1 chunk 保持をやめ、complete WAV chunk 1 個分の PCM が貯まった時点で即 yield するようにした
- legacy multipart path も part ごとに即 yield し、`X-Is-Last` header 由来の `is_last` を維持する形に戻した
- central / edge の `voicevox_tsumugi_chunked.segment_length` を 0.2 秒に変更した
- README / ARCHITECTURE.md / PLAN.md / MEMORY.md / `_docs/latency.md` に first chunk latency 改善の判断と実測値を追記した

### 詰まったこと・解決したこと
- 最初の unit test は fake stream が連続で全 bytes を返すため pending delay を観測できなかった
  - gated fake stream を追加し、次 network chunk を許可する前に first chunk が返ることを test で固定した
- `is_last=True` と first chunk 即時 yield は exact multiple の stream では完全には両立しない
  - PR1823 `audio/wav` stream では途中 chunk を `is_last=False` で即時送信し、終了時に残った final chunk だけ `is_last=True` にする

### 検証
- red test: `uv run pytest -m unit tests/unit/test_voicevox_tts.py::test_voicevox_chunked_backend_yields_first_pr1823_wav_chunk_without_pending_delay -q`
  - pending 1 chunk delay のため timeout failure
- focused unit: `uv run pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_prepare_runtime.py -q`
  - 29 passed
- focused ruff: `uv run ruff check server/shared/config.py server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 628 passed, 23 deselected
- diff check: `git diff --check -- server/shared/config.py server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py config/central_realtime.toml config/edge_kitchen.toml PLAN.md LOG.md MEMORY.md README.md ARCHITECTURE.md _docs/latency.md`
  - pass
- PR1823 launcher backend smoke:
  - `async-voicevox/run_streaming_voicevox.command` で `http://127.0.0.1:50122` を起動
  - text `うん、わかった。少し待ってね。`
  - `segment_length=0.6`: first chunk 1512.2ms、total 2283.6ms、5 chunks
  - `segment_length=0.2`: first chunk 166.0ms、total 2203.1ms、15 chunks
  - combined WAV artifacts: `logs/voicevox-pr1823-segment-compare/segment_0_6.wav`, `logs/voicevox-pr1823-segment-compare/segment_0_2.wav`

### 次のセッションでやること
- 実ブラウザ `make server-debug` で playback queue / `decodeAudioData()` 側の体感と音質を確認する

## 2026-06-06 セッション2

### やること（開始時に書く）
- `async-voicevox` の PR1823 版 launcher `http://127.0.0.1:50122` に Tomoko default を合わせる
- `/streaming_synthesis` が旧 multipart ではなく `audio/wav` stream を返す場合も `voicevox_tsumugi_chunked` で扱えるようにする
- browser client へ raw PCM を流さず、既存の complete WAV chunk / `decodeAudioData()` contract を維持する

### やったこと
- `BackendSpec` に `segment_length` を追加し、`voicevox_chunked` backend から `/streaming_synthesis` へ渡せるようにした
- `VoicevoxChunkedBackend` が legacy `multipart/mixed` と PR1823 `audio/wav` response の両方を受けられるようにした
- PR1823 `audio/wav` stream では WAV header を読み、受信 PCM を `segment_length` 相当の complete WAV に包み直して `AudioChunkOut` として yield する
- central / edge の `voicevox_tsumugi_chunked` URL を `http://127.0.0.1:50122` に変更し、`sample_rate = 24000` / `segment_length = 0.6` を設定した
- README / ARCHITECTURE.md / PLAN.md / MEMORY.md / `_docs/latency.md` に PR1823 stream contract を追記した

### 詰まったこと・解決したこと
- PR1823 の `/streaming_synthesis` は multipart part ではなく、WAV header の後ろに PCM が伸びる single `audio/wav` response だった
  - Tomoko の WebSocket binary 再生境界は変えず、backend で PCM 区間を complete WAV に再包装する形にした
- WAV の `data` chunk size は全体サイズを宣言するため、そこまで待つと streaming latency が落ちる
  - `data` chunk header を見つけた時点で PCM stream として読み始めるようにした
- PR1823 は 24kHz mono のみ対応で、既存の 16kHz VOICEVOX 出力設定を渡すと 422 になる
  - PR1823 chunked default だけ 24kHz にし、通常/stream 比較 backend の 16kHz 設定は残した

### 検証
- focused VOICEVOX/config unit: `uv run pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_prepare_runtime.py -q`
  - 28 passed
- focused ruff: `uv run ruff check server/shared/config.py server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 627 passed, 23 deselected
- diff check: `git diff --check -- server/shared/config.py server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py config/central_realtime.toml config/edge_kitchen.toml PLAN.md LOG.md MEMORY.md README.md ARCHITECTURE.md _docs/latency.md`
  - pass
- PR1823 launcher backend smoke:
  - `async-voicevox/run_streaming_voicevox.command` で `http://127.0.0.1:50122` を起動
  - `VoicevoxChunkedBackend(url=50122, speaker_id=8, sample_rate=24000, chunk_min_accent_phrases=1, segment_length=0.6)` を直叩き
  - text `うん、わかった。少し待ってね。` は 5 complete WAV chunks、first chunk 約 1024.1ms、total 約 1652.6ms
  - 各 chunk は 24kHz mono WAV として `wave` module で読めた

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、first chunk latency と音質を確認する

## 2026-06-06 セッション1

### やること（開始時に書く）
- attention timeout の体感が短い問題に対応する
- 既定値を `engaged -> cooldown` 120 秒、`cooldown -> ambient` 60 秒へ変更する
- unit test を先に更新して、TomoroSession の既定 timeout contract を固定する

### やったこと
- `TomoroSession` の既定 `engaged_timeout_ms` を 20 秒から 120 秒へ変更した
- 既定 `cooldown_timeout_ms` を 8 秒から 60 秒へ変更した
- `tests/unit/test_attention_mode.py` に、120 秒未満では engaged のまま、120 秒で cooldown、60 秒未満では cooldown のまま、60 秒で ambient になる contract test を追加/更新した
- PLAN.md / MEMORY.md / `_docs/latency.md` に attention timeout 延長の判断と検証を追記した

### 詰まったこと・解決したこと
- 既存 worktree には world observation / VOICEVOX 周辺の未コミット変更があったため、今回の変更対象は attention timeout 関連ファイルに限定した
- attention timeout は wall-clock ではなく 16kHz / 512 sample chunk 由来の idle 無音積算なので、120 秒は 3750 chunk、60 秒は 1875 chunk として test で固定した

### 検証
- red test: `uv run pytest -m unit tests/unit/test_attention_mode.py::test_default_engaged_timeout_waits_two_minutes_before_cooldown tests/unit/test_attention_mode.py::test_default_cooldown_timeout_waits_one_minute_before_ambient -q`
  - 既定値変更前に 2 failed
- focused attention unit: `uv run pytest -m unit tests/unit/test_attention_mode.py -q`
  - 10 passed
- focused ruff: `uv run ruff check server/session.py tests/unit/test_attention_mode.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 626 passed, 23 deselected
- diff check: `git diff --check -- server/session.py tests/unit/test_attention_mode.py LOG.md MEMORY.md PLAN.md _docs/latency.md`
  - pass

### 次のセッションでやること
- live browser 会話で、外部調査待ちや follow-up 待ち中の attention 表示が体感どおり残るか確認する

## 2026-06-05 セッション3

### やること（開始時に書く）
- `make information-collect-world` が `status=timeout` / `chars=5` で落ちた原因を切り分ける
- Tomoko 側の subprocess timeout と operator 側の Perplexity response timeout の境界を確認する
- world observation の長文生成に必要な timeout / snapshot 観測を修正し、focused test で固定する

## 2026-06-05 セッション2

### やること（開始時に書く）
- world information / world observation の収集を Codex / Computer Use 手動運用に頼る方針を見直す
- 隣 repo `tomoko-research-operator` の CDP / Perplexity automation を使って raw Markdown artifact を作れるか確認する
- 可能なら Tomoko 側に operator subprocess 呼び出しの CLI / make target / test を追加し、既存 ingest / interpret flow に接続する

### やったこと
- `tomoko-research-operator` 側に `world.observe` MCP tool を追加した
- operator は既存の Chrome CDP / Perplexity fresh-tab / wait / artifact 保存経路を再利用し、`WorldObservationResult.markdown_text` を返すようにした
- Tomoko 側に `WorldObservationMcpClient` と `_tools/collect_world_observation.py` を追加した
- `make information-collect-world` を追加し、`informations/prompts/daily_world_observation.md` を当日の日付 / observed_at に差し替えて operator subprocess へ渡せるようにした
- operator から返る provider text は Tomoko 側で deterministic frontmatter を付け直し、`informations/work/YYYY-MM-DD-world-observation.md` に保存する形にした
- README / informations README / PLAN.md / MEMORY.md / `_docs/latency.md` に operator 収集境界を追記した

### 詰まったこと・解決したこと
- Perplexity DOM の `innerText` では Markdown frontmatter delimiter が落ちる可能性がある
  - provider text をそのまま信じず、Tomoko 側が既知 schema の frontmatter を付け直して strict validator に渡す形にした
- operator repo と Tomoko repo の両方を触る必要があった
  - operator は CDP / provider text / raw artifact だけ、Tomoko は frontmatter / work file / ingest だけ、という既存責務境界を維持した

### 検証
- operator focused/full: `uv run pytest -q`
  - 23 passed
- operator ruff: `uv run ruff check .`
  - pass
- operator diff check: `git diff --check`
  - pass
- Tomoko focused unit: `uv run pytest -m unit tests/unit/test_world_observation_operator_client.py tests/unit/test_makefile_process_entries.py tests/unit/test_world_observation_raw_markdown.py tests/unit/test_world_observation_ingest.py tests/unit/test_world_observation_normalizer.py -q`
  - 25 passed
- Tomoko make dry-run: `make -n information-collect-world`
  - `2026-06-05` の work 出力 target で `_tools/collect_world_observation.py` が呼ばれることを確認
- Tomoko full unit: `uv run pytest -m unit -q`
  - 621 passed, 23 deselected
- Tomoko global ruff: `uv run ruff check .`
  - pass
- Tomoko diff check: `git diff --check`
  - pass

### 次のセッションでやること
- ログイン済み Chrome / Perplexity を起動した状態で `make information-collect-world` を実行し、生成された work Markdown を strict validator / dry-run ingest に通す
- strict validation 後に `make information-ingest-once` / `make information-interpret-once` で DB 取り込みまで確認する

## 2026-06-05 セッション1

### やること（開始時に書く）
- `~/by-llms/async-voicevox` の `/streaming_synthesis` multipart chunk contract を Tomoko 側 TTS backend に取り込む
- `chunk_min_accent_phrases = 1` で叩ける VOICEVOX chunked backend を追加する
- central / edge の default TTS を chunked backend に切り替え、config contract test と docs を同期する

### やったこと
- `VoicevoxChunkedBackend` を追加し、VOICEVOX Engine の `/streaming_synthesis` multipart response を `AudioChunkOut(data, sequence, is_last)` として yield できるようにした
- `BackendSpec.chunk_min_accent_phrases` と `voicevox_chunked` factory branch を追加した
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の default TTS を `voicevox_tsumugi_chunked` に切り替えた
- `make prepare` 相当の VOICEVOX readiness 対象に `voicevox_chunked` を含めた
- README / ARCHITECTURE.md / PLAN.md / MEMORY.md / `_docs/latency.md` に chunked VOICEVOX default 方針を追記した

### 詰まったこと・解決したこと
- 既存 `voicevox_stream` は `/cancellable_synthesis` bytes を結合して 1 WAV にする backend だった
  - 今回は別 backend として `/streaming_synthesis` を扱い、multipart part ごとの complete WAV chunk を preserve する形にした
- `make prepare` は `voicevox` / `voicevox_stream` だけを VOICEVOX app readiness 対象にしていた
  - `voicevox_chunked` も同じ外部 Engine 依存として扱うようにした

### 検証
- red/focused unit: `uv run pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py -q`
  - 初回は `VoicevoxChunkedBackend` 未実装で collection error
- focused unit: `uv run pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_prepare_runtime.py -q`
  - 27 passed
- focused ruff: `uv run ruff check server/shared/config.py server/shared/inference/tts/__init__.py server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py _tools/prepare_runtime.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 615 passed, 23 deselected
- global ruff: `uv run ruff check .`
  - pass

### 次のセッションでやること
- chunked VOICEVOX Engine を実起動した状態で `make server-debug` を走らせ、first chunk latency と live 音質を確認する
- 必要なら `chunk_min_accent_phrases` を 1 / 2 / 4 で比較する

## 2026-06-02 セッション7

### やること（開始時に書く）
- timer / alarm due 通知の gate を調整する
- user が listening 中は通知を保持し、Tomoko の通常 reply / initiative / research notice 中は audio stop して timer/alarm notice に切り替える
- output 未接続時は failed 即確定にする

### やったこと
- timer/alarm due reducer を candidate / arrival の ambient-only gate から分けた
- output 未接続時は `timer_alarm_due_failed` emission と `mark_timer_alarm_failed` command を出すようにした
- listening 中の due は `timer_alarm_due_deferred` として TomoroSession 内に保持し、`idle` 遷移時に再投入するようにした
- Tomoko の reply / playback が active な時は `cancel_reply_generation` と `send_audio_control_stop` を先に出してから due notice を開始するようにした
- internal command runner が `cancel_reply_generation` / `send_audio_control_stop` command を実行できるようにした
- PLAN.md / MEMORY.md / `_docs/latency.md` に preemptive notice 方針と検証を追記した

### 詰まったこと・解決したこと
- 最初の preempt test は `audio_start` だけを作っており、実際の `playback_state` は idle だった
  - `reserve_audio_chunk()` で Tomoko speaking 状態を作る test に直して、preempt command を確認した

### 検証
- red/focused unit: `uv run pytest -m unit tests/unit/test_timer_alarm_voice.py -q`
  - 初回は output 未接続 / listening defer / preempt の期待で 3 failed
  - 実装後 23 passed
- related unit: `uv run pytest -m unit tests/unit/test_timer_alarm_voice.py tests/unit/test_barge_in.py -q`
  - 39 passed
- timer/alarm integration: `uv run pytest -m integration tests/integration/test_timer_alarm_db.py tests/integration/test_smoke_timer_alarm_session_flow.py -q`
  - 3 passed
- focused ruff: `uv run ruff check server/session.py tests/unit/test_timer_alarm_voice.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 613 passed, 23 deselected
- global ruff: `uv run ruff check .`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで Tomoko 発話中に timer/alarm due が来た時、audio queue が止まって通知へ切り替わることを確認する
- listening 中 due の保持が、実 STT final 後に自然なタイミングで通知されるか確認する

## 2026-06-02 セッション6

### やること（開始時に書く）
- 未コミット変更をレビューし、問題がなければテスト確認後に commit / push する
- `.gitignore.swp` のような一時ファイルが混ざっていないか確認する

### やったこと
- 未コミット差分をレビューし、timer/alarm foundation、recent speech echo guard、shared context warm-up の変更を確認した
- `.gitignore.swp` が Vim swap file であることを確認し、コミット対象から削除した
- `MEMORY.md` に timer/alarm の DB row / TomoroSession gate 方針を追記した
- `_docs/latency.md` に今回の unit / integration 検証結果を追記した

### 詰まったこと・解決したこと
- edge gateway session は `tts_backend=None` だが、gateway は `reply_text` を edge browser へ送り、`EdgeReplyPlayer` が edge-local TTS/audio chunk 化するため due 通知の既存 audio lane 境界は維持されていると確認した
- `.gitignore` の末尾空行で `git diff --check` が一度 failed したため修正した

### 検証
- focused ruff: `uv run ruff check server/shared/timer_alarm.py server/session.py server/edge/main.py server/gateway/audio_turn.py server/gateway/context.py server/gateway/turn_taking/barge_in.py tests/unit/test_timer_alarm_voice.py tests/unit/test_barge_in.py tests/integration/test_timer_alarm_db.py tests/integration/test_smoke_timer_alarm_session_flow.py _tools/smoke_timer_alarm_session_flow.py`
  - pass
- focused unit: `uv run pytest -m unit tests/unit/test_timer_alarm_voice.py tests/unit/test_barge_in.py -q`
  - 37 passed
- full unit: `uv run pytest -m unit -q`
  - 611 passed, 23 deselected
- timer/alarm integration: `uv run pytest -m integration tests/integration/test_timer_alarm_db.py tests/integration/test_smoke_timer_alarm_session_flow.py -q`
  - 3 passed
- global ruff: `uv run ruff check .`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で timer/alarm の作成 acknowledgement と due 通知音声を確認する
- 必要なら timer worker を realtime process 内 polling から別プロセス運用に切り出す smoke / make target を追加する

## 2026-06-02 セッション3

### やること（開始時に書く）
- task ledger の会話操作 Phase として create_task / complete_task だけを実装する
- update / cancel は会話仕様から落とし、変更したい場合は完了して作り直す運用にする
- complete_task の曖昧照合は background structured extractor に任せ、DB 更新は deterministic validator で確定する

### やったこと
- `TaskLedgerIntentDetector` / `TaskLedgerCommandRunner` を追加し、create / complete / unsupported を rule-first で扱うようにした
- create は normalized title 由来の deterministic id で `task_ledger_entries` に active row を upsert するようにした
- complete は active task の exact / normalized match を先に試し、曖昧な場合だけ memory_extraction backend の structured extractor に候補 id を出させるようにした
- structured extractor の出力は single candidate / active id / confidence 閾値で deterministic validator が検証し、低 confidence・存在しない id・複数候補では DB 更新しないようにした
- update / cancel は unsupported として task row を変更せず、ユーザーには完了して作り直す運用を促す reply directive を返すようにした
- `TomoroSession` に `task_ledger_requested` / `task_ledger_update_finished` reducer と background transition handler を追加した
- central browser session と edge gateway text session で `TaskLedgerCommandRunner` を接続した
- Postgres store に `complete_task()` を追加し、integration test で二重完了が false になることを確認した

### 詰まったこと・解決したこと
- 「ログ確認をタスクにして」「ログ確認は終わった」のような日本語発話で助詞が title に残るため、wake name / cue 除去後に edge particle を落とすようにした
- 「確認」のような短い fragment は deterministic partial match で完了できるため、曖昧 complete の test は partial match しない発話で structured extractor を通すようにした
- task update の background handler は initial accepted emission を session 側で即送信し、runner は finished event だけを返す形にした

### 検証
- red/focused unit: `uv run pytest -m unit tests/unit/test_task_ledger_voice.py -q`
  - 初回は runner session 渡し忘れと曖昧 complete test の partial match で 3 failed
  - 修正後 7 passed
- focused related unit: `uv run pytest -m unit tests/unit/test_task_ledger_voice.py tests/unit/test_phase88_context_snapshot.py::test_fast_snapshot_reads_top_active_task_ledger_entries tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_can_read_more_active_task_ledger_entries tests/unit/test_phase4_thinking.py -q`
  - 28 passed
- task ledger integration: `uv run pytest -m integration tests/integration/test_task_ledger_db.py -q`
  - 1 passed
- focused ruff: `uv run ruff check server/shared/task_ledger.py server/session.py server/edge/main.py tests/unit/test_task_ledger_voice.py tests/integration/test_task_ledger_db.py`
  - pass
- global ruff: `uv run ruff check .`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 585 passed, 20 deselected
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で `タスクにして` / `終わった` の live event sequence と DB row 更新を確認する
- `needs_confirmation` になった曖昧 complete のユーザー確認 UI / 発話を次 Phase として切るか判断する

## 2026-06-02 セッション2

### やること（開始時に書く）
- task-like working context を short memory hint のまま扱う方針を否定し、DB 永続化された task ledger として実装する
- ContextSnapshotBuilder で active task の軽量 slice を prompt に復帰できるようにする
- short / deep の最終境界は固定せず、まず active task 先頭10件の低コスト読み取りと詳細取得の余地を test / 実測で確認する

### やったこと
- `task_ledger_entries` DDL と `PostgresTaskLedgerStore` / `InMemoryTaskLedgerStore` を追加した
- `TaskLedgerEntry` DTO と ContextBuildPolicy の task ledger 上限を追加した
- ContextSnapshotBuilder が `task_ledger` source を deadline / trace / cache 対象として読み、snapshot に `task_ledger_entries` を返すようにした
- fast / normal は active task 先頭10件、deep は25件、reflective は50件を読む境界にした
- ThinkFastMode の system prompt に `TASK CONTEXT` を追加し、short memory ではなく DB structured task として扱うよう明示した
- runtime default で `PostgresTaskLedgerStore` を TomoroSession / ContextSnapshotBuilder に渡すようにした
- README / ARCHITECTURE.md / MEMORY.md / `_docs/latency.md` に task ledger 方針と検証を追記した

### 詰まったこと・解決したこと
- short memory から task ledger への自動昇格や、音声 transcript からの追加/完了/取消 reducer まで同時に入れると判断が広がるため、今回は保存先・読み取り・prompt 復帰に限定した
- task ledger は embedding retrieval ではなく active/priority 順の軽量 DB slice とし、詳細取得は deep / reflective policy の上限で広げる形にした

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_phase88_context_snapshot.py::test_fast_snapshot_reads_top_active_task_ledger_entries tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_can_read_more_active_task_ledger_entries tests/unit/test_phase4_thinking.py::test_think_fast_includes_task_context_from_snapshot -q`
  - `TaskLedgerEntry` 未定義で collection error
- focused task ledger unit: `.venv/bin/pytest -m unit tests/unit/test_phase88_context_snapshot.py::test_fast_snapshot_reads_top_active_task_ledger_entries tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_can_read_more_active_task_ledger_entries tests/unit/test_phase4_thinking.py::test_think_fast_includes_task_context_from_snapshot -q`
  - 3 passed
- related unit: `.venv/bin/pytest -m unit tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py tests/unit/test_prepare_runtime.py -q`
  - 49 passed
- DB integration: `.venv/bin/pytest -m integration tests/integration/test_task_ledger_db.py -q`
  - 1 passed
- Postgres task ledger context microbench:
  - fast avg 0.541ms / max 4.881ms, deep avg 0.561ms / max 5.083ms
  - fixture active tasks 12 件、fast returned 10 件、deep returned 12 件
- focused ruff: `.venv/bin/ruff check server/shared/models.py server/shared/task_ledger.py server/gateway/context.py server/gateway/thinking/fast.py server/session.py server/edge/main.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py tests/integration/test_task_ledger_db.py`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 578 passed, 20 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- transcript から task の追加 / 完了 / 取消を deterministic reducer で `task_ledger_entries` に反映する Phase を切る
- 実ブラウザ会話で `TASK CONTEXT` 追加後の context build elapsed_ms と prompt 量を `logs/server-debug.log` で確認する

## 2026-06-01 セッション12

### やること（開始時に書く）
- `persona_overlay.md` の既存 unit failure の原因を確認する
- overlay 本文の具体語句で壊れる test を、読み込み contract だけに薄くする

### やったこと
- `test_persona_overlay_describes_inspired_style_without_original_lines` が `原作台詞` という具体語句を期待していることを確認した
- `persona_overlay.md` は体感調整で頻繁に変わる文面なので、repo-local overlay test を「存在する場合に UTF-8 で読める」だけに変更した
- sibling overlay が `ThinkFastMode` の prompt に入る挙動の test は維持した
- PLAN.md / MEMORY.md に persona overlay test 方針を追記した

### 詰まったこと・解決したこと
- overlay の安全ガード文を本文に戻す案もあったが、ユーザー方針どおり文言固定 test は壊れやすいためやめた
- test の責務を「内容の審査」ではなく「overlay loading contract」に絞った

### 検証
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_phase4_thinking.py::test_persona_overlay_file_is_readable_when_present tests/unit/test_phase4_thinking.py::test_think_fast_includes_persona_overlay_when_sibling_file_exists tests/unit/test_phase4_thinking.py::test_think_fast_omits_persona_overlay_when_sibling_file_is_missing -q`
  - 3 passed
- focused ruff: `.venv/bin/ruff check tests/unit/test_phase4_thinking.py`
  - pass
- diff check: `git diff --check`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 575 passed, 19 deselected

## 2026-06-01 セッション11

### やること（開始時に書く）
- 最新 `logs/server-debug.log` から、Tomoko の応答崩れと無音感の原因を STT / prompt / LLM / TTS / audio queue で切り分ける
- calendar future 30-day context 変更が prompt を壊していないか確認する
- 原因が実装差分なら focused test つきで hotfix する

### やったこと
- `server-debug.log` と `backend-trace.jsonl` を確認した
- `今何時` が calendar request 扱いになり、未来30日の予定 42 件が prompt に入っていることを確認した
- 同じ予定一覧が `CALENDAR CONTEXT` と `長期コンテキスト` の両方に二重投入されていることを確認した
- `has_calendar_cue()` と `RuleBasedMemoryGate` で純粋な時計質問を calendar cue から外した
- deep calendar turn の現在 prompt では fresh calendar memory を `long_term_memory` から除き、`CALENDAR CONTEXT` との二重投入を止めた
- calendar follow-up 用の carryover は維持した
- PLAN.md / MEMORY.md に hotfix 方針を追記した

### 詰まったこと・解決したこと
- TTS が壊れたように見えたが、`backend-trace.jsonl` では VOICEVOX が audio chunk を返していた
  - 主因は TTS そのものではなく、LLM が `雑談` を反復する壊れた長文を出し、TTS が巨大 chunk を生成して audio queue overflow したことだった
- `今何時` は current local time prompt だけで答えられるため、calendar DB retrieval へ入れる必要がなかった
  - 時計質問を calendar cue / calendar request から外して解決した

### 検証
- focused hotfix unit: `.venv/bin/pytest -m unit tests/unit/test_phase8_memory.py::test_calendar_cue_is_separate_from_deep_memory_cue tests/unit/test_session_memory_gate.py::test_rule_memory_gate_does_not_treat_clock_query_as_calendar_request tests/unit/test_phase88_context_snapshot.py::test_tomoro_session_carries_calendar_context_into_short_followup -q`
  - 3 passed
- focused related unit: `.venv/bin/pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_session_memory_gate.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py::test_think_fast_includes_calendar_context_from_snapshot -q`
  - 41 passed
- focused ruff: `.venv/bin/ruff check server/gateway/thinking/selector.py server/session_memory_gate.py server/session.py tests/unit/test_phase8_memory.py tests/unit/test_session_memory_gate.py tests/unit/test_phase88_context_snapshot.py`
  - pass
- diff check: `git diff --check`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 574 passed, 19 deselected, 1 failed
  - failure: `test_persona_overlay_describes_inspired_style_without_original_lines`
  - `prompts/persona_overlay.md` が `原作台詞` を含まない既存/別件 failure

### 次のセッションでやること
- live server を再起動して、`今何時` の prompt に `CALENDAR CONTEXT` が出ないことと、VOICEVOX 音声が通常サイズで再生されることを確認する

## 2026-06-01 セッション10

### やること（開始時に書く）
- DB の `calendar_events` から 2026年6月の予定を確認する
- 会話 prompt の calendar context が今日から未来30日の予定を全件載せられるようにする
- ContextSnapshotBuilder の calendar window / limit を unit test で固定する

### やったこと
- PostgreSQL `calendar_events` を確認し、2026年6月の confirmed 予定が 42 件あることを確認した
- `ContextSnapshotBuilder(depth="deep")` の calendar window を今日から未来30日に変更した
- deep calendar context の取得上限を 8 件から 64 件に広げた
- `ContextSnapshotBuilder` に `now_provider` を追加し、calendar window の unit test が実日付に依存しないようにした
- PLAN.md / MEMORY.md / ARCHITECTURE.md / README.md / `_docs/latency.md` に未来30日方針を追記した

### 詰まったこと・解決したこと
- 既存 calendar unit は実日付 `datetime.now(UTC)` に依存しており、固定 fixture の 2026-05-30 予定が現在日付から外れて失敗していた
  - `now_provider` を注入できるようにし、test では固定時刻を渡すことで解決した
- full unit は `prompts/persona_overlay.md` の `原作台詞` 期待で 1 件失敗した
  - 今回の calendar 変更とは無関係の既存/別件 prompt 内容差分として切り分けた

### 検証
- DB query: 2026-06-01 から 2026-07-01 未満の confirmed calendar events は 42 件
- red test: `.venv/bin/pytest -m unit tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_reads_all_future_30_day_calendar_context -q`
  - `now_provider` 未実装で failed
- focused calendar unit: `.venv/bin/pytest -m unit tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_reads_calendar_context tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_reads_all_future_30_day_calendar_context tests/unit/test_phase88_context_snapshot.py::test_tomoro_session_carries_calendar_context_into_short_followup -q`
  - 3 passed
- focused related unit: `.venv/bin/pytest -m unit tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py::test_think_fast_includes_calendar_context_from_snapshot tests/unit/test_session_memory_helpers.py -q`
  - 31 passed
- focused ruff: `.venv/bin/ruff check server/gateway/context.py server/shared/models.py tests/unit/test_phase88_context_snapshot.py`
  - pass
- diff check: `git diff --check`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 573 passed, 19 deselected, 1 failed
  - failure: `test_persona_overlay_describes_inspired_style_without_original_lines`
  - `prompts/persona_overlay.md` が `原作台詞` を含まないためで、今回の calendar 変更とは無関係

### 次のセッションでやること
- `prompts/persona_overlay.md` の persona overlay test failure を別セッションで直すか、意図した overlay 内容に合わせて test を更新する

## 2026-06-01 セッション9

### やること（開始時に書く）
- tomoko-research-operator 側で増やした Perplexity 出力が Tomoko 側でどこまで届いているか実 smoke で確認する
- Tomoko が research result follow-up で読む本文量を、operator の増量結果に合わせて増やす
- Research MCP subprocess / TomoroSession follow-up / TTS 入力までを focused test と実 operator smoke で確認する

### やったこと
- Tomoko 側の `ResearchResult` に MCP structuredContent の `full_text` を保持するようにした
- `research_answer_requested` / `start_research_answer_reply` の発話本文を `full_text` 優先にした
- `full_text` がない result では `short_answer` と `bullets` から発話本文を組み立てる互換 fallback を追加した
- `short_answer` は result-ready emission / metadata 用として残し、deep context は従来どおり LLM summary を使う
- PLAN.md / MEMORY.md / `_docs/latency.md` に今回の判断と実 smoke 結果を追記した

### 詰まったこと・解決したこと
- operator artifact には 600〜800 文字級の本文が出ていたが、Tomoko 側の DTO が `full_text` を読んでいなかった
  - MCP parse と TomoroSession follow-up を修正し、operator の増量本文をそのまま発話対象へ渡すようにした
- 最初の real smoke は引数名を `--speech-text` / `--answer-followup-text` と誤って実行し失敗した
  - script の実引数 `--speech` / `--answer-followup` で再実行し、実 operator 経由で成功した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py::test_parse_mcp_tool_call_response_reads_structured_content_and_dedupes_urls tests/unit/test_research_session_contract.py::test_research_answer_requested_speaks_full_text_when_operator_returns_it -q`
  - `full_text` 未保持 / `short_answer` 固定のため 2 failed
- focused research unit: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 51 passed
- focused ruff: `.venv/bin/ruff check server/gateway/research.py server/session.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py`
  - pass
- real operator smoke: `.venv/bin/python _tools/smoke_research_tomoro_session_flow.py --speech '智子、今日の世界情勢について調べて' --answer-followup '結果を教えて' --command 'uv --directory /Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator run tomoko-research-mcp' --timeout-sec 180 --output logs/research-tomoro-session-real-smoke-fulltext.json`
  - `ok=true`, `status=completed`, `speakable=true`
  - `short_answer` 45 文字、`answer_reply_text` 549 文字
  - `research_answer_requested` と follow-up `reply_text` まで確認
- full unit: `.venv/bin/pytest -m unit -q`
  - 571 passed, 19 deselected, 2 failed
  - failures: `test_deep_snapshot_reads_calendar_context`, `test_tomoro_session_carries_calendar_context_into_short_followup`
  - 前セッションから残っている calendar context の既存 failure で、今回の Research full_text 変更とは無関係

### 次のセッションでやること
- live voice で `結果を教えて` 後の長め発話が体感として長すぎないか確認する
- 長すぎる場合は operator prompt ではなく Tomoko 側の発話用整形として、最大文量や source 行除去を別 Phase で検討する

## 2026-06-01 セッション8

### やること（開始時に書く）
- Phase: Client audio device picker
- UI に input device と output device の select を追加する
- input device は `getUserMedia({ deviceId })` に反映し、output device は対応ブラウザで `setSinkId` に反映する
- `/ws` payload、TomoroSession、audio hot path、playback telemetry contract は変更しない

### やったこと
- `client/index.html` に input / output device selector と device status 表示を追加した
- `client/main.js` で `enumerateDevices()` による device list 更新、選択保存、`getUserMedia({ deviceId })`、接続中の mic stream 差し替えを実装した
- Tomoko playback は Web Audio の mixer を通し、`setSinkId` 対応時だけ `MediaStreamDestination` + hidden audio element 経由で選択 output に流すようにした
- `setSinkId` 非対応時や output 切替失敗時は default destination へ fallback する
- `tests/unit/test_client_audio_devices.py` を追加し、client static contract を固定した
- PLAN.md / MEMORY.md / `_docs/latency.md` に今回の client-only 判断と検証を追記した

### 詰まったこと・解決したこと
- Playwright bundled browser binary が未導入だったため、system Chrome (`/Applications/Google Chrome.app`) を使って layout smoke を実行した
- full unit は `tests/unit/test_phase88_context_snapshot.py` の calendar context 2 件で失敗したが、今回の client-only 差分とは無関係の既存 failure と判断した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_client_audio_devices.py -q`
  - device selector / device API usage 未実装で 4 failed
- focused static client: `.venv/bin/pytest -m unit tests/unit/test_client_audio_devices.py -q`
  - 4 passed
- focused ruff: `.venv/bin/ruff check tests/unit/test_client_audio_devices.py`
  - pass
- JS syntax: `node --check client/main.js`
  - pass
- diff check: `git diff --check`
  - pass
- Chrome layout smoke: `http://127.0.0.1:8768/client/index.html`
  - desktop: input / output select visible
  - mobile 390px: `bodyScrollWidth=390`, `viewportWidth=390`
- full unit: `.venv/bin/pytest -m unit -q`
  - 570 passed, 19 deselected, 2 failed
  - failures: `test_deep_snapshot_reads_calendar_context`, `test_tomoro_session_carries_calendar_context_into_short_followup`

### 次のセッションでやること
- 実ブラウザで permission 後に実デバイス label が出ること、選択した mic / speaker で入出力できることを手元確認する
- calendar context の既存 unit failure は別セッションで原因を見る

## 2026-06-01 セッション7

### やること（開始時に書く）
- `assets/tmp/` 配下の ChatGPT 生成キャラクター素材を確認する
- 1 枚の HTML だけで、身体・表情・口差分の重ね合わせイメージを試せるサンプル UI を作る
- 会話 runtime には接続せず、画面演出の素材確認用に閉じる

### やったこと
- `assets/tmp/preview.html` を追加した
- `base_body.png` / `face_*.png` / `mouce_closed.png` / `mouse_open.png` を Canvas layer として重ねる preview にした
- 顔差分・口差分の切り替え、背景抜き threshold、speaking pulse、breathing、face/mouth の X/Y/scale 調整を UI から試せるようにした

### 詰まったこと・解決したこと
- 画像は PNG だが透明 alpha ではなく、市松模様が RGB として焼き込まれていた
  - Canvas で画像端からつながる明るい無彩色背景だけを flood fill で alpha 0 にしてから重ねるようにした
- layer を縮小すると市松背景が画像端から切り離されて残った
  - 元画像サイズで背景抜きを済ませてから、調整後の位置・scale で描画する順序に変えた

### 検証
- `python3 -m http.server 8766 --bind 127.0.0.1` で `assets/tmp/` を配信
- Browser で `http://127.0.0.1:8766/preview.html` を開き、button count / canvas count / status 表示を確認
- `thinking` face / `open` mouth、face scale / Y、mouth scale の調整後も画面が崩れず、背景抜きが適用されることを screenshot で確認

### 次のセッションでやること
- 本番 UI に入れるなら、サーバー状態から `ui_state` event を出す境界を先に決める
- 素材生成を続けるなら、alpha channel 付き PNG になっているか生成直後に確認する

## 2026-06-01 セッション3

### やること（開始時に書く）
- `教えて` が Whisper で `そして` になりがちなため、follow-up 合言葉を `結果を教えて` に寄せる
- `結果を教えて` が実 transcript 経路で research answer を開始することを unit test で固定する
- Tomoko の result-ready notice も `結果を教えて` を促す文に変える

### やったこと
- speakable な `research_result_ready` の notice text を `調べ終わったよ。結果を教えてって言ってね。` に変更した
- `process_transcript("結果を教えて")` が `research_answer_requested` になり、pending result の `short_answer` を発話する unit test を追加した
- smoke / integration の期待値を新しい notice text に更新した

### 検証
- `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 50 passed
- `.venv/bin/ruff check server/session.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/integration/test_research_mcp_smoke.py`
  - pass

### 次のセッションでやること
- live voice で `結果を教えて` が Whisper final transcript として安定するか確認する

### 追記
- live log では `結果を教えて` は Whisper final transcript まで正しく出ていた
- ただし transcript filter が `audio_level_db=-36.6/-37.1` の `結果を教えて` を `low_audio_short_text` として drop していた
- `結果を教えて` を時計 query と同じく明示 command phrase として扱い、低音量短文でも accept する例外を追加した

### 追加検証
- `.venv/bin/pytest -m unit tests/unit/test_stt_filter.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 63 passed
- `.venv/bin/ruff check server/edge/pipeline/stt_filter.py tests/unit/test_stt_filter.py server/session.py tests/unit/test_research_session_contract.py`
  - pass

## 2026-06-01 セッション4

### やること（開始時に書く）
- central realtime の active STT backend を Apple Speech に切り替える
- config unit / README の active backend 表も合わせる

### やったこと
- `config/central_realtime.toml` の `stt_backend` を `local_apple_speech_ja` に変更した
- README の Default Backends 表で STT を Apple Speech、MLX Whisper large turbo q4 を比較候補に戻した
- `tests/unit/test_phase0_config.py` の active STT 期待値を Apple Speech に更新した

### 検証
- `.venv/bin/pytest -m unit tests/unit/test_phase0_config.py -q`
  - 4 passed
- `.venv/bin/ruff check tests/unit/test_phase0_config.py`
  - pass
- `.venv/bin/ruff check .`
  - pass
- `git diff --check`
  - pass
- live server log:
  - `startup warm-up started target=stt backend=local_apple_speech_ja type=apple_speech model=None`
  - `startup warm-up completed target=stt backend=local_apple_speech_ja elapsed_ms=0.8`

### 次のセッションでやること
- live voice で Apple Speech active の聞き取り挙動を確認する

## 2026-05-31 セッション25

### やること（開始時に書く）
- live research request で Tomoko の一次応答は出るが MCP 結果が保存されない件をログで追えるようにする
- `TomoroSession` の research background task schedule / completion / cancel を server log に出す
- `ResearchCommandRunner` / `ResearchMcpClient` の subprocess 起動、完了、timeout、DB 取り込みを観測できるようにする
- 既存設計どおり MCP は常駐プロセスではなく必要時 subprocess 起動であることをログから判断できるようにする

### やったこと
- `TomoroSession.set_research_transition_handler()` と `_dispatch_research_transition_result()` に handler attach / missing / schedule / finish / cancel / failure ログを追加した
- `ResearchCommandRunner` に request start / finish、invalid request、ingestion skip、ingestion success のログを追加した
- `ResearchMcpClient` と subprocess runner に command start、timeout、failure、parse 後 completion、process exit のログを追加した
- Research MCP は常駐 make process ではなく、Tomoko が request ごとに `tomoko-research-mcp` を subprocess 起動する設計であることを PLAN / MEMORY に記録した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py::test_research_mcp_client_logs_subprocess_lifecycle tests/unit/test_research_gateway.py::test_research_mcp_client_logs_timeout tests/unit/test_research_session_contract.py::test_research_command_runner_logs_ingestion_lifecycle tests/unit/test_research_session_contract.py::test_process_transcript_logs_research_background_task_lifecycle -q`
  - 4 failed。期待する lifecycle log が未実装だった
- focused unit / ruff: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_makefile_process_entries.py -q && .venv/bin/ruff check server/gateway/research.py server/session.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py`
  - 45 passed / ruff pass
- fake smoke: `make smoke-research-session`
  - `ok=true`, `status=completed`, `ingested_research_count=1`
- real operator smoke: `uv run python _tools/smoke_research_tomoro_session_flow.py --command 'uv --directory /Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator run tomoko-research-mcp' --timeout-sec 180 --output logs/research-tomoro-session-real-smoke-latest.json`
  - `ok=true`, `status=completed`, `ingested_research_count=1`
- full unit / global ruff: `.venv/bin/pytest -m unit -q && .venv/bin/ruff check .`
  - 561 passed, 19 deselected / ruff pass

### 次のセッションでやること
- 実 server を再起動して live 発話で `Research MCP subprocess starting` 以降が出るか確認する
- もし start が出ない場合は handler 未接続、start は出るが completion がない場合は operator / Chrome UI 側を次に見る

## 2026-05-31 セッション14

### やること（開始時に書く）
- ambient STT の `大変良いと思いますよ私は` が `low_audio_short_text` で drop された件を確認する
- 低音量短文 filter が日本語の普通の一文まで巻き込んでいないか、test を先に追加して境界を固定する
- hallucination 対策は残しつつ、成立した低音量文は UI LOG / ambient log へ進めるようにする

### 分かったこと
- `大変良いと思いますよ私は` は正規化後 12 文字で、既存の `LOW_AUDIO_SHORT_MAX_CHARS = 20` に巻き込まれていた
- 日本語では 20 文字以内でも普通の短文が成立するため、低音量の blanket drop としては広すぎた
- UI LOG に出ない直接原因は、STT 後に `TranscriptFilter` が drop し、`transcript_final` 送信まで進まなかったこと

### やったこと
- `LOW_AUDIO_SHORT_MAX_CHARS` を 20 から 6 に狭めた
- `大変良いと思いますよ私は` と `いいと思います` は低音量でも accept される unit test を追加した
- `たぶんね` のような 6 文字以下の低音量 fragment は `low_audio_short_text` で drop されることを固定した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_stt_filter.py::test_filter_accepts_low_audio_complete_sentence -q`
  - 既存 threshold 20 のため `drop` になり 1 failed
- focused unit + ruff: `.venv/bin/pytest -m unit tests/unit/test_stt_filter.py -q && .venv/bin/ruff check server/edge/pipeline/stt_filter.py tests/unit/test_stt_filter.py`
  - 12 passed / ruff pass
- related unit + ruff: `.venv/bin/pytest -m unit tests/unit/test_stt_filter.py tests/unit/test_phase3_stt.py tests/unit/test_edge_remote_stt_gate.py -q && .venv/bin/ruff check server/edge/pipeline/stt_filter.py tests/unit/test_stt_filter.py tests/unit/test_phase3_stt.py tests/unit/test_edge_remote_stt_gate.py`
  - 18 passed / ruff pass
- full unit + global ruff: `.venv/bin/pytest -m unit -q && .venv/bin/ruff check .`
  - 519 passed, 17 deselected / ruff pass

### 次のセッションでやること
- 実ブラウザ会話で、ambient / observer の成立文が UI LOG に表示されることを確認する

## 2026-05-31 セッション13

### やること（開始時に書く）
- 最新起動ログで相槌が自然だが少し多い体感になったため、MaAI react 閾値を小幅に上げる
- `0.45` で広げた相槌候補を少し絞り、cooldown / gesture audio lane / output lane 境界は変更しない
- test を先に更新して、adapter default と gesture release gate の境界値を固定する

### やったこと
- MaAI adapter の本番 `react_threshold` default を `0.45` から `0.50` に上げた
- `TOMOKO_MAAI_REACT_THRESHOLD` 未指定時の env default も `0.50` に揃えた
- `GestureAudioEmitter` の release gate default も `0.50` に揃えた
- unit test で `0.49` は `below_threshold`、`0.50` は release されることを固定した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_maai_backchannel_adapter.py::test_maai_backchannel_config_uses_production_react_threshold tests/unit/test_maai_backchannel_adapter.py::test_create_maai_backchannel_tap_from_env_uses_production_react_default tests/unit/test_gesture_audio.py::test_gesture_audio_uses_production_react_threshold -q`
  - 既存 default `0.45` のため 3 failed
- focused unit + ruff: `.venv/bin/pytest -m unit tests/unit/test_maai_backchannel_adapter.py tests/unit/test_gesture_audio.py -q && .venv/bin/ruff check server/gateway/maai_backchannel.py server/gateway/gesture_audio.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_gesture_audio.py`
  - 13 passed / ruff pass
- full unit + global ruff: `.venv/bin/pytest -m unit -q && .venv/bin/ruff check .`
  - 516 passed, 17 deselected / ruff pass
- 実 smoke: `make smoke-maai-dialogue`
  - `max_p_bc_react=0.7259804606437683`
  - `suggestions[0].score=0.7259804606437683`
  - `session_releases[0].emissions[0].payload.threshold=0.5`
  - 0.50 に上げても明確な react cue は `gesture_audio` として release されることを確認

### 次のセッションでやること
- 実ブラウザ会話で相槌頻度を確認し、まだ多ければ cooldown 1500ms 側を少し伸ばすか判断する

## 2026-05-30 セッション31

### やること（開始時に書く）
- `_tools/materials/maai.wav` を MaAI 本体へ流し、相槌 suggestion と TomoroSession release / skip を JSON で確認する
- 48kHz stereo WAV を 16kHz 2ch timeline に変換し、ch1=user / ch2=tomoko として MaAI `bc_2type` に流す
- turn 情報がないため、左右 channel の RMS から user speaking / Tomoko speaking を推定して session release gate へ渡す
- 実ブラウザ runtime / hot path は変更しない

### やったこと
- `_tools/smoke_maai_material.py` を追加し、stereo WAV を 16kHz ch1/ch2 timeline として MaAI へ投入できるようにした
- `session_releases[]` に raw score 由来 suggestion、RMS から推定した speaking flags、TomoroSession emission、TTS/audio/reply_done を出すようにした
- `--start-sec` / `--duration-sec` / `--swap-channels` を追加し、長い素材を区間ごとに等速確認できるようにした
- `make smoke-maai-material` を追加した。default は `_tools/materials/maai.wav` の先頭 30 秒を等速確認する

### 詰まったこと・解決したこと
- `_tools/materials/maai.wav` は約 155 秒あり、全体等速確認は長い
  - Makefile default は 30 秒窓にし、`MAAI_MATERIAL_START_SEC` / `MAAI_MATERIAL_DURATION_SEC` で窓をずらす形にした
- `realtime_scale=0` で高速投入すると MaAI の audio queue overflow が起き、raw score が 1 件しか返らなかった
  - 実確認は等速投入に戻した
- ch1/ch2 の役割が逆の可能性があるため、`--swap-channels` も追加した

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_smoke_maai_material.py -q`
  - `_tools.smoke_maai_material` 未実装で import error
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_smoke_maai_material.py tests/unit/test_makefile_process_entries.py -q`
  - 11 passed
- ruff focused: `.venv/bin/python -m ruff check _tools/smoke_maai_material.py tests/unit/test_smoke_maai_material.py tests/unit/test_makefile_process_entries.py`
  - pass
- 実 smoke: `make smoke-maai-material`
  - 0-30 秒: `raw_score_count=300`, `max_p_bc_react=0.3638071119785309`, `max_p_bc_emo=0.12390013784170151`, `suggestions=[]`, `session_releases=[]`
- 全体 30 秒窓 scan:
  - 0-30 秒: suggestion 0
  - 30-60 秒: suggestion 2, release は 2 件とも `backchannel_skipped`
  - 60-90 秒: suggestion 7, release は 7 件とも `backchannel_skipped`
  - 90-120 秒: suggestion 3, release は 3 件とも `backchannel_skipped`
  - 120-150 秒: suggestion 2, release は 2 件とも `backchannel_skipped`
  - 150-155 秒: suggestion 0
- skip reason 確認:
  - 多くは `kind=emo` 由来の `unsupported_kind`
  - `react` suggestion もあったが `score=0.37` 程度で `below_threshold`
  - 現行 gate の `p_bc_react >= 0.68` では `backchannel_released` は出なかった
- swap channel check:
  - 60-90 秒を `swap_channels=True` で確認
  - `max_p_bc_react=0.5122982859611511`, `max_p_bc_emo=0.5429264307022095`, release はすべて `unsupported_kind`

### 次のセッションでやること
- `p_bc_emo` を実際の感情相槌として release 対象にするかは別判断にする
- 現行 react gate のままなら、この素材では相槌音声は入らない

## 2026-05-30 セッション30

### やること（開始時に書く）
- `make smoke-maai-dialogue` の JSON に TomoroSession release 判定結果も出す
- MaAI suggestion が出た時刻を合成 dialogue timeline に対応させ、user speaking / Tomoko idle の時だけ session を listening にして流す
- `backchannel_released` / `backchannel_skipped`、選ばれた文言、audio bytes、`reply_done control=backchannel` をプログラムで確認できるようにする
- runtime hot path や実ブラウザ経路は変更しない

### やったこと
- `_tools/smoke_maai_dialogue.py` に `session_releases[]` を追加した
- MaAI suggestion の `observed_at` を raw score の `observed_sec` に対応させ、合成 timeline 上の user / Tomoko 発話中フラグを出すようにした
- smoke 専用の `SmokeBackchannelTTS` と TomoroSession harness を使い、suggestion を `apply_backchannel_suggestion()` へ流すようにした
- `session_releases[]` には suggestion、timeline flags、session emissions、TTS input、audio chunk/bytes、`reply_done_controls` を出す

### 詰まったこと・解決したこと
- 既存 smoke は MaAI tap までで止まっていたため、`backchannel_released` は unit test でしか見えなかった
  - 同じ script 内で session release simulation まで行い、JSON だけで発火有無を追える形にした
- 実 runtime の WebSocket 経路は変えず、smoke 専用 harness で TTS/audio を記録する形にした

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_smoke_maai_dialogue.py::test_run_dialogue_smoke_records_session_backchannel_release -q`
  - `session_releases` 未実装で failed
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_smoke_maai_dialogue.py tests/unit/test_makefile_process_entries.py -q`
  - 10 passed
- ruff focused: `.venv/bin/python -m ruff check _tools/smoke_maai_dialogue.py tests/unit/test_smoke_maai_dialogue.py`
  - pass
- 実 smoke: `make smoke-maai-dialogue`
  - `suggestions[0].kind=react`
  - `suggestions[0].score=0.7259804606437683`
  - `session_releases[0].timeline.user_speaking=true`
  - `session_releases[0].timeline.tomoko_speaking=false`
  - `session_releases[0].emissions[0].type=backchannel_released`
  - `session_releases[0].audio_chunks=1`
  - `session_releases[0].reply_done_controls=["backchannel"]`
- full unit: `.venv/bin/python -m pytest -m unit`
  - 495 passed, 17 deselected
- global ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザ runtime で MaAI enabled にした時、同じ `backchannel_released` emission が server log / monitor で見えるか確認する

## 2026-05-30 セッション29

### やること（開始時に書く）
- MaAI `p_bc_react >= 0.68` を LLM なし固定相槌として release する
- release 条件は Tomoko が喋っていない、user が話している、同一 user speech segment で未発話、global cooldown 2000ms とする
- 文言は `うん` / `なるほど` / `そっか` から選ぶ
- backchannel audio は conversation log や本返答 LLM に混ぜず、gesture audio として扱う

### やったこと
- `BackchannelSuggestion(kind="react", score>=0.68)` を TomoroSession の release 対象にした
- release gate に user speaking (`listening`)、Tomoko idle、同一 speech segment 1 回、global cooldown 2000ms を追加した
- release 文言は `うん` / `なるほど` / `そっか` の固定 pool から選び、`style="gentle"` の短い TTS として流すようにした
- gateway の MaAI callback は `post_event()` 直呼びではなく `apply_backchannel_suggestion()` を使い、内部 command まで実行するようにした
- backchannel release は `reply_done` に `control="backchannel"` を付け、conversation log へ保存しない経路にした

### 詰まったこと・解決したこと
- 既存の `backchannel_suggested` は観測 emission だけだったため、そのままでは TTS command が走らなかった
  - `TomoroSession.apply_backchannel_suggestion()` を追加し、event reduce と internal command 実行を 1 つの runtime entry にまとめた
- release 直後は audio turn の speaking guard が残るため、unit test の cooldown 確認では playback guard と cooldown guard を分離して検証した

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py::test_maai_react_suggestion_releases_llm_less_backchannel_audio tests/unit/test_maai_backchannel_tap.py::test_maai_backchannel_is_once_per_user_speech_segment tests/unit/test_maai_backchannel_tap.py::test_maai_backchannel_release_requires_user_speaking_and_idle_tomoko tests/unit/test_maai_backchannel_tap.py::test_maai_backchannel_release_applies_global_cooldown -q`
  - `apply_backchannel_suggestion` 未実装で 4 failed
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py -q`
  - 16 passed
- related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_smoke_maai_dialogue.py tests/unit/test_makefile_process_entries.py -q`
  - 25 passed
- ruff focused: `.venv/bin/python -m ruff check server/session.py server/edge/main.py server/gateway/maai_backchannel.py tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py`
  - pass
- 実 smoke: `make smoke-maai-dialogue`
  - `raw_score_count=200`
  - `frames_sent=2008`
  - `duration_sec=20.0753125`
  - `max_p_bc_react=0.7259804606437683`
  - `max_p_bc_emo=0.19943645596504211`
  - `suggestions=[{"kind":"react","score":0.7259804606437683,...}]`
- full unit: `.venv/bin/python -m pytest -m unit`
  - 494 passed, 17 deselected
- global ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで MaAI enabled にした時の相槌タイミングと体感頻度を確認する
- 必要なら `backchannel_released` / `backchannel_skipped` の monitor 表示を追加する

## 2026-05-30 セッション28

### やること（開始時に書く）
- `make smoke-maai-dialogue` を追加し、`say` 合成の user / Tomoko 二者会話を MaAI 本体へ流す
- MaAI `bc_2type` の raw `p_bc_react` / `p_bc_emo` を threshold 前の JSON summary として全部出す
- user / Tomoko 音声を 16kHz mono の同一 timeline に並べ、ch1 / ch2 frame として投入する
- runtime hot path、相槌発話本体、conversation log 保存、MaAI threshold policy は変更しない

### やったこと
- `_tools/smoke_maai_dialogue.py` を追加し、`say` で user / Tomoko の合成二者会話を作るようにした
- 各発話を 16kHz mono float32 に戻し、同一 timeline 上で user=ch1 / Tomoko=ch2 に配置するようにした
- `MaaiBackchannelTap.observe_duplex_audio()` を追加し、10ms frame 単位で ch1/ch2 を同時投入できるようにした
- `RawScoreMaaiTap` が threshold 前の raw `p_bc_react` / `p_bc_emo` を `raw_scores[]` に記録するようにした
- MaAI raw payload の `x1` / `x2` 音声配列は JSON が巨大になるため `raw_omitted_keys` に逃がし、score / metadata だけを `raw` に残した
- `make smoke-maai-dialogue` を追加した

### 詰まったこと・解決したこと
- 初回の実 smoke では MaAI raw payload に `x1` / `x2` の長い音声配列が入り、stdout / JSON が巨大になった
  - raw score 診断に必要な `p_bc_react` / `p_bc_emo` と `t` は残し、音声配列は omit する形にした

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_smoke_maai_dialogue.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_makefile_process_entries.py -q`
  - 16 passed
- ruff focused: `.venv/bin/python -m ruff check _tools/smoke_maai_dialogue.py server/gateway/maai_backchannel.py tests/unit/test_smoke_maai_dialogue.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_makefile_process_entries.py`
  - pass
- 実 smoke: `make smoke-maai-dialogue`
  - `raw_score_count=200`
  - `frames_sent=2008`
  - `duration_sec=20.0753125`
  - `max_p_bc_react=0.7259804606437683`
  - `max_p_bc_emo=0.19943645596504211`
  - `suggestions=[]`
  - output: `logs/maai-dialogue-smoke.json`

### 次のセッションでやること
- threshold を下げる前に、実ブラウザ会話でも raw score logging を取り、合成音声と実音声の分布差を見る
- 相槌を鳴らす場合は `p_bc_react` の peak と playback state を合わせて release / hold / discard を決める

## 2026-05-30 セッション27

### やること（開始時に書く）
- MaAI 本体を optional audio tap implementation として組み込む
- `MaaiInput.Chunk` に user / Tomoko 2ch 音声を流し、`bc_2type` の `p_bc_react` / `p_bc_emo` を読む
- 閾値と cooldown をかけて `BackchannelSuggestion` を `TomoroSession.post_event()` に戻す
- runtime では `TOMOKO_MAAI_BACKCHANNEL_ENABLED=1` の時だけ有効化し、未インストールや disabled 時の通常会話は壊さない
- 相槌 TTS 発話本体、conversation log 保存、MaAI dependency の default 化は今回入れない

### やったこと
- `server/gateway/maai_backchannel.py` を追加し、MaAI `bc_2type` を `AudioInteractionTap` 実装として起動できるようにした
- user mic chunk は ch1、Tomoko WAV chunk は 16kHz mono float32 に decode / resample して ch2 へ流すようにした
- 片側だけの音声が来た場合は反対 channel に無音 160 samples frame を入れる
- `p_bc_react` / `p_bc_emo` を threshold と cooldown で `BackchannelSuggestion(kind=react|emo, source=maai)` に変換するようにした
- `TOMOKO_MAAI_BACKCHANNEL_ENABLED=1` の時だけ gateway runtime が MaAI tap を作り、suggestion を `TomoroSession.post_event()` へ戻すようにした
- `_tools/smoke_maai_tap_session.py --use-maai` と `make smoke-maai-real` を追加し、実ブラウザなしで MaAI 本体を通せるようにした
- `maai==0.1.16` をローカル `.venv` に追加インストールして smoke を確認した

### 詰まったこと・解決したこと
- MaAI の model filename は `10hz` であり、`frame_rate=10.0` だと Hugging Face lookup が `10.0hz` になって 404 した
  - default / env parse を integer `10` 優先にした
- MaAI `get_result()` は blocking queue wait なので、poll task が終了時に残って smoke が止まることがあった
  - `result_dict_queue.get(timeout=0.2)` を使える時だけ timeout poll にして、`stop()` 後に process が抜けるようにした
- real MaAI smoke では短い dummy / say 音声だけなので suggestion は出ない
  - 今回の完了判定は「MaAI 本体へ音声が流れ、runtime を壊さず終了する」までとする

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_adapter.py tests/unit/test_smoke_maai_tap_session.py tests/unit/test_makefile_process_entries.py -q`
  - 15 passed
- ruff focused: `.venv/bin/python -m ruff check server/gateway/maai_backchannel.py tests/unit/test_maai_backchannel_adapter.py`
  - pass
- full unit: `.venv/bin/python -m pytest -m unit`
  - 487 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check`
  - pass
- 実 smoke: `make smoke-maai-real`
  - `maai_enabled=true`
  - `say_invoked=true`
  - `sent_audio_chunks=1`
  - `sent_audio_bytes=40918`
  - `tomoko_tap_chunks=1`
  - `tomoko_tap_bytes=40918`
  - `user_tap_chunks=8`
  - `user_tap_samples=4000`
  - `suggestions=[]`

### 次のセッションでやること
- 実会話ログで `p_bc_react` / `p_bc_emo` の分布を見て threshold / cooldown を調整する
- 相槌を実際に鳴らす場合は、TomoroSession 側で playback / reply state を見て release / hold / discard を決める

## 2026-05-30 セッション26

### やること（開始時に書く）
- 実ブラウザや実マイクなしで MaAI audio tap を確認できる smoke program を追加する
- `TomoroSession` を空回りさせ、`say` 由来の Tomoko WAV を TTS/audio send 経路から optional tap へ流す
- 任意で user 側 dummy/sine audio を `process_audio_chunk()` に入れ、tap の user 側も確認できるようにする
- `make smoke-maai-tap` で実行できるようにする
- MaAI 本体、sidecar process、相槌発話判断、runtime server 経路は変更しない

### やったこと
- `_tools/smoke_maai_tap_session.py` を追加した
- `TomoroSession` を実サーバーなしで生成し、`SayBackend` の WAV を `_flush_tts_text()` / `_send_audio_chunk()` 経由で流すようにした
- recording tap で Tomoko 音声 chunk 数・bytes、user dummy sine chunk 数・samples、`audio_start` / `audio_end` event を JSON summary として出すようにした
- `make smoke-maai-tap` を追加した
- MaAI 本体、sidecar process、相槌発話判断、runtime server 経路は変更していない

### 詰まったこと・解決したこと
- `_tools` 直下から実行すると repo root が import path に入らず `ModuleNotFoundError: No module named 'server'` になったため、既存 bench tool と同じく script 冒頭で repo root を `sys.path` に追加した
- ruff の E402 は、root path 注入後の local import に `# noqa: E402` を付ける既存 tool pattern に合わせた

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_smoke_maai_tap_session.py tests/unit/test_makefile_process_entries.py -q`
  - 9 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 481 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check`
  - pass
- 実 smoke: `make smoke-maai-tap`
  - `say_invoked=true`
  - `sent_audio_chunks=1`
  - `sent_audio_bytes=40918`
  - `tomoko_tap_chunks=1`
  - `tomoko_tap_bytes=40918`
  - `user_tap_chunks=8`
  - `user_tap_samples=4000`

### 次のセッションでやること
- MaAI 本体へつなぐ場合は、この smoke の recording tap を 16kHz 2ch timeline writer / sidecar client に置き換える
- playback telemetry 補正が必要になったら、server send 時刻ではなく browser playback_started 時刻を Tomoko ch2 の基準にする

## 2026-05-30 セッション25

### やること（開始時に書く）
- MaAI / VAP 系の相槌予測を hot path の前段ではなく、別軸の gesture sensor として接続する土台を追加する
- `TomoroSession` に optional audio tap を注入し、人間マイク chunk と Tomoko TTS chunk を複製できるようにする
- tap 失敗時も VAD / STT / TTS / WebSocket audio send が継続することを unit test で固定する
- MaAI 由来の `backchannel_suggested` event を non-authoritative suggestion として `post_event()` で受けられるようにする
- 実 MaAI dependency / sidecar process / 相槌 TTS 発話本体 / gateway 経路変更は今回入れない

### やったこと
- `server/gateway/audio_interaction_tap.py` を追加し、MaAI / VAP sidecar 用の optional audio observer 境界を作った
- `TomoroSession.process_audio_chunk()` 入口で user mic float32 chunk を tap へ複製するようにした
- `TomoroSession._send_audio_chunk()` で Tomoko output audio bytes を tap へ複製するようにした
- tap の同期例外は warning log にして握り、非同期 awaitable は fire-and-forget task として失敗を log するようにした
- `BackchannelSuggestion` DTO を追加し、`backchannel_suggested` event を command なしの non-authoritative emission として扱うようにした
- MaAI dependency、sidecar process、相槌 TTS 発話、gateway 経路変更、通常 reply policy は変更していない

### 詰まったこと・解決したこと
- 最初の user hot path test で `QuietVAD` が idle のままであることを見落としていたため、tap failure で例外が伝播しないことと state が壊れないことを確認する assertion に直した
- Tomoko 側 audio は `_send_audio_chunk()` で観測するため、最初は browser 実再生時刻ではなく server send 時刻を observed_at とする。playback telemetry 補正は別 Phase に残す

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py -q`
  - 5 passed
- related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_phase885_session_runtime.py tests/unit/test_phase5_tts.py -q`
  - 14 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 478 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実 MaAI sidecar を起動する adapter を追加する場合は、この audio tap から 16kHz 2ch timeline へ変換する
- 相槌を実際に鳴らす場合は、`BackchannelSuggestion` を `TomoroSession` の release / hold 判断へ進め、通常 conversation log へ混ぜない

## 2026-05-30 セッション24

### やること（開始時に書く）
- Retrieve と Use を分ける memory gate 境界を追加する
- `MemoryGate` interface / rule 実装 / logging decorator を用意し、差し替え可能にする
- `TomoroSession` は取得済み long-term memory / calendar memory を gate 経由で prompt へ渡す
- 最初の rule として recall request は会話記憶を使い、calendar request は calendar だけ使い、self statement / chitchat / unclear は会話記憶を抑制する
- gate trace を log に残し、今後の調整点を観測できるようにする
- LLM 追加推論、DB schema、ContextSnapshotBuilder の retrieval policy、audio / TTS hot path は変更しない

### やったこと
- `server/session_memory_gate.py` を追加し、`MemoryGate` protocol / `RuleBasedMemoryGate` / `LoggingMemoryGate` を切った
- `TomoroSession` に `memory_gate` を注入可能にし、context snapshot で取れた候補を prompt へ出す前に gate へ通すようにした
- retrieval plan と prompt exposure を分け、`self_statement` / `chitchat` / `unclear` は deep retrieval と prompt exposure を抑制するようにした
- `calendar_request` は calendar memory だけを expose し、会話要約や restored turn は抑制するようにした
- exposed された fresh memory だけを carryover へ保存するようにし、抑制済みの `123` のような memory が次 turn に残らないようにした
- gate log に retrieved / exposed / suppressed / reason / source counts / top suppressed を出すようにした

### 詰まったこと・解決したこと
- project logger 設定の影響で `caplog` に decorator log が載らなかったため、unit では logger injection で観測する形にした
- calendar cue は `deep` snapshot を使うが、gate で calendar source だけを expose することで「予定確認に過去会話が混ざる」問題を避けた

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_gate.py tests/unit/test_phase88_context_snapshot.py::test_tomoro_session_suppresses_self_statement_memory_prompt -q`
  - 6 passed
- broader memory unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_session_memory_helpers.py tests/unit/test_session_carryover.py tests/unit/test_session_memory_gate.py -q`
  - 48 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 473 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff whitespace: `git diff --check`
  - pass

### 次のセッションでやること
- 実会話ログで `memory_gate plan` / `memory_gate filter` を確認し、intent rule の過不足を調整する
- persona / short memory を gate 対象へ広げる場合は、`MemoryGate` の DTO を `MemoryHit` 専用から source slice 単位へ拡張する

## 2026-05-30 セッション21

### やること（開始時に書く）
- Perplexity で外部観測 Markdown を取得し、`informations/work/2026-05-30-world-observation.md` に保存する
- prompt は `informations/prompts/daily_world_observation.md` を基準にし、投入時だけ日付を 2026-05-30 に合わせる
- private page / account 情報 / secret / 個人情報が混ざっていないことを目視する
- 保存後に strict validator と `make information-ingest-dry-run` / `make information-ingest-once` / `make information-interpret-once` を実行する
- Tomoko 本体 runtime / `/ws` / DB schema / prompt source file は変更しない

### やったこと
- Perplexity へ prompt を clipboard paste で投入し、Markdown 形式でダウンロードした
- ダウンロード成果物から Perplexity export header を除き、frontmatter closing delimiter だけ `***` から `---` に直して `informations/work/2026-05-30-world-observation.md` に保存した
- secret / account / private page / 個人情報らしき混入がないことを目視と keyword scan で確認した
- `make information-ingest` alias を追加し、operator recipe のコマンド名で実行できるようにした
- LM Studio SSE の `event: error` payload を空返答として握りつぶさず、backend error として扱うようにした
- world observation normalizer は 26B candidate lane ではなく 31B memory extraction lane を使うようにした
- LLM normalizer が context length / timeout / JSON truncation で失敗した場合、raw Markdown を保持したまま Markdown 見出し由来の deterministic fallback item を作るようにした

### 保存結果
- 保存先: `informations/work/2026-05-30-world-observation.md`
- 文字数: 11734 chars
- ingest 後 archive 先: `informations/archived/2026-05-30/2026-05-30-world-observation.md`

### 詰まったこと・解決したこと
- 26B normalizer は LM Studio から `context length` error を SSE で返していたが、parser が error payload を拾えず `chunk_count=0` / `JSONDecodeError` として見えていた
- 31B は context には入ったが、JSON 生成が長くなり truncation したため、normalizer は代表 item 最大 8 件・backend timeout 45s・deterministic fallback にした
- fallback は本文を書き換えず、DB に残る raw Markdown への traceable entry point として扱う

### 検証
- strict validator: `mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/work/2026-05-30-world-observation.md`
  - valid true / issues 0
- dry-run: `make information-ingest-dry-run`
  - `processed=1 archived=0 failed=0 skipped=1`
  - `would_ingest informations/work/2026-05-30-world-observation.md`
- ingest: `make information-ingest-once`
  - 最終結果: `processed=1 archived=1 failed=0 skipped=0`
- alias check: `make information-ingest`
  - `processed=0 archived=0 failed=0 skipped=0`
- interpret: `make information-interpret-once`
  - `interpreted=10 error_count=0`
- focused unit: `mise exec -- uv run pytest -m unit tests/unit/test_world_observation_normalizer.py tests/unit/test_lm_studio_backend.py tests/unit/test_makefile_process_entries.py -q`
  - 19 passed
- ruff focused: `mise exec -- uv run ruff check background-process/ingest_world_observations.py server/world_observations/normalizer.py server/shared/inference/backends/lm_studio.py server/shared/inference/trace.py tests/unit/test_world_observation_normalizer.py tests/unit/test_lm_studio_backend.py`
  - pass
- full unit: `mise exec -- uv run pytest -m unit`
  - 465 passed, 17 deselected

### 次のセッションでやること
- `make thinker-once` / `make journalist-once` は今回必須ではないため未実行
- LLM normalizer の品質を上げるなら、raw Markdown 全体を一括で JSON 化するのではなく section chunking を検討する

## 2026-05-30 セッション20

### やること（開始時に書く）
- persona updater の LLM output を full snapshot 生成から diff-only 生成へ変更する
- previous snapshot は full JSON ではなく compact prompt slice として渡す
- LLM diff を deterministic code で merge / prune し、巨大 JSON の往復を避ける
- persona updater の max_tokens を 4096 以上へ上げ、`make persona-updater-once` の基本 limit を 1 にする
- DB schema と会話 hot path は変更しない

### やったこと
- persona updater の structured output schema を `lexicon_diff_json` / `state_diff_json` のみにした
- previous snapshot を full JSON ではなく compact prompt slice として渡すようにした
- LLM diff を Python 側で previous snapshot に merge し、salience / 件数上限で prune するようにした
- diff の `added` / `updated` / `deprecated` は schema 側でも `maxItems=6` に制限した
- `PERSONA_UPDATE_MAX_TOKENS` を 4096 にした
- `PERSONA_UPDATE_LIMIT ?= 1` を Makefile の基本値にした

### 変更していないもの
- DB schema
- 会話 hot path
- session summarizer
- persona update backend lane

### 検証
- red test: diff-only schema / compact previous snapshot / deterministic merge / Makefile default の focused test が失敗することを確認
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase87_persona_snapshots.py::test_llm_persona_snapshot_extractor_uses_persona_update_role tests/unit/test_phase87_persona_snapshots.py::test_llm_persona_snapshot_extractor_sends_compact_previous_snapshot tests/unit/test_phase87_persona_snapshots.py::test_llm_persona_snapshot_extractor_merges_diff_deterministically tests/unit/test_makefile_process_entries.py::test_makefile_defaults_persona_updater_once_to_one_session -q`
  - 4 passed
- related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase87_persona_snapshots.py tests/unit/test_makefile_process_entries.py tests/unit/test_router.py tests/unit/test_phase0_config.py tests/unit/test_lm_studio_backend.py -q`
  - 39 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 461 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- 実行確認: `PERSONA_UPDATE_LIMIT=1 make persona-updater-once`
  - `processed=1`
  - backend trace は `role="persona_update"` / `backend="lmstudio_gemma4_31b"` / `model="gemma-4-31b-it-mlx"`
  - `maxItems=6` 追加後は `total_ms=38017.18808300211` / `chunk_count=213` で完了
- targeted diff check: `git diff --check -- server/background/persona_updater.py tests/unit/test_phase87_persona_snapshots.py tests/unit/test_makefile_process_entries.py Makefile PLAN.md MEMORY.md LOG.md`
  - pass

### 注意
- 8192 / 4096 だけでは structured generation が長く継続し、手動停止した
- diff-only に加えて `maxItems=6` を schema に入れた後に実行完了した
- global `git diff --check` は、今回触っていない `prompts/persona_overlay.md` の EOF 空行で失敗する

## 2026-05-30 セッション19

### やること（開始時に書く）
- persona updater が prompt-only JSON 生成になっている問題を直す
- LM Studio の `chat_stream_structured` / `response_format=json_schema` を使い、persona update output を schema で縛る
- persona update 用の max_tokens を明示し、JSON truncation の可能性を下げる
- 会話 hot path、DB schema、session summarizer は変更しない

### やったこと
- `LLMPersonaSnapshotExtractor` を通常 `chat_stream` から `chat_stream_structured_with_trace_role` に変更した
- persona update 用 JSON schema を追加した
- persona update 用 `PERSONA_UPDATE_MAX_TOKENS = 1600` を追加した
- structured output 非対応 backend では明示的に `RuntimeError` にするようにした
- unit test で `trace_role="persona_update"` / `max_tokens=1600` / top-level schema required keys を固定した

### 変更していないもの
- 会話 hot path
- DB schema
- session summarizer
- 31B lane 設定

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase87_persona_snapshots.py::test_llm_persona_snapshot_extractor_uses_persona_update_role -q`
  - 1 passed
- related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase87_persona_snapshots.py tests/unit/test_router.py tests/unit/test_phase0_config.py tests/unit/test_lm_studio_backend.py -q`
  - 31 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 458 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- targeted diff check: `git diff --check -- server/background/persona_updater.py tests/unit/test_phase87_persona_snapshots.py PLAN.md MEMORY.md LOG.md`
  - pass
- 実行確認: `PERSONA_UPDATE_LIMIT=1 make persona-updater-once`
  - `processed=1`
  - backend trace は `role="persona_update"` / `backend="lmstudio_gemma4_31b"` / `model="gemma-4-31b-it-mlx"`
  - DB では `session_summary_completed | lmstudio_gemma4_31b` が 2 件になった

### 注意
- 途中で `make persona-updater-once` の `--limit 10` も検証として起動し、1 件成功後に長いため停止した
- 31B structured persona update は 1 件 88〜142 秒程度かかった
- global `git diff --check` は、今回触っていない `prompts/persona_overlay.md` の EOF 空行で失敗する

## 2026-05-30 セッション18

### やること（開始時に書く）
- persona updater の LLM role を `session_summary` 兼用から分け、31B backend を使うようにする
- `config/central_realtime.toml` に persona update 専用 backend 設定を追加する
- router / config の unit test を先に更新し、persona updater が `persona_update` role を選ぶことを固定する
- 会話 hot path、session summarizer、memory extraction、DB schema は変更しない

### やったこと
- `InferenceSection` に `persona_update_backend` / `persona_update_fallback` を追加した
- `InferenceRouter.select("persona_update", "privacy")` を追加し、専用 backend / fallback を選べるようにした
- `LLMPersonaSnapshotExtractor` が `session_summary` ではなく `persona_update` role を選ぶようにした
- `config/central_realtime.toml` の persona updater lane を `lmstudio_gemma4_31b` にした
- config / router / persona updater の unit test を追加・更新した

### 変更していないもの
- 会話 hot path
- session summarizer の backend
- memory extraction backend
- candidate / diary backend
- DB schema

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_router.py::test_persona_update_role_uses_configured_backend_and_fallback tests/unit/test_phase0_config.py::test_central_realtime_config_uses_lmstudio_gemma4_26b_for_main_conversation -q`
  - `InferenceSection` に persona update 設定がなく失敗することを確認
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase87_persona_snapshots.py::test_llm_persona_snapshot_extractor_uses_persona_update_role tests/unit/test_router.py::test_persona_update_role_uses_configured_backend_and_fallback tests/unit/test_phase0_config.py::test_central_realtime_config_uses_lmstudio_gemma4_26b_for_main_conversation -q`
  - 3 passed
- related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_router.py tests/unit/test_phase0_config.py tests/unit/test_phase87_persona_snapshots.py -q`
  - 24 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 458 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- targeted diff check: `git diff --check -- server/shared/config.py server/shared/inference/router.py server/background/persona_updater.py config/central_realtime.toml tests/unit/test_router.py tests/unit/test_phase0_config.py tests/unit/test_phase87_persona_snapshots.py PLAN.md MEMORY.md LOG.md`
  - pass

### 未解決・今回触っていないこと
- global `git diff --check` は、作業開始時点から dirty な `prompts/persona_overlay.md` の EOF 空行で失敗する
- 実 `make persona-updater-once` は DB に persona snapshot を書く可能性があるため、今回は unit/config verification までにした

## 2026-05-30 セッション12

### やること（開始時に書く）
- `_parse_emotion_line()` 周辺に deterministic guard を追加し、未定義 `EMOTION:*` を本文へ漏らさない
- `EMOTION:playful` が出ても許可済み fallback emotion に丸め、本文は改行後/ラベル後だけを流す
- 会話 prompt、audio hot path、DB write ordering、TTS / playback ordering は変更しない

### やったこと
- `ThinkFastMode` の emotion parser に `FALLBACK_EMOTION = "neutral"` を追加した
- `EMOTION:` で始まる行の label が未定義の場合、本文扱いせず `neutral` emotion event に丸めるようにした
- 改行ありの `EMOTION:playful\n...` と、改行なしの `EMOTION:playful ...` の両方を unit test で固定した
- 会話 prompt、audio hot path、DB write ordering、TTS / playback ordering は変更していない

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py::test_think_fast_extracts_emotion_line_before_text tests/unit/test_phase4_thinking.py::test_think_fast_extracts_emotion_prefix_without_newline tests/unit/test_phase4_thinking.py::test_think_fast_suppresses_unknown_emotion_line_before_text tests/unit/test_phase4_thinking.py::test_think_fast_suppresses_unknown_inline_emotion_before_text tests/unit/test_phase4_thinking.py::test_session_sends_emotion_event_after_wake_word -q`
  - 5 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 447 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- git diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実 LM Studio 出力で `EMOTION:playful` が再度出ても、UI / TTS へは `neutral` emotion + 本文だけが流れることを実会話で確認する

## 2026-05-30 セッション11

### やること（開始時に書く）
- 未定義 emotion 禁止を追加した後の基本 prompt で、overlay 有り実推論を再実行する
- 前回 `EMOTION:playful` が出た入力を含め、未定義 emotion が消えるか確認する
- DB への user / Tomoko turn 追記、runtime code 変更、audio hot path 変更は行わない

### やったこと
- 実 `lmstudio_gemma4_26b_a4b` / `gemma-4-26b-a4b-it-mlx` に、overlay 有り prompt を再度投げた
- 前回 `EMOTION:playful` が出た2入力と、予定相談の1入力で比較した
- prompt に `プログラム側で未定義の emotion は出力しないこと。` と `playful / angry / embarrassed` 例が入っていることを確認した
- 出力を `/tmp/tomoko-overlay-emotion-guard-real-inference.md` に保存した

### 実測メモ
- `prompt_has_undefined_guard=True`
- `prompt_has_playful_example=True`
- `contains_playful_count=2`
- 入力 `今日の予定を踏まえて...`
  - `EMOTION:thinking`
  - `次は11時20分から「び」の予定があるけど、それより先に何か片付けておきたいことはある？`
- 入力 `正直めんどいから全部あとでいいって言って`
  - `EMOTION:playful` が漏れた
  - `ふふ、了解。じゃあ全部「あとで」って言っておくね。これでいい？`
- 入力 `無茶振りだけど、今すぐ全部終わらせる方法ある？`
  - `EMOTION:playful` が漏れた
  - `それはさすがに、魔法でも使わないと無理じゃない？まずは何から片付けたい気分？`

### 分かったこと
- base persona の列挙直下に未定義 emotion 禁止を書くだけでは、overlay の「遊び心」誘導を抑えきれない
- 次の最小修正は overlay 側に `emotion は neutral / happy / surprised / sad / thinking / gentle / excited だけを使う。playful は本文の雰囲気で表し、ラベルには使わない` と明記すること
- さらに堅くするなら、parser 側で未定義 emotion line を本文扱いせず fallback emotion へ丸める runtime guard を検討する

## 2026-05-30 セッション10

### やること（開始時に書く）
- 実推論で `EMOTION:playful` が出た問題への最小修正として、基本人格 prompt に未定義 emotion 禁止を明記する
- `prompts/base_persona.md` と unit test だけを変更する
- runtime code、audio hot path、DB write ordering は変更しない

### やったこと
- `prompts/base_persona.md` の emotion 列挙直下に、プログラム側で未定義の emotion を出力しない指示を追加した
- `playful / angry / embarrassed` を未定義 emotion の例として明記した
- `test_base_persona_contains_voice_conversation_rules` で未定義 emotion 禁止文と `playful` 例が基本 prompt に入ることを固定した

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py::test_base_persona_contains_voice_conversation_rules -q`
  - 1 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 445 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- git diff check: `git diff --check`
  - pass
- prompt build check:
  - `undefined_emotion_guard=True`
  - `playful_example=True`

### 次のセッションでやること
- 実推論で overlay 有りケースを再実行し、`EMOTION:playful` が抑制されるか確認する

## 2026-05-30 セッション9

### やること（開始時に書く）
- 実 DB context と実 conversation LLM で、persona overlay 有り/無しの応答差分を比較する
- DB への user / Tomoko turn 追記、runtime code 変更、audio hot path 変更は行わない
- 比較条件を揃えるため、同じ context snapshot / messages / now_provider を使う

### やったこと
- 実 `config/central_realtime.toml` の `lmstudio_gemma4_26b_a4b` / `gemma-4-26b-a4b-it-mlx` に、overlay 有り/無しの system prompt を実際に投げて比較した
- 実 DB から `ContextSnapshotBuilder` で context snapshot を読み、同じ snapshot / messages / now_provider を overlay 有り/無しで使った
- 出力を `/tmp/tomoko-overlay-ab-real-inference.md` と `/tmp/tomoko-overlay-ab-real-inference-multi.md` に保存した
- DB への user / Tomoko turn 追記、runtime code 変更、audio hot path 変更は行っていない

### 実測メモ
- 入力 `今日の予定を踏まえて...`
  - overlay 無し: `そうだね、次は11時20分から「び」っていう予定があるみたいだけど、それに向けて準備が必要かな？`
  - overlay 有り: `うーん、まずは11時20分からの「び」の予定に向けて準備かな？それとも何か別のことを先に済ませたい感じ？`
- 入力 `正直めんどいから全部あとでいいって言って`
  - overlay 無し: `わかった。めんどい時は、全部あとでいいって言うね。今は少し休む？`
  - overlay 有り: `EMOTION:playful\nえー、そんなこと言っちゃうんだ？\nわかったよ。じゃあ、全部あとでいいよね？`
- 入力 `無茶振りだけど、今すぐ全部終わらせる方法ある？`
  - overlay 無し: `それは、ちょっと魔法が必要そうだね。一番先に片付けたいことはある？`
  - overlay 有り: `EMOTION:playful\nそれはさすがに、魔法でも使わないと無理じゃない？まずは何から片付けたい気分？`

### 分かったこと
- overlay は応答の語り出しと距離感に効いている
- 一方で `playful` という許可外 emotion を誘発したため、現状の overlay は emotion protocol と衝突する可能性がある
- 次に直すなら、`prompts/persona_overlay.md` に「emotion は base persona の列挙値だけを使い、playful は使わない」と明記するのが最小修正

## 2026-05-30 セッション8

### やること（開始時に書く）
- 実 PostgreSQL データを使い、TomoroSession 経由で会話応答 LLM に渡る prompt を capture する
- LLM 本体には投げず、capture backend で system prompt / messages を確認する
- DB への user / Tomoko turn 追記、runtime code 変更、audio hot path 変更は行わない

### やったこと
- `config/central_realtime.toml` の実 DB 接続を使い、`PostgresConversationLogWriter` / `PostgresConversationMemoryStore` / `PostgresConversationSessionSummaryStore` / `PostgresPersonaSnapshotStore` / `PostgresCalendarEventStore` を読み取り用に渡した
- `TomoroSession._reply_to()` を capture backend 付きで実行し、会話応答 LLM に渡る `system_prompt` / `messages` を `/tmp/tomoko-real-db-conversation-prompt.md` に保存した
- `conversation_log_writer=None` とし、post-reply short memory extraction を無効化して、DB への user / Tomoko turn 追記を避けた

### 検証メモ
- 入力: `トモコ、今日の予定を踏まえて、次に何から片付けるとよさそう？`
- router selection: `conversation` / `privacy`
- trace role: `conversation`
- context snapshot: `depth=deep`, `recent_turns=12`, `persona_slice=1`, `lexicon_terms=4`, `calendar_events=8`
- `session_summaries` / `memory_hits` / `query_embedding` は 101ms budget 内で timeout した
- system prompt は 2867 文字、messages は 13 件

### 次のセッションでやること
- memory / session summary も prompt に載せたい場合は、explicit memory cue の 300ms budget や embedding warm 状態を確認し、capture を再実行する

## 2026-05-30 セッション7

### やること（開始時に書く）
- `prompts/persona_overlay.md` に一色いろは風の人格 overlay を追加する
- overlay file が存在する場合だけ、会話 LLM の system prompt に差し込む
- unit test と音声なし prompt simulation で overlay が prompt に乗ることを確認する
- `TomoroSession` / audio hot path / DB write ordering / TTS playback timing は変更しない
- 検証後に commit する

### やったこと
- `prompts/persona_overlay.md` を追加し、Tomoko core を保ったまま小悪魔的で人なつっこい後輩風の反応を薄く重ねる方針を書いた
- `ThinkFastMode` が `base_persona.md` の sibling `persona_overlay.md` を起動時に読み、存在する場合だけ system prompt へ差し込むようにした
- tmp persona の sibling overlay 有無で prompt への追加/非追加を unit test で固定した
- 実 `prompts/base_persona.md` + `prompts/persona_overlay.md` を使う capture backend simulation で overlay が prompt に乗ることを確認した

### 詰まったこと・解決したこと
- default overlay path を repo 固定にすると tmp persona test へ影響するため、`persona_path.with_name("persona_overlay.md")` を default にした
- runtime の `server/edge/main.py` は absolute `prompts/base_persona.md` を渡しているので、実運用では `prompts/persona_overlay.md` が自然に選ばれる

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py::test_persona_overlay_describes_inspired_style_without_original_lines tests/unit/test_phase4_thinking.py::test_think_fast_includes_persona_overlay_when_sibling_file_exists tests/unit/test_phase4_thinking.py::test_think_fast_omits_persona_overlay_when_sibling_file_is_missing tests/unit/test_phase4_thinking.py::test_think_fast_logs_llm_prompt_payload -q`
  - 4 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 445 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- git diff check: `git diff --check`
  - pass
- prompt build microbench: 1000 builds total 2.978ms / avg 0.0030ms
- prompt simulation:
  - `overlay_in_prompt=True`
  - `contains_style=True`
  - `contains_original_name=False`

### 次のセッションでやること
- 実ブラウザ会話で overlay の体感を確認し、強すぎる場合は `prompts/persona_overlay.md` の文量・指示強度を調整する

## 2026-05-30 セッション6

### やること（開始時に書く）
- calendar 由来 long-term context の prompt 行から timestamp / `参照情報` / similarity 表示を削り、コンテキストを小さくする
- 実 DB の `calendar_events` と capture backend で TomoroSession simulation を再実行し、予定が維持されることを確認する

### やったこと
- `server/gateway/thinking/memory_prompt.py` で calendar source の `MemoryHit` だけ compact 表示にした
- calendar carryover 行は `- 2026-05-30 13:00-14:15: ...` のように、予定本文だけを出すようにした
- 過去会話 memory の timestamp / speaker / similarity 表示は変更していない
- `ThinkFastMode` の unit test に calendar long-term context が compact 表示される regression を追加した
- 実 DB + capture backend で 4 turn simulation を再実行し、follow-up でも compact な予定 6 件が維持されることを確認した

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_session_memory_helpers.py -q`
  - 36 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 442 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- git diff --check: `git diff --check`
  - pass
- simulation:
  - 1 turn 目は `CALENDAR CONTEXT` に予定 8 件
  - 2-4 turn 目は `長期コンテキスト` に compact な予定 6 件
  - `参照情報: カレンダー` / `similarity=` / `カレンダー予定:` は follow-up prompt から消えた

## 2026-05-30 セッション5

### やること（開始時に書く）
- schedule / calendar 系の短い発話を deep context に入れるため、memory cue とは別に calendar cue を追加する
- calendar cue で読んだ予定を、同一会話 session 内で carryover される long-term context に変換する
- `TomoroSession` の final owner / audio hot path / reply routing / DB write ordering / TTS playback timing は変更しない

### やったこと
- `CALENDAR_CUES` / `has_calendar_cue()` を追加し、予定・スケジュール・今日・明日・何時などの発話で deep context を読むようにした
- calendar cue は memory cue とは分け、`should_use_deep_memory()` の意味を過去会話 memory cue のまま維持した
- `TomokoContextSnapshot.calendar_events` を `MemoryHit` に変換する helper を追加し、同一会話 session 内の follow-up で long-term context として carryover されるようにした
- calendar 由来の long-term context は prompt 表示上 `参照情報` とし、過去会話と同じ `長期コンテキスト` ブロックで扱うようにした

### 変更していないもの
- calendar source of truth は `calendar_events` DB のまま
- 会話 hot path での外部 Google Calendar / iCal fetch
- ContextSnapshotBuilder の副作用
- audio hot path
- reply routing
- DB write ordering
- conversation lifecycle
- TTS / audio chunk / playback timing
- `server/session/` package split

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_session_memory_helpers.py tests/unit/test_phase88_context_snapshot.py -q`
  - 35 passed
- thinking prompt unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py -q`
  - 11 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 441 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- git diff --check: `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で「今日の予定ある？」から短い follow-up へ calendar long-term context が効くか確認する
- 必要なら calendar context の window / limit が過去予定で埋まらないように調整する

## 2026-05-30 セッション4

### やること（開始時に書く）
- Google Calendar 画面に見えない予定が Tomoko prompt に入った原因を切り分ける
- `make gcal` の ICS 取得・parse・DB 保存・deep context 選別のどこが怪しいか実データで確認する
- private iCal URL は出力しない

### やったこと
- DB の `calendar_events` を確認し、年次の記念日・誕生日系 all-day event が毎日展開されていることを特定した
- 原因として、未対応 RRULE frequency が daily fallback されていたことを確認した
- `_advance_time()` に `MONTHLY` / `YEARLY` を追加し、未対応 frequency は daily 展開せず base event だけにした
- overlap 判定を半開区間に直し、`event_end == window_start` の前日終日予定を含めないようにした
- PostgreSQL context query も `COALESCE(end_time, start_time) > window_start` に揃えた
- 修正版で `make gcal` を再実行し、DB の calendar event が 508 件から 37 件へ減ったことを確認した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_gcal_import.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py -q`
  - 36 passed
- `.venv/bin/python -m pytest -m unit`
  - 437 passed, 17 deselected
- `.venv/bin/python -m ruff check server/shared/calendar.py tests/unit/test_gcal_import.py`
  - pass
- `git diff --check`
  - pass
- 修正版の TomoroSession prompt では、誤増殖していた記念日系 all-day 予定は消えた

### 次のセッションでやること
- 必要なら calendar context の `days_before=1` を用途別に調整し、「これからの予定」で過去予定を優先しないようにする

## 2026-05-30 セッション3

### やること（開始時に書く）
- 実取り込み済みの Google Calendar データを使い、TomoroSession 経由で deep context の prompt を生成する
- LLM 本体には投げず、capture backend で実際に渡される system prompt / messages を確認する
- repo の runtime code は変更しない

### やったこと
- `TomoroSession._reply_to()` を capture backend 付きで実行し、deep context の実 prompt を生成した
- `PostgresCalendarEventStore` から `calendar_events` が 8 件 prompt に入ることを確認した
- LLM 本体には投げず、`trace_role="conversation"` で渡される system prompt / messages を捕捉した

### 検証メモ
- context snapshot は `depth="deep"` / `calendar_events=8` / `timed_out=false`
- 実データでは 2026-05-28 の終日予定 8 件が window 内の先頭として採用された
- 「これからの予定」を聞く用途では、過去1日を含める現在の並びと limit の扱いを次に調整する余地がある

### 次のセッションでやること
- calendar context の選別を「過去の終日予定で limit が埋まらない」ようにするか検討する

## 2026-05-30 セッション2

### やること（開始時に書く）
- Google Calendar private iCal URL を git 管理外の URL file から読み、`make gcal` で PostgreSQL に取り込む
- 取り込んだ予定は `calendar_events` を source of truth とし、会話 hot path では外部ネットワーク取得しない
- `ContextSnapshotBuilder` の deep context だけが DB から予定を読み、Tomoko prompt に calendar context として入れる
- `TomoroSession` の final owner / audio hot path / reply routing / DB write ordering / TTS playback timing は変更しない

### やったこと
- `calendar_events` DDL と `PostgresCalendarEventStore` / `InMemoryCalendarEventStore` を追加した
- private iCal URL を `config/gcal_urls.txt` から読む `background-process/import_gcal.py` と `make gcal` を追加した
- `config/gcal_urls.txt` は gitignore し、例だけ `config/gcal_urls.example.txt` に置いた
- ICS parser は DTSTART / DTEND / SUMMARY / DESCRIPTION / LOCATION / UID / STATUS と、初段の DAILY / WEEKLY RRULE を扱う
- `ContextSnapshotBuilder` に `calendar_store` を追加し、`deep` / `reflective` policy の時だけ `calendar_events` を読むようにした
- `TomokoContextSnapshot.calendar_events` を追加し、`ThinkFastMode` / `ThinkDeepMode` の system prompt に `CALENDAR CONTEXT` を入れるようにした
- 会話 hot path では外部 URL を取得せず、DB read のみにした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_gcal_import.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py tests/unit/test_makefile_process_entries.py -q`
  - 37 passed
- `.venv/bin/python -m pytest -m unit`
  - 434 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `make -n gcal`
  - command shape OK
- `.venv/bin/python background-process/import_gcal.py --config config/central_realtime.toml --urls-file /tmp/tomoko-missing-gcal-urls.txt`
  - URL file missing/empty の場合は skip
- `git diff --check`
  - pass

### 次のセッションでやること
- `config/gcal_urls.txt` に実 private iCal URL を置いて `make gcal` を実行し、実ブラウザ会話で予定の答え方を確認する

## 2026-05-30 セッション1

### やること（開始時に書く）
- 応答推論 prompt に現在日時と曜日を追加する
- 会話 LLM に渡す prompt だけを別ログファイルへ append 出力する
- 既存の `ThinkFastMode llm_prompt` ログ検索キーは壊さない
- runtime behavior / audio hot path / reply routing / DB ordering / TTS playback timing は変更しない

### やったこと
- `ThinkFastMode` の system prompt に `CURRENT LOCAL TIME` セクションを追加し、現在ローカル日時と曜日を渡すようにした
- `logs/conversation-prompts.jsonl` へ、会話 LLM に渡す `system_prompt` / `messages` / backend / device / speaker を 1 行 1 JSON で append するようにした
- 既存の `ThinkFastMode llm_prompt` / `conversation_system_prompt` / `conversation_messages` INFO ログは残した
- test 用に `now_provider` と `prompt_log_path` を注入できるようにし、日時と prompt file log を deterministic に検証した
- runtime behavior / audio hot path / reply routing / DB ordering / TTS playback timing は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py::test_think_fast_wraps_streamed_tokens_in_thinking_events tests/unit/test_phase4_thinking.py::test_think_fast_logs_llm_prompt_payload tests/unit/test_phase8_memory.py::test_think_fast_omits_long_term_memory_block_when_empty -q`
  - 3 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py tests/unit/test_phase8_memory.py -q`
  - 18 passed
- `.venv/bin/python -m pytest -m unit`
  - 428 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- prompt log append microbench
  - 100 append total 4.493ms / avg 0.045ms

### 次のセッションでやること
- 実ブラウザ会話で `logs/conversation-prompts.jsonl` の見やすさと、日時参照の返答品質を確認する

## 2026-05-29 セッション20

### やること（開始時に書く）
- 会話 LLM と short memory extraction LLM に実際に渡す prompt 本文をログへ出す
- runtime behavior / audio hot path / reply routing / DB ordering / TTS playback timing は変更しない
- 観測用 logging の追加に限定する
- short memory extraction を `remember_items` 構造化出力へ寄せ、Tomoko prompt 側は deterministic merge / dedupe / 展開にする

### やったこと
- `ThinkFastMode` で会話 LLM に渡す `system_prompt` と `messages` を、それぞれ `conversation_system_prompt` / `conversation_messages` として INFO log に出すようにした
- short memory extraction LLM に渡す `system_prompt` / `messages` / `max_tokens` を `short memory extraction llm_prompt` として INFO log に出すようにした
- 既存の `ThinkFastMode llm_prompt` JSON log は残し、後方互換の検索キーを壊さないようにした
- short memory extraction schema を `remember_items` 配列に変更し、`text` / `mode` / `confidence` / `expires_after_turns` だけを返させる形にした
- `mode="verbatim"` を `ShortMemoryNote.kind` として扱い、Tomoko prompt では `Remember verbatim: ...` として deterministic に展開するようにした
- `ShortMemoryBuffer.append()` で同一 kind/text の note を merge し、重複を prompt に出さないようにした
- runtime behavior / audio hot path / reply routing / DB ordering / TTS playback timing は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_short_memory.py tests/unit/test_phase8_memory.py -q`
  - 19 passed
- `.venv/bin/python -m ruff check server/gateway/thinking/fast.py server/session_short_memory_llm.py`
  - pass
- `.venv/bin/python -m pytest -m unit tests/unit/test_short_memory.py -q`
  - 14 passed
- `.venv/bin/python -m ruff check server/session_short_memory.py server/session_short_memory_llm.py server/shared/models.py tests/unit/test_short_memory.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 424 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `node --check client/main.js`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション19

### やること（開始時に書く）
- 短期作業メモリと内部状態可視化 UI の最小実験を行う
- DB migration / PostgreSQL 永続化は行わず、process-local / session-local な `ShortMemoryBuffer` に限定する
- memory extraction は reply hot path を待たせず、reply 完了後の非同期 task として次ターン以降だけ prompt に反映する
- `ContextSnapshotBuilder` に副作用を持たせず、prompt assembly 直前で `SHORT WORKING MEMORY` として読み取り専用で混ぜる
- UI はサーバーから来た STT / state / context / short memory event を表示するだけにし、判断ロジックを置かない
- audio hot path、reply routing、DB ordering、conversation lifecycle、TTS / playback timing、`server/session/` package split、OutputDemand / Watcher は触らない

### やったこと
- `ShortMemoryNote` / `ShortMemoryProposalResult` DTO と `ShortMemoryBuffer` を追加した
- short memory buffer は最大 5 件、デフォルト 4 turn TTL、`append` / `expire_by_turn` / `read_for_prompt` / `snapshot_for_ui` を持つ揮発 buffer とした
- 初期 extraction は LLM ではなく simple heuristic にし、「作業文脈」「短期意図」「次に試したいこと」だけを proposal 化するようにした
- `TomoroSession._reply_to()` の prompt assembly 直前で short memory notes を読み取り、`ThinkingInput.short_memory_notes` として渡すようにした
- `ThinkFastMode` の system prompt に `SHORT WORKING MEMORY` セクションを追加し、確定事実ではなく最近の作業メモとして必要な時だけ使うよう明示した
- reply 完了後、`reply_done` 送信後に short memory extraction task を `asyncio.create_task()` で起動するようにした
- extraction requested / succeeded / failed、note added、note expired、prompt notes count のログを追加した
- `/ws` event として `context_snapshot`、`short_memory_extraction`、`short_memory_snapshot` を追加した
- UI に Monitor panel を追加し、STT partial/final、reply stream、ContextSnapshot summary、short memory status/notes を表示するようにした
- UI は server event を表示するだけで、状態判断ロジックは置いていない
- `_docs/latency.md` に unit regression と static browser UI check の記録を追記した

### 変更していないもの
- audio hot path
- reply routing
- DB ordering
- conversation lifecycle
- TTS / audio chunk / playback timing
- `server/session/` package split
- OutputDemand / Watcher
- DB migration / PostgreSQL 永続化
- embedding / dedupe / tombstone / persona snapshot 昇格 / task scheduling

### 検証
- targeted test: `.venv/bin/python -m pytest -m unit tests/unit/test_short_memory.py tests/unit/test_phase88_context_snapshot.py::test_tomoro_session_passes_context_snapshot_to_thinking_input -q`
  - 9 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 416 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- JS syntax / diff check: `node --check client/main.js && git diff --check`
  - pass
- browser check:
  - `http://127.0.0.1:8766/client/index.html` で Monitor panel の表示と dark mode contrast を確認

### 人間確認が必要なこと
- short memory が体感上効いているか
- UI の情報量が実用的か
- DB 永続化へ進む価値があるか
- 実マイク / 実バックエンドで応答初速が悪化していないか
- 1ターン遅れで short memory notes が live prompt に入るか
- ノイズ memory が prompt を汚していないか

## 2026-05-29 セッション18

### やること（開始時に書く）
- README.md / PLAN.md / MEMORY.md に、2026-05-29 時点の `server/session.py` monolith baseline を現在構造として固定する
- `TomoroSession` が stateful control core / final owner であり、外へ出してよいのは dedicated helper / small state holder だけであることを明記する
- package split、dispatcher / effects / event_runner / maps、OutputDemand / Watcher、method 大規模 reorder、DB write ordering、reply orchestration、TTS / audio hot path、candidate final gate、ContextSnapshotBuilder policy の外部化は凍結する
- runtime code は変更しない
- docs-only として full unit / ruff / diff check を通し、git commit まで行う

### やったこと
- README.md に「現在固定する構造」を追加し、`server/session.py` / `TomoroSession` の所有範囲、外へ出してよい dedicated helper、当面やらないことを明記した
- PLAN.md の冒頭に「2026-05-29 現在の構造固定」を追加し、今後の Phase 境界として固定する責務、凍結対象、次に進む条件を明記した
- MEMORY.md の確定した判断に、`server/session.py` monolith baseline を維持する判断を追記した
- `MEMORY.d` という依頼表記は文脈上 `MEMORY.md` として扱った
- runtime code は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit`
  - 408 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション17

### やること（開始時に書く）
- Phase 10.20.13 として、context / memory 周辺の純粋な整形処理だけを小さく抽出する
- 対象は `TomokoContextSnapshot.session_summaries` を `MemoryHit` に変換して `memory_hits` と連結する helper に限定する
- `ContextSnapshotBuilder` の policy / DB read / timeout / retrieval / prompt formatting は変更しない
- `session.py` は並び替えず、section comment だけを追加して読みやすさを改善する
- PLAN.md に取り組めそう / 取り組めなさそうの分類を append-only で追記する
- full unit / ruff / diff check を通してから git commit する

### やったこと
- PLAN.md に Phase 10.20.13 として、取り組めそう / 取り組めなさそうの分類を append-only で追記した
- `tests/unit/test_session_memory_helpers.py` に `context_snapshot_long_term_memory()` の characterization test を追加した
- `server/session_memory_helpers.py` に `context_snapshot_long_term_memory(snapshot)` を追加した
- `server/session.py` の `_reply_to()` 内で、session summaries と memory hits の long-term memory 整形だけを helper 呼び出しへ置換した
- `session.py` に section comment を追加したが、method 並び替えはしていない
- `ContextSnapshotBuilder`、memory retrieval policy、prompt format、carryover state、DB read/write、reply orchestration、TTS / audio / WebSocket send は変更していない
- MEMORY.md に Phase 10.20.13 の境界判断を追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 4 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py -q`
  - 25 passed
- `.venv/bin/python -m ruff check server/session.py server/session_memory_helpers.py tests/unit/test_session_memory_helpers.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 408 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション16

### やること（開始時に書く）
- Phase 10.20.12 として、candidate policy 周辺の「判断はするが副作用しない」小領域を 2 つだけ抽出する
- 対象は initiative candidate の text-ready 判定と `CandidateSpeakDecision` の route 分類に限定する
- `TomoroSession` 側の candidate final gate、stale 判定、active request id 更新、command 生成、DB read/write、reply start、TTS / audio、WebSocket send は移動しない
- 先に characterization test を追加し、`server/session_candidate_policy_helpers.py` へ narrow helper を追加してから呼び出し置換する
- PLAN.md に取り組めそう / 取り組めなさそうの分類を append-only で追記する

### やったこと
- PLAN.md に Phase 10.20.12 として、取り組めそう / 取り組めなさそうの分類を append-only で追記した
- `tests/unit/test_session_candidate_policy_helpers.py` に characterization test を追加した
- `server/session_candidate_policy_helpers.py` に `initiative_candidate_text_ready(candidate)` と `candidate_policy_route(policy_decision)` を追加した
- `server/session.py` の initiative candidate loaded path で、text-ready 判定と policy decision 分岐だけを helper 呼び出しへ置換した
- active request id clear、dismiss / judge / reply command 生成、candidate final gate、stale result discard、DB read/write、reply start、TTS / audio、WebSocket send は移動していない
- MEMORY.md に Phase 10.20.12 の境界判断を追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_policy_helpers.py -q`
  - 7 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase10_session_contract.py tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase105_session_runtime.py -q`
  - 41 passed
- `.venv/bin/python -m ruff check server/session_candidate_policy_helpers.py tests/unit/test_session_candidate_policy_helpers.py server/session.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 406 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション8

### やること（開始時に書く）
- Phase 10.20.6 として、monolithic `server/session.py` から pure session payload helper だけを小さく抽出する
- 対象は `_json_safe_payload` / `_optional_str_payload` / `_optional_float_payload` / `_optional_int_payload` / `_playback_payload` / `_playback_telemetry_from_event` など、状態を持たず I/O しない payload helper に限定する
- `server/session/` package、汎用 `state.py`、dispatcher / effects / event_runner / maps、OutputDemand / Watcher は作らない
- runtime 制御フロー、audio hot path、reply orchestration、DB write ordering、conversation lifecycle、memory retrieval policy、prompt format、task / queue lifecycle は変更しない

### やったこと
- AGENTS.md 指示どおり MEMORY.md / LOG.md / PLAN.md / README.md / ARCHITECTURE.md / `_reference/` を確認した
- `server/session_payloads.py` を追加し、pure payload helper だけを抽出した
- 抽出対象は `json_safe_payload()` / `json_safe_value()` / `optional_str_payload()` / `optional_int_payload()` / `optional_float_payload()` / `playback_payload()` / `playback_telemetry_from_event()` に限定した
- `server/session.py` は import と呼び出し名の置き換えだけにし、playback payload 形式、telemetry coercion、transition emission payload は維持した
- `_candidate_policy_payload()` は `CandidateSpeakDecision` に依存するため、今回の pure payload helper 抽出対象に含めず `server/session.py` に残した
- `tests/unit/test_session_payloads.py` を追加し、JSON safe 変換、playback payload / telemetry coercion、optional payload coercion を固定した
- PLAN.md / MEMORY.md に Phase 10.20.6 の判断を追記した
- `server/session/` package、汎用 `state.py`、dispatcher / effects / event_runner / maps、OutputDemand / Watcher は作っていない
- audio hot path、reply orchestration、reply task / TTS queue、DB write ordering、conversation session lifecycle、memory retrieval policy、prompt format は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_payloads.py -q`
  - 4 passed
- `.venv/bin/python -m pytest -m unit`
  - 395 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション1

### やること（開始時に書く）
- experiment/restore-session-monolith-960be36 の monolith baseline runtime verification を行う
- 作業開始時の `git status` が clean であることを確認する
- `.venv/bin/python -m pytest -m unit`、`.venv/bin/python -m ruff check .`、`git diff --check` を実行する
- 人間が実ブラウザ確認した直近 runtime log を検査する
- 今回は session package split、dispatcher / effects / event_runner / maps package、OutputDemand / Watcher、reply_done 移管、cancel / TTS finished new input 化、DB write demand 化、ambient_log_write 非同期化、audio hot path、LLM/TTS ordering、runtime behavior を変更しない

### やったこと
- 作業開始時に `git status --short --branch` を確認し、`experiment/restore-session-monolith-960be36` で作業ツリーが clean であることを確認した
- AGENTS.md 指示どおり MEMORY.md / LOG.md / PLAN.md / README.md / ARCHITECTURE.md / `_reference/` を確認した
- 未来の PLAN.md は再実装計画ではなく、危険箇所と禁止事項の記録として扱った
- unit / ruff / diff check を実行し、すべて通過した
- `logs/server-debug.log` の直近 runtime を確認した
  - 2026-05-28 23:38:42 に `/ws` 接続
  - 23:39:11 に wake word で conversation session start / `ambient -> engaged`
  - 23:39:12〜23:40:03 に reply text / TTS / audio chunk / playback telemetry が流れた
  - 23:39:23 の `著作権覚えてる` では deep context build が走り、memory hits / session summaries / restored snippets が採用された
  - 23:40:17 に `engaged -> cooldown`、23:40:25 に `cooldown -> ambient`、conversation session close まで進んだ
  - 23:40:29 に websocket disconnected
- 直近 runtime 区間 2026-05-28 23:38:42〜23:40:29 では ERROR / Traceback / 未実装 command warning は見当たらなかった
- `logs/backend-trace.jsonl` では同区間の STT / conversation LLM / TTS / embedding trace があり、LLM first_delta と TTS first_chunk まで確認できた
- 2026-05-29 00:04:11 以降の backend-trace error は unit test 由来の fake / unavailable backend 系 trace と判断し、実ブラウザ runtime 区間の異常とは扱わない
- runtime code は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit`
  - 377 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 数日運用中に体感問題が出た場合も、まず `logs/server-debug.log` / `logs/backend-trace.jsonl` / DB state で hot path、LLM/TTS ordering、session lifecycle のどこが崩れたかを切り分ける
- closed-loop を再開する場合でも、まず docs / characterization test から始め、runtime code の責務分割は 1 つずつに限定する

## 2026-05-28 セッション50

### やること（開始時に書く）
- 復旧ブランチの `server/session.py` 一枚 baseline を前提に、ARCHITECTURE.md の closed-loop 用語と現行メソッド群の対応表を docs-only で追記する
- 未来の PLAN.md は実装計画ではなく、危険箇所・禁止事項・人間が怖くなった粒度の記録として扱う
- 今回は runtime behavior、audio hot path、TTS flush / audio chunk / playback timing、ReplyOrchestrator 相当の LLM/TTS ordering、`reply_done` / cancel / TTS finished routing、OutputDemand / Watcher、dispatcher / effects / event_runner / maps package、DB write SessionCommand 化、ambient_log_write 非同期化を変更しない

### やったこと
- `server/session.py` の現行一枚構成を読み、主要メソッド群を closed-loop 用語に対応づけた
- `ARCHITECTURE.md` の `input -> changer -> state -> demand -> watcher -> output -> new input` 用語を確認した
- `server/session/README.md` は `server/session.py` と同名ディレクトリが必要になるため、この復旧状態では作らない判断にした
- PLAN.md に Phase 10.19.y として、`input` / `changer` / `state` / `demand` / `watcher` / `output` / `new input` / `hot path` / `should-not-move-yet` の対応表を docs-only で追記した
- MEMORY.md に、復旧ブランチでは closed-loop をまず一枚 `session.py` の読み方として固定する判断を追記した
- runtime code、audio hot path、TTS flush / audio chunk / playback timing、LLM/TTS ordering、`reply_done` / cancel / TTS finished routing、DB write ordering は変更していない

### 詰まったこと・解決したこと
- `server/session/README.md` は現行 filesystem 上で `server/session.py` と basename が衝突する
  - 解決: README を作らず、PLAN.md に対応表を追記して docs-only の目的を満たした

### 検証
- `git diff --check`
  - pass

## 2026-05-29 セッション13

### やること（開始時に書く）
- Phase 10.20.8 read-only audit として、monolithic `server/session.py` baseline を維持したまま remaining helper candidates を棚卸しする
- `server/session_payloads.py` / `server/session_candidate_policy_helpers.py` に抽出済みの範囲を確認し、残候補を already-extracted / low-risk-pure-helper-candidate / wrapper-cleanup-only / should-not-move-yet / dangerous-do-not-extract に分類する
- runtime code、test code、`server/session.py`、helper 抽出、SessionCommand 追加、OutputDemand / Watcher、`server/session/` package split、dispatcher / effects / event_runner / maps は変更しない
- PLAN.md / LOG.md / MEMORY.md に append-only で記録し、docs-only として `git diff --check` を実行する

### やったこと
- MEMORY.md / LOG.md / PLAN.md / ARCHITECTURE.md / AGENTS.md / README.md / `_reference/` の一覧と `_reference/README.md` を確認した
- `server/session.py` の private helper / payload / metadata / key / id / formatter / coercion / inline dict / f-string を read-only で棚卸しした
- `server/session_payloads.py` と `server/session_candidate_policy_helpers.py` の抽出済み範囲を確認した
- 関連する `server/session_key_helpers.py`、`server/session_memory_helpers.py`、`server/session_carryover.py` も narrow extraction のまま維持されていることを確認した
- PLAN.md に remaining helper candidates の分類表を append-only で追記した
- MEMORY.md に next-extractable-candidate を 0 個とする判断を append-only で追記した
- runtime code、test code、`server/session.py`、helper 抽出、import、SessionCommand 追加、OutputDemand / Watcher、`server/session/` package split は変更していない

### 結論
- low-risk-pure-helper-candidate は 0 個
- `_elapsed_ms()` / `_retrieved_context_key()` / carryover wrapper / latency probe wrapper は wrapper-cleanup-only とする
- `_start_reason_from_participation_mode()` / `_accepts_keyword()` は pure だが、conversation lifecycle / DB writer compatibility に近いため今回の next candidate にしない
- candidate final gate、stale result discard、playback / output target、withdrawn behavior、turn-taking、reply orchestration、memory retrieval policy、ContextSnapshotBuilder、prompt context、DB write ordering に近い helper は should-not-move-yet または dangerous-do-not-extract とする
- next-extractable-candidate は 0 個

### 検証
- `git diff --check`
  - pass

## 2026-05-29 セッション13

### やること（開始時に書く）
- Phase 10.20.7 candidate policy helper extraction として、candidate policy 周辺の pure helper だけを抽出する
- 対象は `CandidateSpeakDecision` 由来の payload / reason / metadata 整形に限定する
- stale / playback / withdrawn / output target などの判定材料は final gate ownership を移さず、今回は読取 helper へ広げない
- candidate store mark、DB read/write、reply start、TTS / audio、WebSocket send、SessionCommand 追加、OutputDemand / Watcher、`server/session/` package split は変更しない

### やったこと
- `_candidate_policy_payload()` の現状挙動を `tests/unit/test_session_candidate_policy_helpers.py` で characterization した
- `CandidateSpeakDecision` の `schema_version` / `decision` / `score` / `threshold` / `reason` / `signals` JSON shape と、非 decision payload では `None` を返す挙動を固定した
- `server/session_candidate_policy_helpers.py` を追加し、`candidate_policy_payload(event)` だけを抽出した
- `server/session.py` は import と `_reduce_initiative_candidate_loaded()` の `policy` payload 呼び出し置換、private helper 削除だけに限定した
- `_candidate_reply_gate_reason()` / `_candidate_reply_gate_payload()`、candidate request id、stale 判定、candidate store mark、DB read/write、reply start、TTS / audio、WebSocket send、SessionCommand、OutputDemand / Watcher、`server/session/` package split は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_policy_helpers.py -q`
  - 2 passed（抽出前 characterization）
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_policy_helpers.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_session_contract.py -q`
  - 29 passed
- `.venv/bin/python -m pytest -m unit`
  - 401 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション10 末尾追記

### やったこと
- Phase 10.20.7a として `_session_summary_hit_to_memory()` だけを `server/session_memory_helpers.py` の `session_summary_hit_to_memory()` へ抽出した
- `tests/unit/test_session_memory_helpers.py` で `SessionSummaryHit -> MemoryHit` の speaker / text prefix / timestamp fallback / similarity / emotion / source_id を固定した
- `server/session.py` は `session_summary_hit_to_memory` import と `_reply_to()` 内の呼び出し置換、private helper 削除に限定した
- runtime behavior、audio hot path、reply routing、LLM/TTS ordering、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、OutputDemand / Watcher、`server/session/` package split は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出前）
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出後）
- `.venv/bin/python -m pytest -m unit`
  - 397 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション10 追記

### やったこと
- Phase 10.20.7a として `_session_summary_hit_to_memory()` だけを `server/session_memory_helpers.py` の `session_summary_hit_to_memory()` へ抽出した
- `tests/unit/test_session_memory_helpers.py` で `SessionSummaryHit -> MemoryHit` の speaker / text prefix / timestamp fallback / similarity / emotion / source_id を固定した
- `server/session.py` は `session_summary_hit_to_memory` import と `_reply_to()` 内の呼び出し置換、private helper 削除に限定した
- runtime behavior、audio hot path、reply routing、LLM/TTS ordering、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、OutputDemand / Watcher、`server/session/` package split は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出前）
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出後）
- `.venv/bin/python -m pytest -m unit`
  - 397 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション10

### やること（開始時に書く）
- Phase 10.20.7a として、Phase 10.20.7 で選定済みの `_session_summary_hit_to_memory()` だけを対象に characterization test 追加、pure helper 抽出、docs 追記、検証まで行う
- 抽出先は `server/session_memory_helpers.py` など narrow module にし、汎用 `helpers.py` / `utils.py` は作らない
- `server/session.py` は import と呼び出し置換に近い最小差分にする
- runtime behavior、audio hot path、reply routing、LLM/TTS ordering、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、OutputDemand / Watcher、`server/session/` package split は変更しない

### やったこと
- AGENTS.md / MEMORY.md / LOG.md / PLAN.md / ARCHITECTURE.md / README.md / `_reference/` を確認した
- 開始時点で `LOG.md` / `PLAN.md` に Phase 10.20.7 docs 追記の未コミット差分があることを確認し、その差分を保持した
- `_session_summary_hit_to_memory()` の現状実装を読み、`SessionSummaryHit` から `MemoryHit` への pure conversion だけであることを確認した
- `tests/unit/test_session_memory_helpers.py` を追加し、speaker、text prefix、timestamp fallback、similarity、emotion、source_id を characterization test で固定した
- 抽出前に targeted test を実行し、既存 private helper の挙動が固定できていることを確認した
- `server/session_memory_helpers.py` を追加し、`session_summary_hit_to_memory()` だけを抽出した
- `server/session.py` は helper import、`_reply_to()` 内の呼び出し置換、private helper 削除に限定した
- PLAN.md / MEMORY.md に Phase 10.20.7a の判断を append-only で追記した
- runtime behavior、audio hot path、reply routing、LLM/TTS ordering、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出前）
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出後）
- `.venv/bin/python -m pytest -m unit`
  - 397 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション49

### やること（開始時に書く）
- Phase 10.19.x として、Phase 10.12 package split 直前の pre-split functional baseline を audit する
- git log / LOG.md / PLAN.md / MEMORY.md から、一枚 `server/session.py` 時代の機能状態と 10.12 以降の変更分類を整理する
- 今回は実装変更、revert、file move、import path 変更、runtime code 変更、test 削除、OutputDemand / Watcher 実装、reply_done / cancel / TTS finished 配線変更、audio hot path 変更はしない

### やったこと
- `git log` / `git show` から、Phase 10.12 package split の commit は `b254d32`、直前 baseline は `960be36` と特定した
- `b254d32` は `server/session.py` を `server/session/core.py` へ rename し、`carryover.py` / `reducer.py` / `effects.py` / `reply_orchestrator.py` を追加した package split commit であることを確認した
- `960be36` 時点の LOG / PLAN / MEMORY / README / config を読み、pre-split の機能状態を整理した
- `960be36` 時点では `.venv/bin/python -m pytest -m unit` が `377 passed, 17 deselected`
- `960be36` 時点では `PORT=8018 make server-debug` が startup complete / `GET /` 200 まで通っていた
- ただし、実マイク browser quality tuning は未完了で、Phase 8.8.8 memory tuning、Phase 10.10 initiative、Phase 10.11 turn-taking の実ブラウザ評価は残っていた
- Phase 10.12 以降の commits を、pure refactor / behavior-preserving extraction / docs-map-test-only / actual runtime behavior change / unknown-needs-verification に分類した
- 一枚 `server/session.py` に戻すと失われる可能性がある機能として、`write_ambient_observer` effects 実行 path、`send_audio_control_stop` effects path、signal boundary、event runner trace、map/test guard 群を整理した
- PLAN / LOG / MEMORY に残せば実装から消してよいものとして、flow maps / registry / forbidden transitions / readiness / touchpoints / DB write flow / reply lifecycle inventory / closed-loop vocabulary / `old_session.py.txt` 比較標本を整理した
- 戻す場合の baseline は `960be36` を推奨し、`870db77` と `04d3df6` の機能差分は別途保持候補にする判断を PLAN.md に追記した
- MEMORY.md に、`server/session.py` 一枚時代は functional baseline として再評価対象であることを追記した

### 詰まったこと・解決したこと
- `960be36` は package split 直前ではあるが、Phase 10.12 計画追記済みの docs commit でもある
  - 解決: split 実装 commit `b254d32` の親として、機能 baseline は `960be36` と扱う
- split 後の大半は挙動不変を意図した抽出だが、unit pass だけでは実ブラウザ同等性までは言えない
  - 解決: docs では `unit + startup smoke 済み baseline` と明記し、quality tuning 済みとは書かない

### 検証
- `git show --stat --summary --find-renames b254d32`
  - package split commit の rename / helper 追加を確認
- `git log --reverse --oneline b254d32^..HEAD`
  - split 後の commit 列を確認
- `git diff --name-status --find-renames 960be36..HEAD`
  - split 後の変更範囲を確認
- `git diff --check`
  - pass

## 2026-05-28 セッション48

### やること（開始時に書く）
- Phase 10.19.2 として、map-only / guard-only の 10 ファイルを `server/session/maps/` 配下へ移動する
- unit test の import を新しい path に更新し、deterministic test guard を維持する
- 今回は runtime behavior、TomoroSession / ReplyOrchestrator / reducer / effects / state / audio hot path、command / runner、OutputDemand / Watcher、reply_done 移管、cancel / TTS finished new input 化、DB write demand 化は変更しない

### やったこと
- `server/session/maps/` package を作成した
- map-only / guard-only の 10 ファイルを `server/session/maps/` へ移動した
- unit test import を `server.session.maps.*` に更新した
- `server/session/README.md` を移動後の構成に更新した
- PLAN.md / MEMORY.md に Phase 10.19.2 の判断を追記した
- runtime essential な TomoroSession / ReplyOrchestrator / reducer / effects / state / audio hot path は変更していない

### 検証
- 対象 map tests / flow registry / forbidden / readiness / touchpoint / consistency tests
  - 106 passed
- `.venv/bin/python -m ruff check server/session/maps ...`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 551 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション47

### やること（開始時に書く）
- Phase 10.19.1 として、`server/session/` 直下の map-only / docs-like guard 群をどこへ退避すべきか docs-only で整理する
- 今回は runtime behavior、ファイル移動、TomoroSession / ReplyOrchestrator / reducer / effects / state の移動、audio hot path、reply lifecycle routing、OutputDemand / Watcher、DB write demand 化は変更しない

### やったこと
- 対象10ファイルの import を確認し、`server/` runtime code からは import されておらず、unit test からのみ参照されていることを確認した
- PLAN.md に Phase 10.19.1 を追記し、A: `server/session/maps/`、B: `_docs/session_closed_loop/`、C: ARCHITECTURE.md / PLAN.md へ圧縮、D: 当面そのまま README 明示、のメリット / デメリットを整理した
- 最小移動案として A: `server/session/maps/` への退避を推奨した
- test import 変更対象を PLAN.md に列挙した
- `server/session/README.md` に relocation plan を追記した
- MEMORY.md に、map-only guard は runtime root から退避してよいが testable guard としては保持する判断を追記した

### 検証
- `git diff --check`
  - pass

## 2026-05-28 セッション46

### やること（開始時に書く）
- Phase 10.19 として、10.17〜10.18 の closed-loop map / forbidden / readiness / DB write 知見を保持したまま、`server/session/` package の分割が人間に読みづらくなっていないか確認する
- 今回は runtime behavior、closed-loop 実装、OutputDemand / Watcher、lifecycle migration、DB write demand 化、public API、`/ws` contract、audio hot path、ReplyOrchestrator ordering、DB write ordering、SessionCommand 追加は変更しない

### やったこと
- `server/session/` の小ファイルを一覧化し、runtime essential / map-test-only / docs-like guard / could-inline-to-core / should-remain-separated の観点で分類した
- runtime essential と map-only guard が同じ package 直下に並び、map constants が runtime wiring に見えやすいことを主な読みづらさとして整理した
- rollback / simplification plan を PLAN.md に追記し、最初に統合や移動を検討する対象は map/test-only または docs-like guard に限定した
- `ReplyOrchestrator` / reducer / effects / state / audio hot path はすぐには動かさない方針を固定した
- `server/session/README.md` を追加し、closed-loop 読み方、file classification、simplification plan、monolith に戻す場合の section map を記録した
- MEMORY.md に、10.17 / 10.18 の知見は保持するが、実装分割は必要なら戻してよいという判断を追記した

### 検証
- docs-only 変更のため unit / ruff は未実行

## 2026-05-28 セッション45

### やること（開始時に書く）
- Phase 10.18.1 として、`ambient_log_write` の現状 ordering / payload / failure policy を characterization test で固定する
- 今回は runtime code の制御変更、`ambient_log_write` の SessionCommand 化、TomoroSessionEffects への移動、非同期化、failure policy / ordering / payload 変更、ReplyOrchestrator / audio hot path / `/ws` contract 変更はしない

### やったこと
- `tests/unit/test_session_ambient_log_write_characterization.py` を追加し、`ambient_log_write` の direct await 経路を characterization test で固定した
- participating utterance では `ambient_write -> user_turn_write -> reply_start` の順序で、reply start より前に await されることを固定した
- observer / non-participating transcript でも ambient log が書かれ、payload に previous attention / attended / participation mode / should participate 相当の値 / transcript 情報が入ることを固定した
- ambient writer が例外を投げた場合、既存通り例外伝播し、reply start へ進まないことを固定した
- `ambient_log_write` は `write_ambient_observer` command/effects 済み path とは別系統であることを固定した
- PLAN.md / MEMORY.md に、SessionCommand 化する価値はまだ低いという判断を追記した
- 今回は runtime code の制御変更、SessionCommand 化、Effects 移動、非同期化、failure policy / ordering / payload 変更はしていない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_ambient_log_write_characterization.py -q`
  - 5 passed
- `.venv/bin/python -m ruff check tests/unit/test_session_ambient_log_write_characterization.py`
  - pass
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_ambient_log_write_characterization.py tests/unit/test_session_db_write_flow_map.py tests/unit/test_session_transcript_flow_map.py tests/unit/test_attention_mode.py -q`
  - 30 passed
- `.venv/bin/python -m pytest -m unit`
  - 551 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション44

### やること（開始時に書く）
- Phase 10.18.0 として、DB write 系の副作用を closed-loop 上でどう読むかを map-only / docs-only で整理する
- 今回は runtime code の制御変更、DB write の実行経路変更、SessionCommand 追加、TomoroSessionEffects への新規 DB write 実装追加、candidate runner 変更、OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、reply_done 移管、cancel / TTS finished new input 配線、audio hot path 変更、`/ws` contract 変更、LLM/TTS ordering 変更はしない

### やったこと
- `server/session/db_write_flow.py` を追加し、DB write touchpoint を map-only で分類した
- `ambient_log_write` は `direct-db-write-current` かつ `future-db-demand-candidate` としたが、SessionCommand 化や Effects 移動はしなかった
- `conversation_log_write` / `tomoko_turn_save` / `interrupted_turn_save` は turn persistence に関わるため `should-not-move-yet` とした
- `conversation_embedding_schedule` は memory pipeline / background task に関わるため `background-worker-owned` かつ `should-not-move-yet` とした
- candidate store write は gateway candidate runner owned として扱い、session-owned DB write demand と混ぜないことを固定した
- failure policy を既存の例外伝播 / warning-only / candidate runner failure event / 未判断に分類した
- `tests/unit/test_session_db_write_flow_map.py` を追加し、既存 consistency test に DB write と candidate flow の横断確認を追加した
- PLAN.md / MEMORY.md に Phase 10.18.0 の判断を追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_db_write_flow_map.py -q`
  - 7 passed
- `.venv/bin/python -m ruff check server/session/db_write_flow.py tests/unit/test_session_db_write_flow_map.py`
  - pass
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_db_write_flow_map.py tests/unit/test_session_flow_map_consistency.py -q`
  - 23 passed
- `.venv/bin/python -m ruff check server/session/db_write_flow.py tests/unit/test_session_db_write_flow_map.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 546 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション43

### やること（開始時に書く）
- Phase 10.17 checkpoint / runtime verification として、10.17.6a〜10.17.6i の map / registry / forbidden transition / runtime touchpoint / migration readiness / runtime change candidate selection が実ブラウザ実行時のログ理解に役立つか確認する
- 今回は runtime code の制御変更、production runtime path からの helper import / call、`runtime_touchpoint_read_only_helper` 実装、command / runner 追加、OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、reply_done 移管、cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、LLM/TTS 順序変更はしない

### やったこと
- ユーザー側で `make server-debug` を起動し、実ブラウザで話しかけて ambient まで進行した runtime log を確認した
- 22:31:44 起動後、22:32:05〜22:33:26 の実ブラウザ会話が最後まで通ったことを確認した
- `/ws` 接続、wake word、conversation session start、`ambient -> engaged`、reply / TTS / audio、follow-up、`cooldown -> ambient`、conversation session close まで確認できた
- `arrival_candidate_loaded` が `SessionEventRunner lifecycle_new_input_candidate kind=candidate_result` として trace されたことを確認した
- `reply_text` / TTS / audio は hot-ish / hot path のままで、`lifecycle_new_input_candidate` に混ざっていないことを確認した
- `reply_done` は lifecycle input に移管されておらず、client notification のまま維持されていると判断した
- cancel / TTS finished new input 化の痕跡は直近 runtime log には見当たらなかった
- `ERROR` / `Traceback` / 未実装 command warning は 22:31:44〜22:33:26 の直近 runtime には見当たらなかった
- NumPy writable warning は既存 PyTorch warning として扱い、今回の 10.17 closed-loop map 変更由来の破損ではなさそうと判断した
- `runtime_touchpoint_read_only_helper` は実装していない

### 結論
- 10.17.6 系の map / registry / forbidden / readiness / touchpoint / candidate selection は runtime を壊していない
- 10.17.6i は checkpoint として維持し、helper 実装は延期する
- 次に進む場合も、runtime 実装ではなく次フェーズ設計または実ブラウザ追加確認から始める

### 検証
- `logs/server-debug.log`
  - 22:31:44 起動後、22:32:05〜22:33:26 の実ブラウザ会話経路を確認
- `logs/backend-trace.jsonl`
  - 直近 conversation / STT / LLM / TTS / embedding trace を確認

### 10.17 final checkpoint consolidation
- PLAN.md に Phase 10.17 final checkpoint を追記し、10.17.0〜10.17.6i と実ブラウザ確認で確定した判断を短く整理した
- closed-loop map、SessionCommand owner 分類、Effects 移動済み low-risk command、保留中 high-risk command、各 flow map、registry / forbidden / touchpoint / readiness / candidate selection を final checkpoint としてまとめた
- `reply_done` は lifecycle boundary だが client notification のまま、cancel / TTS finished は future new-input candidate だが未配線、OutputDemand / Watcher は future candidate だが未実装として固定した
- audio hot path / LLM-TTS ordering / stop ack path は触らないことを再固定した
- `runtime_touchpoint_read_only_helper` は候補として維持するが実装延期とした
- 次フェーズ候補を A: 実ブラウザ追加確認、B: DB write demand 化の設計だけ、C: high-risk reply command の個別設計だけ、の3つに絞った
- 今回は runtime code の制御変更、helper 実装、新規 command / runner、OutputDemand / Watcher、ReplyOrchestrator 制御変更、`reply_done` 移管、cancel / TTS finished new input 化、stop ack / audio hot path / LLM-TTS ordering 変更はしていない

## 2026-05-28 セッション42

### やること（開始時に書く）
- Phase 10.17.6i の完了扱いは維持しつつ、`runtime_touchpoint_read_only_helper` をすぐ実装すべきか再確認する
- 今回は runtime code の制御変更、production runtime path からの helper 呼び出し、command / runner 追加、OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、reply_done 移管、cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、LLM/TTS 順序変更はしない

### やったこと
- `runtime_touchpoint_read_only_helper` は production runtime change ではなく、既存 map を読む read-only inspection helper として分類した
- 現時点では既存 map / tests / PLAN / MEMORY で判断材料は足りているため、helper 実装は延期する判断を PLAN.md / MEMORY.md に追記した
- helper を入れる場合の最小条件として、`10.17.6j: runtime touchpoint read-only helper, not used by production path` を明示し、production runtime path から import / call しないことを固定した
- 10.17.6i の reject 判断は維持した

### 詰まったこと・解決したこと
- `selected-first-runtime-change` という名前は、そのまま読むと次にすぐ実装してよいように見える
  - 解決: 10.17.6i checkpoint として、候補選定は維持するが helper 実装は必要性が出るまで延期する、と docs に固定した

### 検証
- docs-only 変更のため新規 unit test は追加していない
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_change_candidates.py -q`
  - 8 passed
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション41

### やること（開始時に書く）
- Phase 10.17.6i として minimal runtime change candidate selection を map-only / docs-only で行う
- 10.17.6a〜10.17.6h の map / registry / forbidden transition / runtime touchpoint / migration readiness をもとに、次フェーズで実装可能な最小 runtime change 候補を 1 個だけ選定する
- 今回は runtime code の制御変更、実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、reply_done 移管、cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、LLM/TTS 順序変更はしない

### やったこと
- `server/session/flow_runtime_change_candidates.py` を追加し、minimal runtime change candidate を map-only / docs-only で整理した
- first runtime change candidate を `runtime_touchpoint_read_only_helper` 1 個だけに絞った
- 選定理由を、既存 runtime touchpoint map を読む read-only helper であり route / hot path / ReplyOrchestrator 制御 / lifecycle migration / future-* 昇格 / 実行順序変更 / 新規 command を伴わないこととして固定した
- `reply_done` lifecycle migration、cancel / TTS finished new input 化、OutputDemand / Watcher、stop ack path、audio hot path、LLM/TTS ordering は rejected-for-now とし、それぞれの拒否理由を固定した
- readiness / forbidden transition / runtime touchpoint / consistency test に candidate selection の横断確認を追加した
- PLAN.md と MEMORY.md に Phase 10.17.6i の判断を追記した

### 詰まったこと・解決したこと
- `candidate_runner_output_read_only_helper` も小さく見えるが、candidate runner output path は既に runtime-current である
  - 解決: first candidate から外し、runner-output と session input の境界を曖昧にしない read-only touchpoint helper だけを選定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_change_candidates.py -q`
  - 初回は `flow_runtime_change_candidates.py` 未作成で collection error
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_change_candidates.py -q`
  - 8 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_change_candidates.py tests/unit/test_session_flow_migration_readiness.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_map_consistency.py -q`
  - 66 passed
- `.venv/bin/python -m ruff check server/session/flow_runtime_change_candidates.py tests/unit/test_session_flow_runtime_change_candidates.py tests/unit/test_session_flow_migration_readiness.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_map_consistency.py`
  - 初回は import order で失敗
- `.venv/bin/python -m ruff check server/session/flow_runtime_change_candidates.py tests/unit/test_session_flow_runtime_change_candidates.py tests/unit/test_session_flow_migration_readiness.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 538 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション40

### やること（開始時に書く）
- Phase 10.17.6h として migration readiness checklist を map-only / docs-only で追加する
- 10.17.6a〜10.17.6g の flow map / registry / forbidden transition / runtime touchpoint をもとに、次フェーズで実装変更に入るための条件を明文化する
- 今回は runtime code の制御変更、実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、reply_done 移管、cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、LLM/TTS 順序変更はしない

### やったこと
- `server/session/flow_migration_readiness.py` を追加し、migration readiness checklist を map-only / docs-only で固定した
- `future-*` は explicit phase / dedicated test / guard / owner boundary / doc update を満たすまで runtime-current に昇格しないことを固定した
- `should-not-move-yet` は readiness の存在だけでは解除されないことを固定した
- audio hot path / `reply_done` / cancel / TTS finished / OutputDemand / Watcher / stop ack は explicit phase なしに ready 扱いしないようにした
- runtime touchpoint は記録済みでも実装許可を意味しないことを固定した
- registry / forbidden transition / runtime touchpoint / consistency test に readiness の横断確認を追加した
- PLAN.md と MEMORY.md に Phase 10.17.6h の判断を追記した

### 詰まったこと・解決したこと
- readiness checklist を追加すると「次は ready だから実装してよい」と読まれやすい
  - 解決: 全 entry を `not-ready-runtime-change` とし、`requires-*` / `blocked-by-*` 条件を満たすまで許可ではないことを test で固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_migration_readiness.py -q`
  - 初回は `flow_migration_readiness.py` 未作成で collection error
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_migration_readiness.py -q`
  - 9 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_migration_readiness.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_map_consistency.py -q`
  - 54 passed
- `.venv/bin/python -m ruff check server/session/flow_migration_readiness.py tests/unit/test_session_flow_migration_readiness.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 526 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション39

### やること（開始時に書く）
- Phase 10.17.6g として runtime touchpoint audit を read-only / map-only で行う
- TomoroSession / ReplyOrchestrator / candidate runner / websocket notification / audio chunk / stop ack / cancellation / interruption / LLM-TTS ordering の既存 runtime 接点を洗い出す
- 実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、ReplyOrchestrator 制御変更、reply_done 移管、cancel / TTS finished new input 配線、stop ack / websocket / audio chunk 経路変更、LLM/TTS 順序変更はしない

### やったこと
- `server/session/flow_runtime_touchpoints.py` を追加し、既存 runtime touchpoint を read-only / map-only で分類した
- TomoroSession の signal entry / websocket client notification / audio hot path / precomputed reply done を touchpoint として固定した
- ReplyOrchestrator の approved reply execution / `reply_text` / TTS flush / audio chunk / normal `reply_done` / LLM-TTS ordering を touchpoint として固定した
- CandidateCommandRunner の loaded result / `candidate_command_failed` を runner-output-path として固定した
- stop ack reply path を `TomoroSessionEffects._apply_stop_intent_ack -> /ws reply_done control` のまま固定した
- cancellation / interruption は future-migration-candidate touchpoint として記録したが、new input wiring はしていない
- registry / forbidden transition / consistency test に runtime touchpoint の横断確認を追加した
- PLAN.md と MEMORY.md に Phase 10.17.6g の判断を追記した

### 詰まったこと・解決したこと
- `cancellation` と `interruption` は将来 lifecycle input 候補だが、audio hot path や `reply_done` と違って must-remain-current ではない
  - 解決: `future-migration-candidate` として touchpoint に載せつつ、`is_new_input_wired=False` と forbidden transition 参照で未配線を固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_touchpoints.py -q`
  - 初回は `flow_runtime_touchpoints.py` 未作成で collection error
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_touchpoints.py -q`
  - 8 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_map_consistency.py -q`
  - 41 passed
- `.venv/bin/python -m ruff check server/session/flow_runtime_touchpoints.py tests/unit/test_session_flow_runtime_touchpoints.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 513 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション38

### やること（開始時に書く）
- Phase 10.17.6f として forbidden transition map を map-only で追加する
- candidate_flow / reply_flow / output_flow / lifecycle_flow / flow_registry の分類語彙について、今のフェーズで移動・統合・配線してはいけない関係を固定する
- 実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、reply orchestration 制御変更、reply_done 移管、cancel / TTS finished new input 配線、stop ack 経路変更、audio hot path 変更、LLM/TTS 順序変更はしない

### やったこと
- `server/session/flow_forbidden_transitions.py` を追加し、forbidden transition を map-only で固定した
- `client-notification` と `candidate-demand` の相互変換禁止、`runner-output` と reply future candidate の混同禁止を追加した
- `future-new-input-candidate` / `future-output-demand-candidate` / `future-watcher-candidate` を runtime-current へ昇格しない guard として固定した
- `audio-hot-path` を OutputDemand / client notification に吸収しない guard として固定した
- `reply_done` lifecycle implementation、`reply_cancelled` / `tts_finished` new input implementation、stop ack routing change を禁止した
- `tests/unit/test_session_flow_forbidden_transitions.py` を追加し、既存 registry / consistency test に横断確認を足した
- PLAN.md と MEMORY.md に Phase 10.17.6f の判断を追記した

### 詰まったこと・解決したこと
- `reply_done` は lifecycle-boundary として読めるが、禁止遷移では event 名そのものを guard したい
  - 解決: vocabulary term だけでなく `event_name` / `current_route` を持つ forbidden transition として固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_forbidden_transitions.py -q`
  - 初回は `flow_forbidden_transitions.py` 未作成で collection error
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_forbidden_transitions.py -q`
  - 8 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_map_consistency.py -q`
  - 30 passed
- `.venv/bin/python -m ruff check server/session/flow_forbidden_transitions.py tests/unit/test_session_flow_forbidden_transitions.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 502 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション37

### やること（開始時に書く）
- Phase 10.17.6e として flow map vocabulary registry を map-only で追加する
- candidate_flow / reply_flow / output_flow / lifecycle_flow に散っている分類語彙を一覧化し、意味の重複・混線・危険な再利用を防ぐ
- 実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、reply orchestration 制御変更、reply_done 移管、cancel / TTS finished new input 配線、audio hot path 変更はしない

### やったこと
- `server/session/flow_registry.py` を追加し、flow map の分類語彙 registry を map-only で固定した
- common guard / shared boundary / flow-specific / future-unimplemented / do-not-move / hot-path / runtime-current を分類カテゴリとして定義した
- `future-*` 系語彙は未実装候補であり、実装済み new input / OutputDemand / Watcher を意味しないことを test で固定した
- `should-not-move-yet` は future candidate とは別概念として固定した
- `candidate-demand` / `client-notification` / `runner-output` / `audio-hot-path` / `lifecycle-boundary` の混同を防ぐ characterization test を追加した
- 既存 flow map consistency test に registry guard の横断確認を追加した
- PLAN.md と MEMORY.md に Phase 10.17.6e の判断を追記した

### 詰まったこと・解決したこと
- `audio hot path` と `audio-hot-path`、`future new-input candidate` と `future-new-input-candidate` のような表記揺れが map 間に存在する
  - 解決: registry では別 term として収録しつつ aliases で関係を明示し、どちらも runtime 実装変更を意味しないことを固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_registry.py -q`
  - 初回は `flow_registry.py` 未作成で collection error
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_map_consistency.py -q`
  - 20 passed
- `.venv/bin/python -m ruff check server/session/flow_registry.py tests/unit/test_session_flow_registry.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 492 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション36

### やること（開始時に書く）
- Phase 10.17.6d として reply lifecycle boundary 周辺を map-only で整理する
- `reply_done` / cancel / TTS finished / stop ack / interruption / audio hot path / LLM-TTS ordering の境界を characterization test で固定する
- `reply_done` 移管、cancel / TTS finished の new input 配線、interruption / cancellation 制御変更、stop ack 経路変更、audio chunk 経路変更、LLM/TTS 順序変更はしない

### やったこと
- `server/session/lifecycle_flow.py` を追加し、`LIFECYCLE_FLOW_BOUNDARY_MAP` で reply lifecycle boundary 周辺を分類した
- `reply_done` は lifecycle-boundary だが `/ws` client notification のまま維持する分類にした
- reply cancel / TTS finished は future-new-input-candidate だが未配線として分類した
- cancellation / interruption は lifecycle 関連境界だが ReplyOrchestrator 制御変更なしとして固定した
- stop ack reply path は確認対象として固定し、`TomoroSessionEffects._apply_stop_intent_ack -> /ws reply_done control` のままにした
- audio chunk は audio-hot-path、LLM/TTS ordering は should-not-move-yet として固定した
- `tests/unit/test_session_lifecycle_flow_map.py` を追加し、既存 consistency test に lifecycle flow の横断確認を足した
- PLAN.md に Phase 10.17.6d を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- `reply_done` は lifecycle classifier では new input 候補でもあるが、実経路では client notification のまま維持する必要がある
  - 解決: lifecycle-boundary と client-notification-route を分けて map に載せ、`is_new_input_wired=False` を test で固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_lifecycle_flow_map.py -q`
  - 初回は `lifecycle_flow.py` 未作成で collection error
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_lifecycle_flow_map.py -q`
  - 7 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_lifecycle_flow_map.py tests/unit/test_session_flow_map_consistency.py tests/unit/test_session_lifecycle.py tests/unit/test_session_reply_flow_map.py tests/unit/test_session_output_flow_map.py -q`
  - 45 passed
- `.venv/bin/python -m ruff check server/session/lifecycle_flow.py tests/unit/test_session_lifecycle_flow_map.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 482 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション35

### やること（開始時に書く）
- Phase 10.17.6c として OutputDemand / output boundary 周辺を map-only で整理する
- candidate demand / client notification / runner output / audio hot path / future OutputDemand / future Watcher の境界を characterization test で固定する
- 実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、reply orchestration 制御変更、audio hot path 変更はしない

### やったこと
- `server/session/output_flow.py` を追加し、`OUTPUT_FLOW_BOUNDARY_MAP` で output boundary 周辺を分類した
- candidate fetch / judge / store mark / reply start を candidate-demand として分類し、client notification ではないことを固定した
- candidate loaded / `candidate_command_failed` を runner-output として分類し、reply cancel / TTS finished とは分けた
- `reply_text` / `reply_done` を client-notification、audio chunk を audio-hot-path として分類した
- OutputDemand / Watcher は future candidate として分類したが、実装済み扱いしないようにした
- `tests/unit/test_session_output_flow_map.py` を追加し、既存 `test_session_flow_map_consistency.py` に output flow の横断確認を足した
- PLAN.md に Phase 10.17.6c を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- `start_*_reply` は candidate demand だが、reply path へ渡る境界でもあるため、単純な `candidate_flow` だけでは読めない
  - 解決: `candidate_to_reply_boundary` として分類し、client notification ではないことを test で固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_output_flow_map.py -q`
  - 初回は `candidate_reply_start_demands` の境界分類で 1 failed / 6 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_output_flow_map.py -q`
  - 7 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_output_flow_map.py tests/unit/test_session_flow_map_consistency.py tests/unit/test_session_candidate_flow_map.py tests/unit/test_session_reply_flow_map.py -q`
  - 33 passed
- `.venv/bin/python -m ruff check server/session/output_flow.py tests/unit/test_session_output_flow_map.py tests/unit/test_session_flow_map_consistency.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 473 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション34

### やること（開始時に書く）
- Phase 10.17.6b として candidate flow / reply flow map 間の整合性だけを unit test で固定する
- `command-but-runner-pending` / `already-command-and-runner` / `should-not-move-yet` / `future new-input candidate` / hot path / lifecycle boundary / ownership 語彙が混ざっていないことを確認する
- 実行配線、新規 command、runner 実装、OutputDemand / Watcher 実装、reply orchestration 制御変更、audio hot path 変更はしない

### やったこと
- `tests/unit/test_session_flow_map_consistency.py` を追加し、candidate flow / reply flow の横断 characterization を固定した
- candidate 側の `start_arrival_reply` / `start_initiative_reply` が reply-orchestration-owned で、reply 側の `start_precomputed_reply()` が TomoroSession owned 境界であることを確認した
- `already-command-and-runner` / `command-but-runner-pending` が reply flow へ漏れていないことを確認した
- `should-not-move-yet` と `future new-input candidate` が別概念であることを確認した
- `candidate_command_failed` は gateway runner output の new input で、reply flow の future candidate とは混ぜないことを確認した
- `reply_text` / audio chunk / `reply_done` が candidate demand/output 側へ混ざっていないことを確認した
- reply flow の no-routing-change guard に candidate flow と共通の `no_hot_path_change` を併記した
- PLAN.md に Phase 10.17.6b を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- candidate flow は `no_hot_path_change`、reply flow は `no_audio_hot_path_change` だけを持っており、横断 guard の語彙が少しずれていた
  - 解決: reply flow に共通 guard の `no_hot_path_change` も併記し、既存の audio-specific guard は残した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_map_consistency.py -q`
  - 初回は guard 語彙差分で 1 failed / 6 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_flow_map_consistency.py tests/unit/test_session_reply_flow_map.py tests/unit/test_session_candidate_flow_map.py -q`
  - 25 passed
- `.venv/bin/python -m ruff check server/session/reply_flow.py tests/unit/test_session_flow_map_consistency.py tests/unit/test_session_reply_flow_map.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 465 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション33

### やること（開始時に書く）
- Phase 10.17.6a として `ReplyOrchestrator` を closed-loop map-only で整理する
- 通常 reply / precomputed reply / stop ack reply path / reply_text delta / emotion / TTS flush / audio chunk / reply_done / cancellation を分類する
- 実行配線変更、command 追加、new input queue 再投入、`reply_done` routing 変更、audio hot path / `/ws` contract 変更はしない

### やったこと
- `server/session/reply_flow.py` を追加し、`REPLY_FLOW_CLOSED_LOOP_MAP` で ReplyOrchestrator 周辺を map-only に分類した
- `reply_text` delta は hot-ish client notification、`emotion` は client notification、TTS flush / audio chunk は audio hot path として固定した
- `reply_done` は lifecycle boundary だが client notification のまま維持する分類にした
- reply cancellation / interruption と TTS finished は future new-input candidate として読むが、今回は配線しないことを固定した
- `tests/unit/test_session_reply_flow_map.py` を追加し、上記分類と no-routing-change guard を characterization として固定した
- PLAN.md に Phase 10.17.6a を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- `start_precomputed_reply()` は candidate runner output から入るが、reply output ordering と attention / feedback state update が TomoroSession 側に残っている
  - 解決: ReplyOrchestrator ではなく TomoroSession 側の changer/state update として分類した
- stop ack reply path は cancellation / reserved audio / `reply_done` control notification が絡む
  - 解決: should-not-move-yet として、今回の実装移動対象から外した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_reply_flow_map.py -q`
  - 7 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_reply_flow_map.py tests/unit/test_session_lifecycle.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_reply_audio_pipeline.py -q`
  - 29 passed
- `.venv/bin/python -m pytest -m unit`
  - 458 passed, 17 deselected
- `.venv/bin/python -m ruff check server/session/reply_flow.py tests/unit/test_session_reply_flow_map.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション32

### やること（開始時に書く）
- Phase 10.17.5b として Candidate / initiative flow の demand/output readiness を分類する
- 対象 command / event を `already-command-and-runner` / `command-but-runner-pending` / `session-final-gate` / `gateway-runner-output` / `reply-orchestration-owned` / `should-not-move-yet` に分ける
- 実行配線変更、新規 command 追加、OutputDemand / Watcher 新設、reply orchestration 変更、audio hot path 変更はしない

### やったこと
- `server/session/candidate_flow.py` に `CandidateFlowDemandOutputReadinessEntry` と `CANDIDATE_FLOW_DEMAND_OUTPUT_READINESS` を追加した
- `CANDIDATE_FLOW_FINAL_GATE_READINESS` を追加し、final gate が TomoroSession 側に残ることを分類表にした
- `fetch_*` / `judge_initiative_candidate` / candidate store mark / dismiss は already-command-and-runner とした
- `start_arrival_reply` / `start_initiative_reply` は reply-orchestration-owned とした
- `candidate_command_failed` は gateway-runner-output かつ `SessionEventRunner` へ戻る new input として分類した
- `tests/unit/test_session_candidate_flow_map.py` に readiness characterization を追加した
- PLAN.md に Phase 10.17.5b を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- `start_*_reply` は `CandidateCommandRunner` が実行しているが、実体は `start_precomputed_reply()` 経由で reply output ordering に入る
  - 解決: already-command-and-runner ではなく reply-orchestration-owned として、次の実装移動対象から外した
- `candidate_command_failed` は command ではなく runner が生成する event である
  - 解決: gateway-runner-output / new input として分類した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_flow_map.py -q`
  - 11 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_flow_map.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_session_lifecycle.py -q`
  - 31 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 451 passed, 17 deselected
- `.venv/bin/python -m ruff check server/session/candidate_flow.py tests/unit/test_session_candidate_flow_map.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション31

### やること（開始時に書く）
- Phase 10.17 checkpoint を PLAN.md に追記する
- 次に進む候補 B として Candidate / initiative flow を closed-loop map する
- 今回は実行配線、candidate 処理、reply orchestration、hot path、`/ws` contract を変更しない

### やったこと
- PLAN.md に Phase 10.17 checkpoint を追記し、closed-loop / command owner / Effects 移行 / TranscriptFlow 到達点と次候補 A/B/C をまとめた
- `server/session/candidate_flow.py` を追加し、`CANDIDATE_FLOW_CLOSED_LOOP_MAP` を静的 map として定義した
- `tests/unit/test_session_candidate_flow_map.py` を追加し、initiative / arrival path、reducer 側 changer、gateway runner output、final gate ownership を characterization として固定した
- PLAN.md に Phase 10.17.5a を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- candidate flow は `TomoroSessionReducer` と `CandidateCommandRunner` にまたがる
  - 解決: reducer 側を changer、candidate store I/O / reply start / mark を watcher output として map し、実行配線は変えない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_flow_map.py -q`
  - 5 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_candidate_flow_map.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_session_lifecycle.py -q`
  - 38 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 445 passed, 17 deselected
- `.venv/bin/python -m ruff check server/session/candidate_flow.py tests/unit/test_session_candidate_flow_map.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション30

### やること（開始時に書く）
- Phase 10.17.4 continuation として、`TranscriptFlow` の demand emission 候補が既存 `SessionCommand` / `TomoroSessionEffects` に到達済みか分類する
- 分類は `already-command-and-effects` / `command-but-effects-pending` / `direct-output-not-command` / `should-not-move-yet` とする
- 今回は実装移動、command 追加、reply orchestration 変更、audio hot path 変更、`/ws` contract 変更をしない

### やったこと
- `server/session/transcript_flow.py` に `TranscriptFlowDemandReadinessEntry` と `TRANSCRIPT_FLOW_DEMAND_EMISSION_READINESS` を追加した
- demand emission 候補を `SessionCommand` / `TomoroSessionEffects` の実装済み table と照合した
  - already-command-and-effects: `insert_stop_intent_observation` / `send_audio_control_stop` / `write_ambient_observer`
  - command-but-effects-pending: なし
  - direct-output-not-command: `ambient_log_write`
  - should-not-move-yet: `conversation_log_write` / `conversation_embedding_schedule`
- `tests/unit/test_session_transcript_flow_map.py` に readiness characterization を追加し、implemented / pending table と分類の整合を固定した
- PLAN.md に Phase 10.17.4c を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- `conversation_log_write` は `save_tomoko_turn` と名前が近いが、現状の `TranscriptFlow` では user turn persistence であり Tomoko turn command とは同一視しない
  - 解決: 低リスク候補にはせず、`conversation_embedding_schedule` とセットで should-not-move-yet にした
- `ambient_log_write` は `write_ambient_observer` と似ているが、participation path の ambient write 全般をまだ表す command がない
  - 解決: direct-output-not-command として分類した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_transcript_flow_map.py -q`
  - 11 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_transcript_flow_map.py tests/unit/test_session_commands.py tests/unit/test_session_signals.py tests/unit/test_phase885_session_runtime.py -q`
  - 27 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 440 passed, 17 deselected
- `.venv/bin/python -m ruff check server/session/transcript_flow.py tests/unit/test_session_transcript_flow_map.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション29

### やること（開始時に書く）
- Phase 10.17.4 continuation として `TranscriptFlow` map 上の direct output を分類する
- 対象は `barge_in_decision` / `participation_decision` / `session_lifecycle` に限定する
- direct output の移動、command 追加、reply orchestration 変更、audio hot path 変更、`/ws` contract 変更はしない

### やったこと
- `server/session/transcript_flow.py` に `TranscriptFlowDirectOutputClassificationEntry` と `TRANSCRIPT_FLOW_DIRECT_OUTPUT_CLASSIFICATIONS` を追加した
- `barge_in_decision` / `participation_decision` / `session_lifecycle` の direct output を分類した
  - changer/state update: `initiative_feedback`
  - demand emission candidate: DB write / observer write / embedding scheduling / audio stop demand
  - gateway/client notification: existing `/ws` client events
  - reply orchestration owned: `cancel_reply_generation`
- `tests/unit/test_session_transcript_flow_map.py` に classification characterization を追加した
- PLAN.md に Phase 10.17.4b を追記し、MEMORY.md に今回の分類判断を追記した

### 詰まったこと・解決したこと
- `send_audio_control_stop` は client 向け audio control でもあるが、既に session_watcher command/effect 境界がある
  - 解決: `/ws` payload は変えず、分類上は demand emission candidate として扱う
- `cancel_reply_generation` は demand 化候補にも見えるが、reply task / TTS cancellation ordering に触る
  - 解決: 今回は reply orchestration owned と分類し、TranscriptFlow 側では触らない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_transcript_flow_map.py -q`
  - 7 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_transcript_flow_map.py tests/unit/test_session_signals.py tests/unit/test_phase885_session_runtime.py -q`
  - 15 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 436 passed, 17 deselected
- `.venv/bin/python -m ruff check server/session/transcript_flow.py tests/unit/test_session_transcript_flow_map.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション28

### やること（開始時に書く）
- Phase 10.17.4 continuation として `TranscriptFlow` を closed-loop の changer として読むための現状 map を追加する
- characterization test で participation / turn-taking / barge-in / session lifecycle / direct output の現状分類を固定する
- direct output の移動、command 追加、reply orchestration 変更はしない

### やったこと
- `server/session/transcript_flow.py` に `TranscriptFlowClosedLoopStep` と `TRANSCRIPT_FLOW_CLOSED_LOOP_MAP` を追加した
- `TranscriptFlow` の現状を `transcript_filter` / `turn_taking_decision` / `barge_in_decision` / `participation_decision` / `session_lifecycle` / `reply_start_decision` / `audio_input_reset` に分類した
- `tests/unit/test_session_transcript_flow_map.py` を追加し、changer / watcher boundary / input boundary と、現状 direct output の一覧を characterization として固定した
- PLAN.md に Phase 10.17.4a を追記し、MEMORY.md に今回の判断を追記した

### 詰まったこと・解決したこと
- `TranscriptFlow` にはまだ client event / DB write / reply start などの direct output が残っている
  - 解決: 今回は移動せず、closed-loop map 上で現状 direct output として明示し、次の移動対象を見える形にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_transcript_flow_map.py tests/unit/test_session_signals.py tests/unit/test_phase885_session_runtime.py -q`
  - 11 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 432 passed, 17 deselected
- `.venv/bin/python -m ruff check server/session/transcript_flow.py tests/unit/test_session_transcript_flow_map.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-28 セッション27

### やること（開始時に書く）
- `make server-debug` 後の `logs/server-debug.log` を確認し、Phase 10.17 系の lifecycle trace / session command trace が実経路に出ているかを見る
- 実装変更はせず、`reply_done` / lifecycle routing / hot path / `/ws` contract は触らない

### やったこと
- 19:56〜20:00 の `logs/server-debug.log` を確認した
- WebSocket 接続直後の `arrival_candidate_loaded` が `SessionEventRunner lifecycle_new_input_candidate kind=candidate_result` として trace されていることを確認した
- `reply_text` delta / TTS latency / STT transcript / state transition などの hot path では `lifecycle_new_input_candidate` trace が増えていないことを確認した
- `reply_done` は現設計どおり client notification のままで、SessionEventRunner trace には出ていないことを確認した

### 詰まったこと・解決したこと
- 今回の実会話では `initiative_candidate_loaded` / `candidate_command_failed` / `reply_cancelled` / `tts_finished` は発生していない
  - 解決: 今回は実ログ上で発生した `arrival_candidate_loaded` の trace と、発生していない lifecycle event の現設計との差分を分けて確認した

### 検証
- `rg` で `logs/server-debug.log` の lifecycle / warning / command / hot path 関連行を確認した
- `git diff --check`
  - pass

### 次のセッションでやること
- initiative / candidate failure / cancellation を実操作または targeted test で発生させる場合は、配線変更せず trace の出方だけ確認する

## 2026-05-28 セッション14

### やること（開始時に書く）
- Phase 10.15 Session signal boundary and gateway port split と配下タスク全体を実装する
- コミットは行わず、既存の PLAN.md / LOG.md 未コミット追記を保持する
- `SessionInputSignal` / `SessionOutputSignal` の最小型を追加し、audio binary は signal 化しない
- `TomoroSession.accept_signal()` と package 内 dispatcher を追加し、既存 `post_event()` / `process_transcript()` は compatibility sugar として残す
- gateway / edge adapter は semantic event を signal として session に渡す方向へ寄せる

### やったこと
- `server/shared/models.py` に `SessionInputSignal` / `SessionOutputSignal` を追加した
  - `SessionInputSignal` は `Transcript | PlaybackTelemetry | SessionEvent` の alias とし、既存 DTO の二重包装を避けた
  - `SessionOutputSignal` は既存 client JSON event contract を壊さない薄い wrapper とした
- `server/gateway/ports.py` を追加し、gateway input/output を audio path と signal path に分類できるようにした
- `TomoroSession.accept_signal()` を追加し、`post_event()` / `process_transcript()` / `handle_playback_telemetry()` を互換 sugar に寄せた
- `server/session/dispatcher.py` を追加し、signal type から transcript / playback / legacy event reducer へ振り分ける目次にした
- `server/session/transcript_flow.py` を追加し、final transcript の処理本体を `core.py` から移した
- `server/gateway/edge_adapter.py` / `server/edge/main.py` / `server/gateway/candidate_commands.py` の semantic session input を `accept_signal()` 経由へ寄せた
- `tests/unit/test_session_signals.py` を追加し、signal 型、gateway port 分類、compatibility sugar、dispatcher route を固定した
- PLAN.md の Phase 10.15.0〜10.15.4 のチェックを更新し、MEMORY.md に今回の signal boundary 方針を追記した

### 詰まったこと・解決したこと
- `ExternalTranscriptInput` のような wrapper を作ると既存 `Transcript` DTO の二重包装になりそうだった
  - 解決: `SessionInputSignal` は既存 semantic DTO の type alias にし、reset など local STT 由来の補助情報は `accept_signal(..., reset_audio_input=True)` の keyword に留めた
- dispatcher を作るだけだと巨大判断体が移るだけになる
  - 解決: dispatcher は type switch だけにし、final transcript 本体は `TranscriptFlow`、playback は既存 `OperationPlan` 経由 reducer に残した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_signals.py tests/unit/test_phase14_edge_split.py -q`
  - 17 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_signals.py tests/unit/test_phase14_edge_split.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py -q`
  - 43 passed
- `.venv/bin/python -m pytest -m unit`
  - 394 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- lifecycle flow または candidate flow を `accept_signal()` / dispatcher 配下の明示 flow としてさらに分離する
- `SessionEvent(type=..., payload=dict)` に残す event type / payload key の定数化範囲を、家族版で high-risk になるものから順に狭める

### 追加判断
- 現在の 10.15 実装は、Phase 10.13 の `_RUNTIME_STATE_FIELDS` / `__getattr__` / `__setattr__` が残った状態で積んでいるため、一度戻す方針に変更する
- 次の順序にする
  - 今回の 10.15 実装差分を戻す
  - Phase 10.16 として `_RUNTIME_STATE_FIELDS` / `__getattr__` / `__setattr__` を消す
  - `runtime_state.xxx` か明示 method に寄せる
  - その後で 10.15 の `accept_signal` / dispatcher / flow を再実装する
- PLAN.md に Phase 10.15.R / 10.16 / 10.15.Re として追記した

## 2026-05-28 セッション13

### やること（開始時に書く）
- 直前の dispatcher / transcript flow preview の未コミット差分を破棄する
- PLAN.md に、gateway / session 境界を audio path と signal path に分ける Phase を追記する
- 今回は実装せず、家族版へ進む前の構造整理方針を固定する

### やったこと
- 未コミットだった `LOG.md` / `MEMORY.md` / `PLAN.md` / `server/session/core.py` の preview 差分を破棄した
- 未コミットだった `server/session/dispatcher.py` / `server/session/inputs.py` / `server/session/transcript_flow.py` / `tests/unit/test_session_dispatcher.py` を削除した
- PLAN.md に Phase 10.15 Session signal boundary and gateway port split を追記した

### 詰まったこと・解決したこと
- preview 実装では `ExternalTranscriptInput` が既存 `Transcript` DTO の二重包装に見え、読みやすさが増えたか判断しづらかった
  - 解決: preview は破棄し、先に gateway / session の入出力分類を PLAN 上で固定する方針に戻した

### 検証
- `git status --short`
  - preview 破棄後は差分なし

### 次のセッションでやること
- Phase 10.15.0 から、SessionInputSignal / SessionOutputSignal の最小型と gateway port 分類を実装する

## 2026-05-28 セッション12

### やること（開始時に書く）
- Phase 10.14 Session operation plan prototype を PLAN.md に追記して実装する
- `parallel([...]).then().do(...)` に近い考え方を Python の小さな `EventPlan` として実装する
- read-only は同一 phase で並列実行でき、write は phase 順に直列化できる契約を unit test で固定する
- 既存 reducer の単純 event で `EventPlan` を実利用し、抽象だけで終わらせない

### やったこと
- PLAN.md に Phase 10.14 Session operation plan prototype を追記し、完了チェックを更新した
- `server/session/operation_plan.py` に `OperationPlan` / `OperationContext` / `OperationResult` を追加した
- `.parallel([...]).then().do(...)` に近い書き味で、phase 内は async gather、phase 間は順序保証する実行器を追加した
- `TomoroSessionReducer` の playback telemetry event を `OperationPlan` 経由にし、既存 `SessionEvent` / `SessionCommand` / `TransitionResult` 契約を維持した
- `tests/unit/test_session_operation_plan.py` を追加し、parallel phase の同時実行、then phase の順序、sync runner の async step reject を固定した
- MEMORY.md に Phase 10.14 の operation plan 方針を追記した

### 詰まったこと・解決したこと
- async step を `run_sync()` に渡した時、生成済み coroutine が warning を出した
  - 解決: sync runner が awaitable を検出した時に coroutine を close してから明示的に `RuntimeError` を出すようにした
- reducer はまだ同期 reduce 契約なので、いきなり全体を async dispatcher にするのは差分が大きい
  - 解決: `OperationPlan.run()` は将来の parallel read 用に async、既存 reducer からは `run_sync()` で実利用する二段構えにした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_operation_plan.py tests/unit/test_session_reducer.py -q`
  - 6 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_operation_plan.py tests/unit/test_session_reducer.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py -q`
  - 32 passed
- `.venv/bin/python -m pytest -m unit`
  - 388 passed, 17 deselected

### 次のセッションでやること
- `TranscriptFlow` / `LifecycleFlow` のどちらかを、read-only policy と write transition の plan として切り出す
- `post_event(SessionEvent(type=...))` の外部公開感を減らし、public API は typed facade method に寄せる

## 2026-05-28 セッション11

### やること（開始時に書く）
- Phase 10.13 TomoroSession state object and operation boundary を PLAN.md に追記して実装する
- `TomoroSessionState` を追加し、runtime state fields を一箇所に集約する
- production 外部の public facade は引き続き `TomoroSession` のみに保ち、operation helper は package 内部実装として扱う
- 既存 `/ws` / Thinking / TTS / DB store 契約を変えず、unit test と ruff で挙動不変を確認する

### やったこと
- PLAN.md に Phase 10.13 TomoroSession state object and operation boundary を追記し、完了チェックを更新した
- `server/session/state.py` に `TomoroSessionState` を追加し、runtime state fields を集約した
- `TomoroSession` に `runtime_state` と内部互換 proxy を追加し、既存 internal access の挙動を保ったまま state の置き場を一箇所にした
- `session_started` / `initiative_candidate_loaded` / `arrival_candidate_loaded` の event-driven 判断を `TomoroSessionReducer` に移した
- `tests/unit/test_session_state.py` を追加し、state 初期値と connected output snapshot の契約を固定した
- `tests/unit/test_session_reducer.py` に、legacy property 経由の active session id が `runtime_state` に入ることを追加確認した
- MEMORY.md に Phase 10.13 の state container 方針を追記した

### 詰まったこと・解決したこと
- state object を追加しただけでは `core.py` の行数がほぼ減らなかった
  - 解決: candidate / arrival の event reducer も `TomoroSessionReducer` へ移し、`core.py` を 1836 行から 1572 行まで減らした
- 既存コードの全 state access を一気に `runtime_state.foo` へ書き換えると差分が大きくなりすぎる
  - 解決: Phase 10.13 では internal proxy を許可し、状態の置き場を先に一箇所へ寄せた

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py tests/unit/test_session_reducer.py tests/unit/test_session_concurrency.py -q`
  - 32 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_state.py tests/unit/test_session_reducer.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py -q`
  - 31 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_state.py tests/unit/test_session_reducer.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase106_initiative_policy.py -q`
  - 51 passed
- `.venv/bin/python -m pytest -m unit`
  - 385 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- さらに読みやすくするなら、`process_transcript()` 周辺を `TranscriptFlow` へ分離し、core を 1200 行台まで落とす

## 2026-05-28 セッション10

### やること（開始時に書く）
- Phase 10.12 TomoroSession package split and internal responsibility extraction 全体を実装する
- `server/session.py` を `server/session/` package へ移し、外部 import 契約 `from server.session import TomoroSession` を維持する
- carryover helper / reducer / effects / reply orchestration の責任を package 内で分け、TomoroSession を public facade / state holder として残す
- PLAN.md / MEMORY.md / LOG.md は追記またはチェック更新だけで扱い、unit test と ruff で挙動不変を確認する

### やったこと
- `server/session.py` を `server/session/core.py` に移し、`server/session/__init__.py` から `TomoroSession` だけを export する package 構成にした
- `server/session/carryover.py` に `RetrievedContextCarryover` を切り出し、deep retrieval carryover の dedupe / eviction / clear を helper に分離した
- `server/session/reducer.py` に `TomoroSessionReducer` を追加し、playback / connected output / client stop / idle timer / stop intent classified の event reduce を移した
- `server/session/effects.py` に `TomoroSessionEffects` を追加し、`SessionCommand` 実行を effects executor に寄せた
- `server/session/reply_orchestrator.py` に `ReplyOrchestrator` を追加し、LLM stream / ReplyPipeline / TTS queue / Tomoko turn write の実行手順を TomoroSession 本体から分離した
- `tests/unit/test_session_carryover.py` を追加し、carryover の重複排除・件数/text budget eviction・clear を固定した
- `tests/unit/test_session_reducer.py` を追加し、client stop / stale stop intent / playback telemetry command の reducer 契約を固定した
- PLAN.md の Phase 10.12.0〜10.12.5 のチェックを更新し、MEMORY.md に今回の package facade 方針を追記した

### 詰まったこと・解決したこと
- `TomoroSessionEffects` へ stop ack 実行を移した後、turn-taking stop 経路が旧 private method を直接呼んで失敗した
  - 解決: `SessionCommand(type="apply_stop_intent_ack")` を `_run_internal_commands()` に戻し、stop ack も effects executor 経由に統一した
- carryover の text budget test で、単一 entry が budget を超えた場合に全 eviction される仕様を踏んだ
  - 解決: entry count と text budget を別条件で検証する test に分けた

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py -q`
  - 26 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase8_memory.py -q`
  - 25 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py tests/unit/test_session_concurrency.py tests/unit/test_barge_in.py -q`
  - 40 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_carryover.py tests/unit/test_phase88_context_snapshot.py -q`
  - 20 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_reducer.py tests/unit/test_session_carryover.py tests/unit/test_phase10_session_contract.py -q`
  - 19 passed
- `.venv/bin/python -m pytest -m unit`
  - 383 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで stop / playback interrupt / initiative follow-up を軽く通し、package split 後も runtime log の意味が読みやすいか確認する

## 2026-05-28 セッション9

### やること（開始時に書く）
- Phase 8.8.8 memory retrieval weighting and session turn restore と配下タスクを実装する
- source quota / weight / score breakdown を `ContextSnapshotBuilder` の trace に出す
- summary hit から user turn snippets を復元し、同一 query embedding を使い回す
- cue type ごとの memory weighting を rule-first で入れ、unit test と実ログで確認できる形にする

### やったこと
- `ContextSnapshotBuilder` に memory source ごとの quota / weight / final score 計算を追加した
- `ContextBuildTrace` に `cue_type` と selected / dropped / score breakdown を追加した
- `session_summaries` hit 後に、上位 session の raw user turn snippets を optional source として復元するようにした
- restored turn は session_id で raw logs を読むだけにし、online path では未 embedding turn を生成しない形にした
- cue type を rule-first で `recall` / `detail` / `stance` / `normal` に分類し、source weight を切り替えるようにした
- user turn を主、Tomoko turn を補助にする初期 quota / role weight をコード内定数として固定した
- background embedding 用に `background-process/embed_conversation_turns.py` と `make turn-embedder` / `make turn-embedder-once` を追加した
- PLAN.md の Phase 8.8.8.0〜8.8.8.3 のチェックを更新し、MEMORY.md に確定判断を追記した

### 詰まったこと・解決したこと
- `make server-debug` は既存の 8000 番サーバーが生きていて address in use になった
  - 解決: `PORT=8018 make server-debug` で起動 smoke を行い、startup warm-up と `/` の 200 応答を確認した
- Phase 8.8.8.4 の実マイク会話 quality tuning は、このセッションでは自動化できないため未チェックのまま残す
  - ただし、`source_scores` / `cue_type` / restored snippet counts はログで見える状態にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase88_context_snapshot.py -q`
  - 17 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py -q`
  - 25 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_makefile_process_entries.py tests/unit/test_phase88_context_snapshot.py -q`
  - 21 passed
- `.venv/bin/python -m pytest -m unit`
  - 377 passed, 17 deselected
- `.venv/bin/python -m ruff check background-process/embed_conversation_turns.py server/gateway/context.py server/shared/models.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_makefile_process_entries.py`
  - pass
- `make -n turn-embedder-once && make -n turn-embedder`
  - pass
- `PORT=8018 make server-debug`
  - startup complete, `GET /` 200, then stopped manually

### 次のセッションでやること
- 実ブラウザのマイク入力で `著作権の話覚えてる` / `詳しくはどんな話やったっけ` / `どういう風に考えてたっけ` を確認する
- `source_scores` と `ThinkFastMode llm_prompt` を見て、detail / stance の source weight を微調整する

## 2026-05-28 セッション8

### やること（開始時に書く）
- 記憶 retrieval / restored turn / source weighting の核心設計を PLAN.md に Phase として追記する
- `weight or quota` ではなく、quota は source 占有上限、weight は quota 内と assemble 時の ranking 補正として両方使う契約にする
- summary hit 後に横出しする DB query / rerank でも、embedding が必要な場合は同一 `query_embedding_task` を使い回すことを明記する
- 今回は実装せず、後続 LLM が迷わない計画境界を作る

### やったこと
- PLAN.md に Phase 8.8.8 memory retrieval weighting and session turn restore を追記した
- memory source の粒度、quota と weight の役割分担、final score の計算式を計画として固定した
- summary hit 後の二段階 async query / restored turn rerank でも同一 `query_embedding_task` を使い回す契約を明記した
- background embedding は CPU lane に寄せ、online path では未 embedding turn を作らない方針を明記した

### 検証
- `git diff --check`
  - pass

### 次のセッションでやること
- Phase 8.8.8.0 から、まず source quota / weight のコード内定数と trace 追加を実装する

## 2026-05-28 セッション7

### やること（開始時に書く）
- Phase 8.8.7 として、carryover された長期記憶が fast follow-up の実 prompt に入らない問題を修正する
- `ThinkDeepMode` 専用だった長期記憶 prompt formatter を共通化し、`ThinkFastMode` でも `ThinkingInput.long_term_memory` を読む
- deep retrieval の検索回数は増やさず、TomoroSession が渡した記憶だけを prompt に反映する
- 先に unit test で、fast mode の system prompt に carryover memory が含まれることを固定する

### やったこと
- PLAN.md に Phase 8.8.7 fast follow-up memory prompt を追記し、完了チェックを更新した
- `server/gateway/thinking/memory_prompt.py` を追加し、長期記憶 prompt formatter を共通化した
- `ThinkFastMode` が `ThinkingInput.long_term_memory` を受け取った時だけ system prompt に長期記憶ブロックを追加するようにした
- `ThinkDeepMode` は同じ formatter を使う薄い subclass に整理し、既存 deep memory prompt 契約を維持した
- fast mode の system prompt に carryover memory が含まれる regression test と、memory が空なら prompt を増やさない test を追加した
- MEMORY.md に、今回の原因が retrieval / carryover ではなく prompt 接続漏れだった判断を追記した

### 詰まったこと・解決したこと
- 実ログでは `carryover_used count=6` が出ていたため TomoroSession 側は memory を渡せていた
  - 解決: `ThinkFastMode` と `ThinkDeepMode` の prompt assembly を揃え、fast follow-up でも渡された memory を読むようにした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py -q`
  - 22 passed
- `.venv/bin/python -m pytest -m unit`
  - 374 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` の実ブラウザで、`carryover_used` が出る fast follow-up の `ThinkFastMode llm_prompt` に会話セッション要約が入ることを確認する

## 2026-05-28 セッション2

### やること（開始時に書く）
- `background-watch` は案内ターゲットのまま維持する
- Phase 10.11 実ブラウザ評価で必要な `turn-taking-worker` を案内に追加する
- 追加依存を増やさず、macOS 標準の `screen` で複数 worker をまとめて起動・attach・stop できる Makefile entry を追加する

### やったこと
- Makefile の `background-watch` 表示に `make turn-taking-worker` を追加した
- `screen-runtime` / `screen-runtime-full` / `screen-attach` / `screen-stop` / `screen-list` を追加した
- `screen-runtime` は会話評価の最小構成として `server-debug` / `turn-taking-worker` / `thinker` / `session-summarizer` / `persona-updater` を別 window で起動する
- `screen-runtime-full` は追加で `journalist` / `information-interpret` を起動する

### 詰まったこと・解決したこと
- `background-watch` は常駐 worker を一撃起動する target ではなく、別 terminal で起動する command の案内であることを維持した
- `tmux` はこの環境になく、`screen` は `/usr/bin/screen` にある
  - 解決: 追加 dependency を増やさない方針に合わせ、`screen` を使う Makefile entry にした
- `make -n screen-runtime-full` の dry-run 中に、recipe 内の `$(MAKE)` を含む行が GNU make の仕様で実行対象になった
  - 解決: `screen` window 内で実行する command は `$(MAKE)` ではなく literal `make` にして、dry-run で実起動しないようにした

### 検証
- `make background-watch`
  - `make turn-taking-worker` が表示されることを確認する
- `make -n screen-runtime`
  - `server-debug` / `turn-taking-worker` / `thinker` / `session-summarizer` / `persona-updater` を起動する dry-run を確認する
- `make -n screen-runtime-full`
  - full 追加 worker の dry-run を確認する

### 次のセッションでやること
- 一撃起動が必要になったら、Makefile の background 起動ではなく supervisor / tmux / docker compose などの管理方法を検討する

## 2026-05-26 セッション1

### やること（開始時に書く）
- backend 依頼/応答の debug trace を JSONL として `logs/backend-trace.jsonl` に出す
- LM Studio request lifecycle に `start` / `queue_acquired` / `response_headers` / `first_delta` / `done` / `error` を出す
- local LLM / TTS backend も同じ `tomoko_backend_call` trace 語彙で start / first / done / error を出し、GPU/queue 詰まりを推測できるようにする
- PLAN.md に backend trace Phase を追記してから実装する

### やったこと
- PLAN.md に Phase 13.5 backend call JSONL trace を追記し、完了チェックを更新した
- `server/shared/inference/trace.py` を追加し、`logs/backend-trace.jsonl` へ 1 行 1 JSON の `tomoko_backend_call` trace を出すようにした
- `chat_stream_with_trace_role()` / `chat_stream_structured_with_trace_role()` を追加し、既存 fake backend と互換性を保ちながら role を渡せるようにした
- `LMStudioBackend` に URL 単位 process-local semaphore と lifecycle trace を追加した
  - `start`
  - `queue_acquired` + `wait_ms`
  - `response_headers`
  - `first_delta`
  - `done`
  - `error`
- local LLM backend に同じ trace 語彙を追加した
  - `GemmaMLXBackend`
  - `MLXLMBackend`
  - `OllamaBackend`
- TTS backend に同じ trace 語彙を追加した
  - `SayBackend`
  - `KokoroMLXBackend`
  - `VoicevoxBackend`
  - `VoicevoxStreamBackend`
- STT / embedding backend に request 単位の trace を追加した
  - `FasterWhisperSTT`
  - `MlxWhisperSTT`
  - `WhisperCoreMLSTT`
  - `WhisperKitServeSTT`
  - `SentenceTransformerEmbeddingBackend`
- 会話 / 要約 / candidate / stop intent / initiative / diary / world observation から role を渡すようにした

### 詰まったこと・解決したこと
- 既存 unit test には `chat_stream(..., trace_role=...)` を受けない fake backend が多い
  → 直接 signature を変えた呼び出しにせず、helper が `trace_role` 対応可否を見て渡す形にした
- LM Studio の複数 backend 名は同じ URL を共有しうる
  → backend 名単位ではなく `lmstudio:<url>` を `queue_key` にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_backend_trace.py tests/unit/test_lm_studio_backend.py tests/unit/test_gemma_mlx_backend.py tests/unit/test_mlx_lm_backend.py tests/unit/test_voicevox_tts.py tests/unit/test_kokoro_mlx_tts.py tests/unit/test_phase4_thinking.py tests/unit/test_world_observation_normalizer.py tests/unit/test_world_observation_interpreter.py`
  - 34 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_backend_trace.py tests/unit/test_stt_backends.py tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py`
  - 31 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を走らせ、`jq 'select(.trace=="tomoko_backend_call")' logs/backend-trace.jsonl` で会話 / background の重なりと LM Studio queue wait を確認する

## 2026-05-26 セッション2

### やること（開始時に書く）
- Kokoro の方が first binary 到着は速い前提を明確にしつつ、体感確認用に default TTS を通常 VOICEVOX へ戻す
- cancellable synthesis の初回 worker 遅延を比較に混ぜないため、`voicevox_tsumugi_stream` ではなく `voicevox_tsumugi` を採用する

### やったこと
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の `tts_backend` を `voicevox_tsumugi` に変更した
- config 契約テストの期待値も `voicevox_tsumugi` に戻した
- README / MEMORY.md に、cancellable stream ではなく通常 VOICEVOX を試す理由を追記した

### 詰まったこと・解決したこと
- `/cancellable_synthesis` は最初の binary 到着を速めるものではなかった
  → VOICEVOX 体感確認では通常 `/synthesis` backend を使い、Kokoro との速度差は backend trace で別途見る

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_voicevox_tts.py`
  - 21 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check config/central_realtime.toml config/edge_kitchen.toml tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py`
  - pass
- `git diff --check`
  - pass

## 2026-05-26 セッション3

### やること（開始時に書く）
- STT で `WhisperKit + openai_whisper-large-v3-v20240930_turbo_632MB + cpuAndNeuralEngine` 相当を試した履歴があるか確認する
- exact model / compute units を明示できる `whisperkit_serve` backend 設定を追加する
- central realtime の active STT をこの backend に切り替え、unit test で契約を固定する

### やったこと
- 過去ログ上では `WhisperKit serve small`、`WhisperKit serve large-v3-v20240930_626MB`、`local_whisper_mlx_large_turbo_q4` は試していたが、画像の turbo 632MB exact lane は active config として固定していなかったことを確認した
- `WhisperKitServeSTT` に `compute_units` を追加し、`whisperkit-cli serve` 起動時に `--audio-encoder-compute-units` / `--text-decoder-compute-units` を渡すようにした
- `config/central_realtime.toml` に `local_whisperkit_serve_large_turbo_632m_cpu_ne` を追加した
  - model: `large-v3-v20240930_turbo_632MB`
  - port: `127.0.0.1:50062`
  - compute units: `cpuAndNeuralEngine`
- central realtime の active `stt_backend` をこの backend に切り替えた
- PLAN.md / MEMORY.md / `_docs/latency.md` に判断と未実測事項を追記した

### 詰まったこと・解決したこと
- WhisperKit CLI help 上は `cpuAndNeuralEngine` が default だったが、実験条件を明示するため config から渡す形にした
- 既存 `local_whisper_mlx_large_turbo_q4` は品質が良かった比較候補として残した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py`
  - 16 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、`logs/backend-trace.jsonl` の STT `total_ms`、実 transcript、mactop 等の CPU/ANE/GPU 使用状況を確認する

## 2026-05-25 セッション51

### やること（開始時に書く）
- LM Studio の OpenAI 互換 API で `gemma-4-e4b-it-mlx` を使う backend 設定を追加する
- central realtime の会話 backend に `lmstudio_gemma4_e4b` を採用し、既存 Gemma E2B / LFM fallback を崩さない
- config / router / LM Studio backend の unit test で採用モデルを固定する

### やったこと
- `config/central_realtime.toml` に `lmstudio_gemma4_e4b` backend を追加した
- active `conversation_backend` を `lmstudio_gemma4_e4b` に切り替えた
- `conversation_fallback` を `local_gemma4_e2b_mlx` にし、LM Studio 側が落ちた/遅い場合も Gemma 系 local fallback に留めた
- config / router の unit test を E4B 採用前提へ更新した
- README / MEMORY.md / `_docs/latency.md` に採用モデルと smoke 実測を追記した

### 詰まったこと・解決したこと
- 既存の LM Studio backend 実装は OpenAI 互換 SSE に対して十分汎用だった
  → Python 実装は増やさず、backend spec と契約テストの追加に限定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_router.py tests/unit/test_lm_studio_backend.py`
  - 16 passed
- `.venv/bin/python -m pytest -m unit`
  - 326 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- LM Studio E4B short smoke
  - backend: `lmstudio_gemma4_e4b`
  - model: `gemma-4-e4b-it-mlx`
  - first delta 313.5ms / total 314.6ms
  - output: `はい。`

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、E4B の応答品質・口調・first audio 体感を確認する

## 2026-05-25 セッション41

### やること（開始時に書く）
- 現行 `local_whisperkit_serve_large` が実サンプルで空文字を返す問題を受け、STT backend を MLX Whisper large turbo q4 へ切り替える
- `local_whisper_mlx_large_turbo_q4` を設定に追加し、config 契約テストで固定する
- HF Hub の未認証 download と有料 plan / token の関係を確認して説明する

### やったこと
- `config/central_realtime.toml` の active `stt_backend` を `local_whisper_mlx_large_turbo_q4` に変更した
- `local_whisper_mlx_large_turbo_q4` backend を追加し、`mlx-community/whisper-large-v3-turbo-q4` を使うようにした
- config 契約テストを MLX large turbo q4 前提に更新した

### 詰まったこと・解決したこと
- `local_whisperkit_serve_large` は実サンプルで空文字を返し、会話ログ上でも partial は出るのに final が空文字になる挙動があった
  → WhisperKit serve large は一旦採用から外し、実測で精度と速度のバランスが良かった MLX large turbo q4 を採用する
- HF Hub の未認証 download は匿名アクセス扱いで rate limit が低い
  → まず HF_TOKEN を渡すだけでも匿名より上限が上がり、さらに PRO / Team / Enterprise で上限が上がる
- 実ブラウザ体感では STT 品質が劇的に改善し、会話として成り立ち始めている感触があった
- 「ココココ」系の hallucination っぽい出力時に GPU が完全にフルに使われていた
  → large 系 decoder が無音/ノイズ/短い断片で粘っている可能性があるため、次は STT 前の segment 抑制や no-speech 系閾値を確認する

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_stt_backends.py`
  - 16 passed
- `mise exec -- uv run ruff check config/central_realtime.toml tests/unit/test_phase0_config.py`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 300 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` を再起動し、`local_whisper_mlx_large_turbo_q4` の startup warm-up と実会話 transcript を確認する

## 2026-05-25 セッション42

### やること（開始時に書く）
- ルート直下に git 管理外の `work/` を切り、実環境ノイズや読み上げ評価の録音 artifact を保存できるようにする
- 既存 `/ws` の上で debug 録音を開始/停止し、今の UI から noise / read-aloud 録音を取れるようにする
- 読み上げ用の文章を UI に出し、録音後に configured STT の transcript と処理時間を返す

### やったこと
- `.gitignore` に `work/` を追加した
- `DebugAudioRecorder` を追加し、`work/audio-recordings/<recording_id>.wav` と `.json` metadata を保存するようにした
- `/ws` に `debug_recording_start` / `debug_recording_stop` JSON event を追加した
- 録音中の audio chunk は通常の `TomoroSession.process_audio_chunk()` へ流さず、debug recorder にだけ渡すようにした
- 読み上げ評価録音では、保存した audio を configured STT にかけて transcript / STT elapsed を UI と metadata に返すようにした
- 現行 UI に `Noise 1s` / `Read 5s` / `Next` ボタン、読み上げ文、debug 結果欄を追加した
- `tests/unit/test_debug_recording.py` と `/ws` debug recording unit test を追加した

### 詰まったこと・解決したこと
- フル unit で `test_phase106_initiative_policy.py` の候補期限が 2026-05-25 12:10 固定になっており、現在時刻では expired 判定になっていた
  → テスト候補の `created_at` を `datetime.now(UTC)` にし、常に未来の `expires_at` になるように修正した

### 検証
- `node --check client/main.js`
  - pass
- `mise exec -- uv run pytest -m unit tests/unit/test_debug_recording.py tests/unit/test_phase1_echo.py`
  - 6 passed
- `mise exec -- uv run ruff check server/edge/debug_recording.py server/edge/main.py tests/unit/test_debug_recording.py tests/unit/test_phase1_echo.py`
  - pass
- `mise exec -- uv run pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_debug_recording.py tests/unit/test_phase1_echo.py`
  - 19 passed
- `mise exec -- uv run pytest -m unit`
  - 303 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` のブラウザで `Noise 1s` と `Read 5s` を実行し、`work/audio-recordings/` の WAV/JSON と UI transcript を確認する

## 2026-05-25 セッション36

### やること（開始時に書く）
- `vad_silence_ms` を 1000ms に変更し、ユーザー発話の途中で返答へ入りにくくする
- 単なる待機ではなく、返答がまだ表示/音声出力されていない段階で新しい `listening` が来たら stale reply としてキャンセルし、人間の続き発話を優先する
- STT backend を `local_whisperkit_serve_small` に切り替え、WhisperKit serve/CoreML 経路で確認できるようにする

### やったこと
- `config/central_realtime.toml` の `vad_silence_ms` を 1000ms に変更した
- central runtime の `stt_backend` を `local_whisperkit_serve_small` に変更した
- `TomoroSession` に未出力 reply の stale cancel を追加した
  - 新しい `listening` が来た時、reply text / emotion / audio がまだ出ていなければ古い reply task をキャンセルする
  - すでに出力が始まった reply は既存の barge-in / stop-intent 制御に任せる
- config 契約と stale cancel の unit test を追加した

### 詰まったこと・解決したこと
- 最初は「少し待つ」案に見えたが、小手先対応になるため否定した
  → session 管制として、未出力の古い reply を stale result として捨てる方針にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_streaming_tts_pipeline.py tests/unit/test_phase0_config.py tests/unit/test_stt_backends.py`
  - 19 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 297 passed, 17 deselected
- `TOMOKO_STT_BENCH_BACKENDS=local_whisperkit_serve_small mise exec -- uv run pytest -m perf --tb=short tests/perf/test_stt_latency.py -s`
  - 1 passed
  - warm 7003.5ms / measured 211.9ms

### 次のセッションでやること
- `make server-debug` の実ブラウザ確認で、発話途中の分割返答が減ったか確認する
- WhisperKit serve 切り替え後の mactop ANE/CPU/GPU の見え方を実機で確認する

## 2026-05-27 セッション14

### やること（開始時に書く）
- ARCHITECTURE.md / PLAN.md / LOG.md / MEMORY.md を参照し、Tomoko の進捗・判断・実装の流れを読み物としてまとめる
- レポート兼記録として読める同人誌っぽい HTML を `work/` 配下に生成する
- 既存の計画・判断ファイルは source of truth として変更せず、今回の成果物は artifact として隔離する

### やったこと
- `work/tomoko-progress-doujin-report-20260527.html` を追加した
- README / ARCHITECTURE / PLAN / LOG / MEMORY と `_reference/` の旧 Unity / REST 実装を踏まえ、Tomoko の進捗・判断・実装を制作記録風の章立てでまとめた
- M1 音声経路、M2 記憶構造、TomoroSession の stateful control core、自発発話、backend trace、turn-taking judge までを読み物として整理した

### 詰まったこと・解決したこと
- `work/` は git 管理外 artifact なので `git status` には HTML が出ない
  - 解決: 成果物パスを LOG と最終報告で明示する

### 検証
- `wc -l work/tomoko-progress-doujin-report-20260527.html`
  - 865 lines
- `rg -n "<h1>|Chapter 10|参照したローカル文書|Tomoko 制作記録" work/tomoko-progress-doujin-report-20260527.html`
  - title / h1 / Chapter 10 / appendix heading を確認
- `git diff --check -- LOG.md`
  - pass

### 次のセッションでやること
- 実装作業に戻る場合は Phase 10.10.4 / Phase 10.11.4 の実ブラウザ評価から再開する

# LOG.md

実装セッションの時系列ログ。セッションをまたいだ引き継ぎのために書く。

---

## 2026-05-28 セッション6

### やること（開始時に書く）
- STOP/START UI で会話をぶつ切りした時も、会話 session が summarizer 対象へ進むようにする
- `/ws` adapter が DB close を直接実行する設計は避け、Stop/Disconnect の事実を `SessionEvent` として `TomoroSession` に渡す
- `TomoroSession` が final owner として active conversation session を `ui_stop` / `client_disconnect` で close する
- 先に unit test を追加し、transport lifecycle が store 直叩きではなく session event 経由で close されることを固定する

### やったこと
- PLAN.md に Phase 8.6.1 client lifecycle による session close を追記し、完了チェックを更新した
- UI Stop が WebSocket close 前に `{"type":"client_stop"}` を送るようにした
- `/ws` adapter が `client_stop` を `SessionEvent(type="client_stop_requested")` へ変換するようにした
- WebSocket disconnect 時は `_connection_registry.unregister()` 後の snapshot を `connected_output_state_changed` として `TomoroSession` へ戻すようにした
- `TomoroSession.apply_client_lifecycle_event()` を追加し、lifecycle event の internal command として active conversation session を close するようにした
- active session は UI Stop では `end_reason="ui_stop"`、connected client 0 の disconnect では `end_reason="client_disconnect"` で閉じる
- MEMORY.md に、Stop/Disconnect は SessionEvent 経由で conversation session を閉じる判断を追記した

### 詰まったこと・解決したこと
- 最初に考えた `/ws` disconnect から store を直接 close する案は、TomoroSession の session lifecycle 所有境界を壊すため採用しなかった
  - 解決: adapter は transport 事実だけを event 化し、close の判断と DB 更新は TomoroSession の internal command に寄せた
- `client_stop` event の unit test は `SessionEvent` の timestamp まで比較して落ちた
  - 解決: event type / payload の契約だけを検証する形にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase85_conversation_sessions.py tests/unit/test_phase1_echo.py -q`
  - 13 passed
- `node --check client/main.js`
  - pass
- `.venv/bin/python -m ruff check server/session.py server/edge/main.py tests/unit/test_phase85_conversation_sessions.py tests/unit/test_phase1_echo.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 372 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで Stop/Start を数回行い、`conversation_sessions` が `end_reason='ui_stop'` / `summary_status='pending'` へ進むことを確認する
- `make session-summarizer-once` で Stop 由来 session が `completed` になることを DB と `logs/session-summarizer.log` で確認する

## 2026-05-28 セッション5

### やること（開始時に書く）
- PLAN.md に、会話セッション内で一度 deep retrieval した長期記憶を短期 carryover として保持する Phase を追記する
- `TomoroSession` が active conversation session の作業メモとして retrieval carryover を持ち、次の短い follow-up でも prompt に渡せるようにする
- carryover は source of truth ではなく、source id / hash / 文字列 budget で dedupe / eviction し、ログから挙動を追えるようにする
- 先に regression test を追加し、`著作権の話とか覚えてる` の次の `どういう風に考えてたっけ` で retrieval が維持されることを固定する

### やったこと
- PLAN.md に Phase 8.8.6 session retrieval carryover を追記し、完了チェックを更新した
- `TomoroSession` に session-local の retrieved context carryover を追加した
  - fresh retrieval と carryover を `ThinkingInput.long_term_memory` へ merge する
  - fast follow-up でも、直前 deep retrieval の `session_summaries` / `memory_hits` が prompt に残る
  - fresh retrieval がある時は carryover に記録し、次 turn 以降は DB 再検索なしで再利用する
- `MemoryHit.source_id` を追加し、session summary 由来の memory は `session_summary:<session_id>` で dedupe できるようにした
- turn-level hit は speaker / timestamp / normalized text hash で dedupe するようにした
- carryover は最大 6 entry / 900 文字に収め、超過時は古い / 低 similarity entry から落とすようにした
- session close 時に carryover を clear するようにした
- `carryover_added` / `carryover_used` / `carryover_evicted` / `carryover_cleared` の debug log を追加した
- regression test を追加した
  - deep retrieval した memory が次の短い follow-up に渡る
  - fresh retrieval と carryover が重複しない
  - text budget 超過で古い entry が落ちる
  - session close で clear される

### 詰まったこと・解決したこと
- 最初は `MemoryHit` だけでは session summary の source id を保持できず、text hash だけの dedupe になっていた
  - 解決: `MemoryHit.source_id` を任意 field として追加し、summary 変換時に `session_summary:<session_id>` を入れるようにした
- `ContextSnapshotBuilder` の read cache に混ぜる案もあったが、prompt 内容に影響するため `TomoroSession` の active session 作業メモとして扱う方針にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase88_context_snapshot.py -q`
  - 14 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase85_conversation_sessions.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase105_session_runtime.py -q`
  - 32 passed
- `.venv/bin/python -m pytest -m unit`
  - 368 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで `著作権の話とか覚えてる` に続けて `どういう風に考えてたっけ` を試し、`carryover_used` と `ThinkFastMode llm_prompt` を確認する

## 2026-05-28 セッション1

### やること（開始時に書く）
- README.md の古い構成・開発状況・default backend 記述を、現行の Tomoko 実装状態に合わせて全体更新する
- ユーザー指示により md ファイルの通常更新制限は一時解除されているため、README.md は追記ではなく本文を整理し直す
- 既存の AGENTS / MEMORY / LOG / PLAN / ARCHITECTURE / _reference の判断から外れないようにする

### やったこと
- README.md を現行 runtime の入口として全面更新した
- 古い「M1 実装中 / M2 未着手」前提を、M1/M2 実装済み、M3 実装中、M4 一部実装という状態へ修正した
- default backend を `config/central_realtime.toml` に合わせて更新した
  - 会話 LLM: `lmstudio_gemma4_26b_a4b`
  - fallback: `local_gemma4_e2b_mlx`
  - STT: `local_apple_speech_ja`
  - TTS: `voicevox_tsumugi`
  - embedding: `local_bge_m3`
- `TomoroSession` / `ContextSnapshotBuilder` / `InferenceRouter` / background worker / PostgreSQL tables / debug logs の現在形を README に整理した
- よく使う Makefile command、background worker command、test command、外部観察 pipeline、`_reference/` の意味を更新した

### 詰まったこと・解決したこと
- README の既存記述には Gemma E4B や MLX Whisper small など、現行 config とズレた記述が残っていた
  - 解決: `config/central_realtime.toml` と Makefile を現物確認し、README 側を合わせた
- 通常の markdown 追記制限は、今回のユーザー指示により一時解除として扱った

### 検証
- `git diff --check -- README.md LOG.md`
  - pass
- `rg -n "M1 \\| 話せるTomoko \\|.*実装中|M2 \\| 記憶があるTomoko \\| 未着手|Gemma 4 E4B|MLX Whisper small|logs/server\\.log|🚧|未着手" README.md`
  - M5 の未着手記述のみ残存
- `make test-unit`
  - 359 passed, 17 deselected

### 次のセッションでやること
- README の運用導線をさらに詰めるなら、`make server-debug` 起動前に必要な LM Studio / VOICEVOX / Apple Speech 権限チェックを短い troubleshooting として追加する

## 2026-05-27 セッション15

### やること（開始時に書く）
- 明示的な記憶想起発話で deep context が 100ms timeout し、長期記憶が空になる問題を修正する
- ContextSnapshotBuilder 内で query embedding を共有し、session summary search と turn memory search の二重 embedding を避ける
- session summary を turn memory より優先し、明示的想起時の context budget を現実的な値へ上げる
- source 別 timing / skipped reason / cache / source error がログから追えるようにする

### やったこと
- `ContextSnapshotBuilder` で query embedding を build 単位に共有し、session summary search と turn memory search が同じ `embed_query()` 結果を使うようにした
- `query_embedding` を context trace / cache trace / stage timing の独立 source として記録するようにした
- `prioritize_session_summaries=True` を `ContextBuildPolicy` に追加し、turn memory search は session summary search 完了後に走るようにした
- `ContextBuildTrace` に `skipped_reasons` を追加し、timeout / missing store などの理由をログで読めるようにした
- `ContextSnapshotBuilder` の info log に `stage_timings_ms` / `skipped_reasons` / `source_errors` を出すようにした
- 明示的な記憶 cue（`この前` / `覚えてる` / `話してた` など）で deep に入った時だけ、context budget を 300ms に上げるようにした
- 通常の長文 deep は従来どおり `ContextBuildPolicy.for_depth("deep")` の 100ms budget を維持した

### 詰まったこと・解決したこと
- SQL 自体は実測で 1ms 前後だったため、budget 問題の主因は DB ではなく query embedding 生成と二重実行だった
  - 解決: 同一 build 内の query embedding を共有し、source timing と skipped reason をログ化した
- `memory_hits` と `session_summaries` を完全並列にすると、summary が間に合わないまま両方 timeout しやすい
  - 解決: 明示的な想起ではまず session summary を優先し、turn memory は補助に回す方針を code contract にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase88_context_snapshot.py -q`
  - 10 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py -q`
  - 16 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase4_thinking.py tests/unit/test_phase88_context_snapshot.py -q`
  - 46 passed
- `.venv/bin/python -m pytest -m unit`
  - 359 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` の実ブラウザで「この前話していたAIの話って覚えてる？」を再試行し、`ContextSnapshotBuilder` log の `query_embedding` / `session_summaries` / `memory_hits` timings と hit counts を確認する

## 2026-05-27 セッション1

### やること（開始時に書く）
- LM Studio の自動ロード前提で Gemma 4 E4B / 26B / 31B 候補を同一プロンプトで叩く
- first token / total latency と、返答の意味性・会話の踏み込みを比較する
- Tomoko の default 会話モデルを大きくした時の効果とリスクを判断する

### やったこと
- LM Studio `/v1/models` で `gemma-4-e4b-it-mlx` / `gemma-4-26b-a4b-it-mlx` / `gemma-4-31b-it-mlx` が利用可能なことを確認した
- Tomoko の base persona 相当 prompt と同一会話 context で、3モデルの streaming latency と返答内容を比較した
- 会話後処理 worker 相当の「会話から知見を抽出する」prompt でも、3モデルの出力を比較した

### 詰まったこと・解決したこと
- 31B は短い音声返答では first content が 0.9〜1.4s、total が 2.3〜2.9s になり、hot path には重い
- 26B A4B は初回 model switch / load では約10sかかったが、ロード直後の短い会話返答は first content 0.28〜0.33s、total 0.48〜0.63s で E4B と同程度だった
- 短い音声返答では 31B の品質優位は小さく、26B A4B の方が速度・意味性のバランスが良かった
- 会話後処理の知見抽出では、E4B より 26B / 31B の方が「意味ある会話=新しい知見」「即応の口と後で考える頭の分離」などを記憶候補として具体化できた

### 次のセッションでやること
- hot path 全面置換ではなく、まず deep / background role だけ `gemma-4-26b-a4b-it-mlx` に向ける構成を検討する
- LM Studio 同一 URL semaphore のため、background 26B/31B が conversation E4B を塞がないよう queue / process 分離を検討する

## 2026-05-27 セッション2

### やること（開始時に書く）
- LM Studio の実ログを見ながら Gemma 4 31B / 26B / E4B に同一リクエストを投げる
- formatted input / output に `<|think|>` / thought channel / reasoning field が出ているか確認する
- 31B の遅さが thinking 由来か、モデルサイズ・ロード・生成速度由来かを切り分ける

### やったこと
- `lms log stream --source model --json --stats` を開き、LM Studio の formatted input / output を直接確認した
- `gemma-4-31b-it-mlx` に `"think": false`、`think` 省略、`"think": true` の3パターンを投げた
- system prompt 先頭に `<|think|>` を明示したパターンも投げた

### 詰まったこと・解決したこと
- 31B の formatted input はどのパターンでも末尾が `<|channel>thought\n<channel|>` になった
  - これは Gemma 4 model card の「thinking disabled 時も空 thought block tag を出す」挙動に近い
  - 実 output は本文のみで、thought 本文は生成されなかった
- OpenAI 互換 streaming delta でも `reasoning` / `reasoning_content` / `thinking` field は出なかった
- `"think": true` はこの LM Studio / Gemma 4 MLX 経路では formatted input を変えなかった
- `<|think|>` を system prompt に明示しても、実 output は `17 × 23 = 391` のような本文だけで thought は出なかった

### 結論
- 今回の実測ログ上、`gemma-4-31b-it-mlx` が長い thinking を生成して遅くなっている証拠はない
- 31B の 1秒台 first content は、hidden thinking よりモデルサイズ / MLX decode 速度 / prompt 処理の影響と見るのが妥当
- LM Studio の Gemma 4 template は空 thought channel prefix を入れるが、これは no-think 用の assistant prefix と判断する

## 2026-05-27 セッション3

### やること（開始時に書く）
- Tomoko と数ターン会話済みの状態を dummy messages と補助文脈で再現する
- E4B / 26B A4B / 31B が文脈を使って意味のある返答を返せるか比較する

### やったこと
- base persona に、会話から得られた補助文脈を足した system prompt を作った
- 「意味のある会話」「朝レビュー・夜設計」「即応の口と後で考える頭」の3シナリオを messages として投げた
- 3モデルの latency と返答内容を比較した

### 結論
- E4B は速いが、踏み込み要求に対して「情報が足りない」に逃げやすい
- 26B A4B は「作業中の会話」と「振り返りの外の会話」が混ざっている、という盲点を返せており、意味性が最も良かった
- 31B は初回ロード/切替が約16s、ロード後も first content 約1.3〜1.5s / total 約2.7〜3.5s で重い
- 31B の返答は悪くないが、26B A4B より明確に優れてはいなかった

## 2026-05-27 セッション4

### やること（開始時に書く）
- 明日の実機会話比較のため、central realtime の active conversation backend を Gemma 4 26B A4B MLX に切り替える
- E4B は比較・復帰用 backend として残す
- latency を犠牲にしても 26B を試せるよう、config / contract test / docs の期待値を更新する

### やったこと
- `config/central_realtime.toml` の `conversation_backend` を `lmstudio_gemma4_26b_a4b` に変更した
- `lmstudio_gemma4_26b_a4b` backend を追加し、model を `gemma-4-26b-a4b-it-mlx` にした
- 26B の live trial を妨げないよう、`max_latency_ms` は 5000ms に広げた
- `lmstudio_gemma4_e4b` は比較・復帰用として残した
- README / MEMORY.md / `_docs/latency.md` に 26B 採用理由と未実測事項を追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_router.py`
  - 12 passed
- `.venv/bin/python -m pytest -m unit`
  - 334 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を走らせ、26B の first text / first audio / 会話の意味性を確認する
- 戻す場合は `config/central_realtime.toml` の `conversation_backend` を `lmstudio_gemma4_e4b` に戻す

## 2026-05-25 セッション51

### やること（開始時に書く）
- VOICEVOX Engine の stream / cancellable synthesis 対応状況を確認する
- 既存ブラウザ再生契約を壊さずに使える `voicevox_stream` TTS backend を追加する
- central / edge の default TTS を stream 版 VOICEVOX へ切り替え、unit test で固定する

### やったこと
- `VoicevoxStreamBackend` を追加し、VOICEVOX Engine の `/cancellable_synthesis` を優先して使うようにした
- 実行中の VOICEVOX Engine 0.25.2 では experimental feature が default 無効で `/cancellable_synthesis` が 404 になるため、`/synthesis` fallback を入れた
- Tomoko の現ブラウザは binary chunk ごとに `decodeAudioData()` するため、backend は完全な WAV chunk を返す契約を維持した
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の `tts_backend` を `voicevox_tsumugi_stream` に変更した
- README / MEMORY / `_docs/latency.md` に stream endpoint と fallback の扱いを記録した

### 詰まったこと・解決したこと
- OpenAPI 上は `/cancellable_synthesis` が存在するが、実 AudioQuery では `{"detail":"実験的機能はデフォルトで無効になっています。使用するには引数を指定してください。"}` で 404 になった
  → 現環境では fallback を必須とし、真の部分音声再生は PCM framing / client playback 変更の別作業に切り出す

### 検証
- `curl -sS --max-time 3 http://127.0.0.1:50021/openapi.json`
  - `/cancellable_synthesis` の存在を確認
- `.venv/bin/python -m pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase14_edge_split.py`
  - 17 passed
- `.venv/bin/python -m ruff check server/shared/inference/tts/voicevox.py tests/unit/test_voicevox_tts.py server/shared/inference/tts/__init__.py tests/unit/test_phase14_edge_split.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 327 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- 実 smoke
  - text: `うん、わかった。少し待ってね。`
  - first chunk 347.5ms / total 347.6ms / 24kHz mono / 2837.3ms audio
  - output: `logs/voicevox-tsumugi-stream-smoke.wav`

## 2026-05-25 セッション50

### やること（開始時に書く）
- VOICEVOX Engine の stream / cancellable synthesis 対応状況を確認する
- 既存ブラウザ再生契約を壊さずに使える `voicevox_stream` TTS backend を追加する
- central / edge の default TTS を stream 版 VOICEVOX へ切り替え、unit test で固定する

## 2026-05-25 セッション49

### やること（開始時に書く）
- 起動済み `voicevox.app` / VOICEVOX Engine を使う TTS backend を追加する
- 春日部つむぎの speaker/style を default にし、Tomoko の `tts_backend` として設定で使えるようにする
- VOICEVOX 側の推論は外部 app / Engine に任せ、Tomoko 側では GPU/MLX TTS を使わない HTTP adapter として実装する

### やったこと
- `VoicevoxBackend` を追加し、VOICEVOX Engine の `/audio_query` / `/synthesis` を叩いて WAV を返す TTS backend にした
- 春日部つむぎの speaker id `8` を default にし、`春日部つむぎ` / `春日つむぎ` / `tsumugi` alias でも指定できるようにした
- `config/central_realtime.toml` と `config/edge_kitchen.toml` の `tts_backend` を `voicevox_tsumugi` に変更した
- README に、VOICEVOX は起動済み外部 Engine を使い、本体・音源・出力音声は各規約に従うことを追記した
- `_docs/latency.md` に `voicevox.app` 実 smoke の first / total latency を記録した

### 詰まったこと・解決したこと
- `voicevox.app` は `127.0.0.1:50021` で `/speakers` に応答しており、春日部つむぎは speaker id `8` だった
- Tomoko 側では GPU/MLX TTS を起動せず、CPU 側で動いている外部 VOICEVOX Engine へ HTTP で依頼する形にした

### 検証
- `curl -sS --max-time 2 http://127.0.0.1:50021/speakers`
  - `春日部つむぎ` / `id=8` を確認
- `.venv/bin/python -m pytest -m unit tests/unit/test_voicevox_tts.py tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py`
  - 18 passed
- `.venv/bin/python -m pytest -m unit`
  - 324 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- 実 smoke
  - text: `うん、わかった。少し待ってね。`
  - first chunk 364.7ms / total 364.9ms / 24kHz mono / 2837.3ms audio
  - output: `logs/voicevox-tsumugi-smoke.wav`

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を起動し、VOICEVOX TTS の体感・音質・回り込みを確認する
- 長文で first audio が遅い場合は、TTS flush 単位や VOICEVOX audio_query の pause / speed 調整を見直す

## 2026-05-25 セッション40

### やること（開始時に書く）
- stop-intent worker で rule / embedding signal を先に保存し、LLM classifier 失敗だけで observation 全体を error にしない
- LM Studio 500 などの LLM 側一時失敗を degraded optional として扱う unit test を追加する

### やったこと
- `StopIntentClassifierWorker` が rule / embedding signal を保存・emit してから optional LLM classifier を実行するようにした
- LLM classifier が例外を出した場合、`method="llm"` / `predicted_kind="none"` / `confidence=0.0` の degraded signal を保存し、observation は `completed` にするようにした
- LLM 失敗時にも rule / embedding / degraded LLM signal が残る unit test を追加した

### 詰まったこと・解決したこと
- LM Studio 500 は stop-intent の補助 LLM だけの失敗だったため、deterministic な rule signal を巻き込まない処理順へ変更した

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_intent_queue.py`
  - 5 passed
- `mise exec -- uv run ruff check server/gateway/stop_intent.py tests/unit/test_stop_intent_queue.py`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 300 passed, 17 deselected

### 次のセッションでやること
- 実 `make server-debug` で LM Studio 500 が再発した時、`stop_intent_observations.status=completed` と degraded LLM signal が残ることを DB で確認する

## 2026-05-25 セッション35

### やること（開始時に書く）
- 外部観測 interpretation に `tomoko_private_reaction` と `candidate_seed_text` を追加する
- thinker / journalist が一般要約ではなく Tomoko の内心反応と発話候補の種を使えるようにする
- 既存 DB に DDL を反映し、実データを再 interpretation する

### やったこと
- `world_observation_interpretations` に `tomoko_private_reaction` / `candidate_seed_text` を追加した
- `WorldObservationInterpretation` / store / `world_observation_trace` / DB integration test を新フィールドに対応させた
- interpreter の structured output schema と prompt に、内心メモと自発発話候補の種を必須フィールドとして追加した
- thinker の world observation source は `candidate_seed_text` を優先し、なければ `tomoko_private_reaction` / `interpretation_text` に fallback するようにした
- journalist input は `tomoko_private_reaction` と `candidate_seed_text` を diary material に含めるようにした
- 再実行前後の SELECT を `logs/world-observation-observe/2026-05-25-before-private-reaction-rerun-fixed.json` と `logs/world-observation-observe/2026-05-25-after-private-reaction-rerun-fixed.json` に保存した

### 詰まったこと・解決したこと
- 既存 DB へ `CREATE OR REPLACE VIEW world_observation_trace` を適用した時、view の既存列 `reason_json` の位置に `tomoko_private_reaction` を挿入する形になり PostgreSQL が拒否した
  → `DROP VIEW IF EXISTS world_observation_trace` してから view を作り直す DDL にした
- view 更新失敗後に interpretation delete が走ったため、一時的に DB 上の interpretation は 0 件になった
  → DDL 修正後に再度 `make information-interpret-once` を実行し、10 件生成し直した

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko -v ON_ERROR_STOP=1 < docker/postgres/init/013_world_observations.sql`
- `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
  - `296 passed, 17 deselected`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase180_world_observations_db.py tests/integration/test_phase87_persona_snapshots_db.py`
  - `2 passed`
- `git diff --check`

### 観測
- 実DBの `world_observation_trace` で `tomoko_private_reaction` と `candidate_seed_text` が埋まることを確認した
- 例: `MLXの統合の話、少しだけ気になったよ。` のような短い発話種が生成された

### 次のセッションでやること
- 反応がまだ硬い場合は、`tomoko_private_reaction` の文体制約をさらに会話寄りにし、`candidate_seed_text` を候補生成の scoring に組み込む

## 2026-05-25 セッション34

### やること（開始時に書く）
- 外部観測 interpretation が一般要約に寄りすぎている問題を補正する
- `reason_json` に `persona_basis` / `user_basis` / `speakability_basis` / `avoid_overclaim` を必須化する
- `speakability_hint` を `short_now` / `later` / `diary` / `avoid` の enum にする
- interpreter prompt に `base_persona.md` 本文を渡す
- 初期 persona snapshot seed を DB に入れ、外部観測を再 interpretation して反応を見る

### やったこと
- `WorldObservationInterpretation.from_json()` で `speakability_hint` enum と `reason_json` 必須キーを検証するようにした
- LM Studio structured output schema でも `speakability_hint` enum と `reason_json` 必須キーを強制した
- 外部観測 interpreter prompt に `base_persona.md` 本文を追加し、口調コピーではなく関心・距離感の判断材料として使うよう明記した
- `_tools/seed_initial_persona_snapshot.py` と `make persona-seed-initial` を追加した
- initial seed として warmth / curiosity / restraint / local inference / MLX / voice interaction / life texture を persona snapshot に入れた
- 再実行前後の外部観測 interpretation を `logs/world-observation-observe/2026-05-25-before-rerun.json` と `logs/world-observation-observe/2026-05-25-after-rerun.json` に保存した

### 詰まったこと・解決したこと
- 最初の seed tool は `_tools` 配下から実行した時に `server` import path が通らなかった
  → 既存 `_tools` と同じく repo root を `sys.path` に追加した
- `COPY TO STDOUT` で保存した JSON が `\n` escaped の1行になった
  → 観測用ファイルとして読みやすいよう pretty JSON へ整形し直した

### 検証
- `make persona-seed-initial`
  - `persona_seed inserted state_id=d4fcf6a7-8937-4f13-94c6-67b0d07445e3 lexicon_id=aae8f139-4158-42c4-8e53-0c066429ffcf`
- `docker exec tomoko-postgres psql ... DELETE FROM world_observation_interpretations`
- `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- `docker exec tomoko-postgres psql ... SELECT count(*) ... FROM world_observation_interpretations`
  - `interpretations = 10`, `with_state = 10`, `with_lexicon = 10`
- `mise exec -- uv run pytest -m unit`
  - `296 passed, 17 deselected`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase180_world_observations_db.py tests/integration/test_phase87_persona_snapshots_db.py`
  - `2 passed`
- `mise exec -- uv run ruff check .`

### 観測
- `reason_json` は4キーが入り、persona / user / speakability / overclaim の根拠が分離された
- `interpretation_text` は「少しだけ気になる」「後で会話の種として置く」など、以前より Tomoko の距離感が出た
- ただしまだ全体としては控えめで、強い個性というより「静かに受け取る」方向に寄っている

### 次のセッションでやること
- もっと面白くするなら、`tomoko_private_reaction` や `candidate_seed_text` のような発話候補寄りの別フィールドを追加する

## 2026-05-25 セッション33

### やること（開始時に書く）
- Phase 18 の外部観測 interpreter に、Tomoko が何者かを短く示す system prompt grounding を追加する
- `tomoko_interest` / `relevance_to_user` / `speakability_hint` が一般的なアシスタント像ではなく Tomoko の関心で解釈されることを unit test で固定する
- `base_persona.md` を短い core persona として厚くし、persona snapshot の扱いルールと serialized JSON を LLM prompt に常に流す
- persona snapshot が 0 件でも、空状態を表す JSON fallback を prompt に入れる
- 既存の外部観測レコードを削除し、実 Markdown を再 ingest / interpret する

### やったこと
- `WorldObservationInterpreter` の system prompt に `Tomoko profile` を追加した
- Tomoko を「一人のユーザーと暮らすローカル推論ベースの日本語音声対話システム」として短く定義した
- ユーザーとの関係、関心領域、ニュース解説者ではないこと、話題候補としての出し方を prompt に明記した
- interpreter unit test で grounding が system prompt に含まれることを固定した
- `prompts/base_persona.md` に、Tomoko のあたたかさ、好奇心、遊び心、遠慮深さ、ユーザーとの関係性を短く追記した
- `server/shared/persona_prompt.py` を追加し、persona snapshot の扱いルールと empty fallback JSON を一箇所にまとめた
- 会話 prompt は persona slice / lexicon terms を serialized JSON として常に渡すようにした
- 外部観測 interpreter は最新 persona snapshot 全体、または空 fallback JSON を system prompt に渡すようにした

### 詰まったこと・解決したこと
- structured output は JSON 形状を固定できるが、Tomoko が何者かという意味の grounding は別途必要だった
  → normalizer ではなく、Tomoko の関心を採点する interpreter にだけ短い profile を入れる形にした
- DB に persona snapshot が 0 件の時、prompt から persona 情報セクション自体が消えると LLM が一般アシスタント像で補完しやすい
  → 空の snapshot JSON を明示し、「まだ学習済みデータがない」という状態を渡す形にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_world_observation_interpreter.py tests/unit/test_lm_studio_backend.py tests/unit/test_world_observation_normalizer.py`
- `mise exec -- uv run ruff check server/world_observations/interpreter.py tests/unit/test_world_observation_interpreter.py server/shared/inference/backends/lm_studio.py server/world_observations/normalizer.py tests/unit/test_lm_studio_backend.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_world_observation_interpreter.py tests/unit/test_phase87_persona_snapshots.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py`
- `mise exec -- uv run ruff check prompts/base_persona.md server/shared/persona_prompt.py server/gateway/thinking/fast.py server/world_observations/interpreter.py tests/unit/test_world_observation_interpreter.py tests/unit/test_phase87_persona_snapshots.py`
- `docker exec tomoko-postgres psql ... DELETE FROM world_observation_documents WHERE raw_file_path = 'informations/work/2026-05-25-world-observation.md'`
- `make information-ingest-once`
  - `world_observation_ingest processed=1 archived=1 failed=0 skipped=0`
- `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- `docker exec tomoko-postgres psql ... SELECT status, count(*) FROM world_observation_documents GROUP BY status`
  - `completed = 1`
- `docker exec tomoko-postgres psql ... SELECT count(*) ... FROM world_observation_interpretations`
  - `interpretations = 10`, `with_state = 0`, `with_lexicon = 0`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
  - `296 passed, 17 deselected`

### 次のセッションでやること
- 必要なら `make information-interpret-once` の実データ出力を見て、`tomoko_interest` / `relevance_to_user` の偏りを確認する

## 2026-05-25 セッション32

### やること（開始時に書く）
- セッション31の Perplexity / Computer Use 実行結果を踏まえ、`informations/prompts/daily_world_observation.md` を更新する
- Markdown artifact と frontmatter delimiter が安定するように prompt を明確化する

### やったこと
- `daily_world_observation.md` に成果物名、Markdown document 出力、code fence 禁止を明記した
- frontmatter delimiter は `---` 固定とし、`***` や水平線で代用しないよう明記した
- 本文構造を topic heading / 事実 / 推測・含意 / source_hint に寄せた
- Perplexity の copy button ではなく `Markdown形式でダウンロード` を使う前提を prompt 内にも追記した

### 詰まったこと・解決したこと
- セッション31では copy button 由来の Markdown が画面表示向けに整形されることがあった
  → prompt 側でも download 用 Markdown として成立する形を優先するようにした

### 検証
- `git diff -- informations/prompts/daily_world_observation.md LOG.md` で変更範囲を確認した

### 次のセッションでやること
- 次回の Perplexity 実行で artifact title / frontmatter / source_hint が安定するか確認する

## 2026-05-25 セッション31

### やること（開始時に書く）
- `informations/prompts/daily_world_observation.md` を使って Computer Use 経由で Perplexity に外部観測レポートを依頼する
- 得られた Markdown を `informations/work/2026-05-25-world-observation.md` に保存する
- 保存後に `make information-ingest-dry-run` を実行し、受け入れ可否と問題点を確認する

### やったこと
- Computer Use で Perplexity に外部観測レポートを依頼し、Markdown 形式で成果物をダウンロードした
- ダウンロードした `world_observation_2026-05-25.md` を `informations/work/2026-05-25-world-observation.md` に保存した
- 保存した Markdown に対して validator と ingest dry-run を実行した

### 詰まったこと・解決したこと
- Computer Use の `type_text` は長い日本語 prompt の入力が崩れた
  → Chrome の入力欄へ clipboard 経由で prompt を入れて送信した
- Perplexity の copy button で取れる Markdown は画面表示向けに整形されることがあった
  → download menu の `Markdown形式でダウンロード` を使い、frontmatter delimiter が正しいファイルを保存した

### 検証
- `mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/work/2026-05-25-world-observation.md`
  - valid: true
  - issues: []
- `make information-ingest-dry-run`
  - `world_observation_ingest processed=1 archived=0 failed=0 skipped=1`
  - `would_ingest informations/work/2026-05-25-world-observation.md`

### 次のセッションでやること
- 必要なら `make information-ingest-once` で実取り込みし、`make information-interpret-once` で Tomoko 用の解釈生成へ進める

## 2026-05-25 セッション30

### やること（開始時に書く）
- Phase 18 全体として、外部観測 Markdown と Tomoko 解釈パイプラインを実装する
- 18.0 から 18.8 まで、raw artifact directory、schema validator、DB store、normalizer、ingest job、interpreter、thinker / journalist 接続、operator recipe、trace hardening をテスト可能な単位で進める
- `/ws` / `TomoroSession` の hot path へ外部情報取得や LLM normalize を入れず、background / local job に閉じる

### やったこと
- `informations/` directory contract、`.gitignore`、README、Perplexity prompt、Codex operator recipe、sample artifact を追加した
- raw Markdown frontmatter validator、validator CLI、normalizer、ingest job、interpreter worker、trace inspect CLI を追加した
- `world_observation_documents` / `world_observation_items` / `world_observation_interpretations` / `world_observation_trace` を追加した
- `PostgresWorldObservationStore` / `InMemoryWorldObservationStore` を実装し、checksum idempotent import と archive / failed file movement を接続した
- `utterance_candidates.metadata_json` を追加し、world observation candidate の document / item / interpretation trace を保存できるようにした
- thinker に `WorldObservationSource`、journalist input に world observation interpretation 素材を接続した
- `PLAN.md` の Phase 18 checkbox と実装結果、`MEMORY.md`、`_docs/latency.md` を追記した

### 詰まったこと・解決したこと
- Phase 18.6 の trace は当初 `context_tags` だけで足りるように見えた
  → PLAN の `utterance_candidates.metadata_json` 契約を満たすため、候補テーブルに JSONB metadata を追加した
- `normalizer` の schema validation は pydantic を増やさず、既存 DTO 方針に合わせて dataclass + 手動 validation にした
- Perplexity / Computer Use の実 UI 操作は不安定な外周手順なので、operator recipe として文書化し、test 対象から外した

### 検証
- `mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/samples/sample-world-observation.md`
- `make information-ingest-dry-run`
- `git check-ignore informations/work/example.md informations/archived/example.md informations/failed/example.md`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### 次のセッションでやること
- 実 Perplexity 出力を `informations/work` に置き、`make information-ingest-dry-run` → `make information-ingest-once` → `make information-interpret-once` → `make thinker-once` の実データ smoke を行う
- 必要なら `world_observation_trace` で candidate / diary / conversation への接続を実データで確認する

## 2026-05-25 セッション29

### やること（開始時に書く）
- Supertonic F1 の stop ack を諦める前に、`はい` / `止めます` の分割生成 + WAV 結合を試す
- `はい、発話を止めます` など短すぎない制御文も生成し、Whisper で明瞭性を比較する
- Supertonic F1 で安定する候補があれば、Kyoko 版ではなく Supertonic F1 版 `assets/audio/stop_ack.wav` へ差し替える

### やったこと
- Supertonic F1 で `はい` / `止めます` / `はい、発話を止めます` の分割生成と WAV 結合を試した
- 追加で `はい、止めますね。` など、短すぎない候補を生成した
- 人間の聞き取りで `logs/stop-ack-supertonic-retry/phrase_tomemasu_ne.wav` を採用する判断になったため、`assets/audio/stop_ack.wav` へコピーした
- `StopAckAudioProvider` の control response text を `はい、止めますね` に更新した
- `tests/unit/test_stop_ack_audio.py` を Supertonic F1 採用版の 44.1kHz mono WAV 検証に更新した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に、Kyoko fallback を否定して選定済み Supertonic F1 版を採用する追記を入れた

### 詰まったこと・解決したこと
- `local_whisper_mlx_small` は短い Supertonic F1 の候補を安定して文字起こしできなかった
  → 今回は固定アセットの最終判断として、STT 結果より人間の聞き取りを優先した
- 既存の `はい、止めます` は末尾が欠けて聞こえやすかった
  → `はい、止めますね。` にして発話を少し伸ばし、末尾の不自然な切れを避けた

### 検証
- `file logs/stop-ack-supertonic-retry/phrase_tomemasu_ne.wav`
- `python` wave inspect: 44.1kHz / 16-bit / mono / 1756.8ms / 154,996 bytes
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで Supertonic F1 stop ack `はい、止めますね` が最後まで自然に聞こえることを確認する

## 2026-05-25 セッション28

### やること（開始時に書く）
- `assets/audio/stop_ack.wav` が「はい、止めます」の末尾「す」を欠いて聞こえる問題を、STT と波形で確認する
- 固定 WAV 自体が欠けている場合は Supertonic F1 で再生成または tail padding を入れて差し替える
- `StopAckAudioProvider` のテストに音声長 / tail silence を追加し、固定 WAV が末尾切れしにくいことを保証する

### やったこと
- 現行 Supertonic F1 版 `assets/audio/stop_ack.wav` を `local_whisper_mlx_small` にかけ、`四四四` と誤認識されることを確認した
- `はい、止めます。`、`はい、止めまーす。`、F2-F5 などの Supertonic 候補を生成して STT 比較したが、安定しなかった
- 明瞭性優先で `say -v Kyoko` 版へ戻し、`sox ... pad 0 0.30` で末尾 300ms の無音を追加した
- `tests/unit/test_stop_ack_audio.py` に 16kHz mono / duration / tail silence の検証を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に、Supertonic F1 採用を否定して Kyoko + tail silence 採用へ補正する追記を入れた

### 詰まったこと・解決したこと
- MLX Whisper は短い Supertonic 音声にかなり弱く、文字起こしだけでは完全な音質評価にはならない
  → ただし current asset の誤認識と Kyoko 版の安定認識の差が大きいため、control response は明瞭性優先で判断した
- 末尾が聞こえない問題が生成音声由来かブラウザ再生由来かを分けるため、固定 WAV に tail silence を入れて再生側の切れにも余裕を持たせた

### 検証
- `mise exec -- uv run python _tools/bench_stt_backends.py --backends local_whisper_mlx_small --runs 3 --audio-file assets/audio/stop_ack.wav --output logs/stop-ack-kyoko-clear/stt.json`
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_stop_intent_db.py`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで Kyoko stop ack が最後まで聞こえることを確認する

## 2026-05-25 セッション27

### やること（開始時に書く）
- `assets/audio/stop_ack.wav` を Supertonic-3 CoreML F1 voice style で再生成して差し替える
- Kyoko 生成と記録していた Phase 10.9 の実装結果・latency note を Supertonic F1 生成へ補正する
- 固定 WAV 読み込みテストと `ruff check .` / `pytest -m unit tests/unit/test_stop_ack_audio.py` で確認する

### やったこと
- `_tools/bench_tts_backends.py --targets supertonic_coreml_f1 --text 'はい、止めます' --output-dir logs/stop-ack-supertonic-f1` で Supertonic-3 CoreML F1 の固定 WAV を生成した
- `logs/stop-ack-supertonic-f1/supertonic_coreml_f1.wav` を `assets/audio/stop_ack.wav` へコピーして、Kyoko 版を置き換えた
- `PLAN.md` / `_docs/latency.md` / `MEMORY.md` に、Kyoko 生成を否定して Supertonic F1 採用へ補正する追記を入れた

### 詰まったこと・解決したこと
- Supertonic F1 の出力は 16kHz ではなく 44.1kHz mono PCM WAV だった
  → 既存の fixed WAV provider と Web Audio 再生経路は WAV container をそのまま扱うため、Supertonic 生成物をそのまま採用した

### 検証
- `file assets/audio/stop_ack.wav`
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_ack_audio.py`
- `mise exec -- uv run ruff check .`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで stop ack の音量・声質・再生タイミングを確認し、必要なら固定 WAV の speed / total_step を調整する

## 2026-05-25 セッション26

### やること（開始時に書く）
- Phase 10.9 全体として、online parallel stop-intent queue と固定 WAV 停止応答を実装する
- PostgreSQL queue / store、background classifier worker、`SessionEvent(type="stop_intent_classified")`、固定 WAV `StopAckAudioProvider` をテスト先行で接続する
- `pytest -m unit tests/unit/test_stop_intent_queue.py tests/unit/test_stop_ack_audio.py tests/unit/test_phase105_session_runtime.py`、integration DB test、全 unit で完了確認する

### やったこと
- `stop_intent_observations` / `stop_intent_shadow_signals` DDL と `stop_intent_shadow_analysis` view を追加した
- `PostgresStopIntentStore` / `InMemoryStopIntentStore`、rule / embedding / LLM classifier、`StopIntentClassifierWorker` を追加した
- LLM classifier は `asyncio.Semaphore(1)` で最大1同時にし、PostgreSQL 側は `FOR UPDATE SKIP LOCKED` で二重処理を避けるようにした
- `TomoroSession` が stop / wait / withdraw 候補 transcript から observation insert command を作り、classifier result を `stop_intent_classified` event として stale check するようにした
- 高信頼 stop 採用時は reply/TTS cancel、`audio_control stop`、固定 WAV「はい、止めます」の audio turn 送信で停止応答を完了するようにした
- `assets/audio/stop_ack.wav` を追加した。生成コマンドは `say -v Kyoko --data-format=LEI16@16000 -o assets/audio/stop_ack.wav 'はい、止めます'`
- `PLAN.md` の Phase 10.9 チェックボックス、`MEMORY.md`、`_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- `process_transcript()` は既存の direct async path なので、SessionEvent の reducer だけでは observation insert を表せなかった
  → `SessionCommand(type="insert_stop_intent_observation")` を internal command として作り、hot path では DB insert のみに閉じた
- 固定 WAV は通常返答ではなく control response なので、`conversation_logs` には保存せず audio turn と playback telemetry だけ既存経路に乗せた

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_intent_queue.py tests/unit/test_stop_ack_audio.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_stop_intent_db.py`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- Chrome 実セッションで「その話いったん置いといて」「今は聞けない」系が observation と shadow signal に残り、間に合う場合だけ fixed WAV stop ack へ切り替わることをログで確認する

## 2026-05-25 セッション25

### やること（開始時に書く）
- Phase 10.8 全体として、`AudioTurnController` を公開 API だけで進む純粋な制御対象に寄せる
- `TomoroSession` の audio turn pass-through helper と private helper 依存テストを削り、reply / precomputed reply の出力順序を public behavior で固定する
- `pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py` と全 unit で完了確認する

### やったこと
- `TomoroSession` の audio turn pass-through helper を削除し、reply generation / precomputed reply / hard interrupt が `AudioTurnController` の public API を直接使うようにした
- `tests/unit/test_session_concurrency.py` を private helper 直呼びから public behavior 検証へ補正した
- `PLAN.md` の Phase 10.8 チェックボックスを完了へ更新し、`ARCHITECTURE.md` / `MEMORY.md` に責務境界を追記した

### 詰まったこと・解決したこと
- precomputed reply は先に attention event を出すため、テストでは `reply_text` 以降の audio output 順序を検証する形にした
- Phase 10.8 のチェックボックス更新時に Phase 10.9 の同名検証項目へ一時的に巻き込みが出たため、未着手状態へ戻した

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

### 次のセッションでやること
- Phase 10.9 を実装する場合は、stop-intent queue の DDL / store / fixed WAV command 境界をテスト先行で固定する

## 2026-05-25 セッション24

### やること（開始時に書く）
- Phase 10.7 全体として、candidate runtime hard gate の所有者を `TomoroSession` に集約する
- `CandidateSpeakPolicy` / `CandidateCommandRunner` から runtime state 依存を外し、外側は soft decision と event 変換だけを担当する形へ戻す
- policy test と session final gate test を先に固定し、`pytest -m unit` で完了確認する

### やったこと
- `CandidateSpeakPolicy.evaluate()` から `TomoroRuntimeState` 依存を外し、soft score と candidate metadata 条件だけを見る形にした
- `CandidateCommandRunner` から `session.get_now_state()` を読む runtime gate を削除し、candidate fetch / policy decision / event post に寄せた
- `TomoroSession` の candidate final gate reason を `gate_reason` として emission payload に残し、candidate loaded 後の gate block を log に出すようにした
- policy が `speak` でも attention / VAD / playback / audio target の状態で `TomoroSession` が止める unit test を追加した
- `PLAN.md` の Phase 10.7 チェックボックスと実装結果、`MEMORY.md` の確定判断を追記した

### 詰まったこと・解決したこと
- candidate text readiness / expiry は runtime hard gate ではなく candidate metadata 条件として policy 側に残した
- fetch 前の早期 skip は `TomoroSession` 内の DB fetch 削減であり、runner / adapter 側の authoritative gate ではない形を維持した

### 検証
- `mise exec -- uv run ruff check server/gateway/initiative_policy.py server/gateway/candidate_commands.py server/session.py tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- Phase 10.8 に進む場合は、`AudioTurnController` への薄い delegate と private helper 依存テストを public behavior 検証へ寄せる

## 2026-05-25 セッション23

### やること（開始時に書く）
- markdown 編集制限の一時解除を受け、online parallel stop-intent classifier と固定 WAV stop acknowledgement の Phase を PLAN.md に追記する
- LLM 推論は最大1同時のキュー、データソースは PostgreSQL、会話停止は `TomoroSession` 経由で行う方針を明文化する

### やったこと
- `PLAN.md` に Phase 10.9「online parallel stop-intent queue と固定 WAV 停止応答」を追記した
- 既存ルール即停止を維持しつつ、embedding / LLM classifier を PostgreSQL queue 経由の online background worker として扱う方針を明文化した
- LLM 推論は最大1同時、classifier result は stale check 付き `SessionEvent` として `TomoroSession` に戻す方針にした
- 高信頼 stop が間に合った場合は、TTS / reply が進んでいても固定 WAV「はい、止めます」で会話停止を完了する方針にした

### 詰まったこと・解決したこと
- queue を新しい外部基盤にせず、PostgreSQL の observation / signal table と `FOR UPDATE SKIP LOCKED` で軽量に始める方針にした
- 固定 WAV は通常会話ログではなく control response として扱い、回り込みは既存 playback echo protection に乗せる方針にした

### 検証
- `git diff --check -- PLAN.md LOG.md`

### 次のセッションでやること
- Phase 10.9 実装時は DDL / store / worker concurrency test から始め、`TomoroSession` へは stale-safe な advisory event と fixed WAV command を最後に接続する

## 2026-05-25 セッション22

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、情報の流れが崩れている `TomoroSession` / `AudioTurnController` 境界の整理 Phase を PLAN.md に追記する
- candidate 発話 gate の所有者を `TomoroSession` に寄せ、`CandidateSpeakPolicy` / runner / main 側の runtime hard gate を削る Phase を PLAN.md に追記する

### やったこと
- `PLAN.md` に Phase 10.7「candidate runtime gate の所有者を TomoroSession に集約する」を追記した
- `PLAN.md` に Phase 10.8「AudioTurnController を純粋な制御対象に寄せる」を追記した
- Phase 10.6 の runtime hard gate 方針と Phase 6.6.4 の thin delegate 温存方針を、追記で明示的に補正した

### 詰まったこと・解決したこと
- candidate policy は soft decision に寄せ、runtime hard gate の正は `TomoroSession` にだけ置く方針に整理した
- audio turn は `TomoroSession` の意味判断から命令される制御対象とし、private helper 直呼びや薄い delegate を削る方針に整理した

### 検証
- `git diff --check -- PLAN.md LOG.md`

### 次のセッションでやること
- Phase 10.7 を実装する場合は、policy test から runtime hard gate 期待を削除し、`TomoroSession` final gate の unit test を先に固定する
- Phase 10.8 を実装する場合は、`tests/unit/test_session_concurrency.py` を private helper 依存から public behavior 検証へ移す

## 2026-05-25 セッション21

### やること（開始時に書く）
- Phase 10.6 配下の残りとして、ユーザーフィードバックを source / topic / emotional_need 別に保持して policy に反映する
- `judge_initiative_candidate` command に実 LLM judge runner を接続し、境界ケースだけ JSON judge を使えるようにする
- `TomoroSession` は引き続き final gate のみを担当し、DB read / LLM 実行は runner 側に閉じる

### やったこと
- `initiative_feedback_signals` DDL と `PostgresCandidateFeedbackStore` / `InMemoryCandidateFeedbackStore` を追加した
- `CandidateFeedbackScope` / `CandidateFeedbackSummary` DTO を追加し、source / topic / emotional_need bucket ごとに feedback を集計できるようにした
- `TomoroSession.start_precomputed_reply()` に `feedback_scope` を渡せるようにし、自発発話後の「それ今じゃない」「静かにして」「うん、なに？」系 transcript を feedback signal として保存するようにした
- `CandidateCommandRunner` が active candidate ごとに feedback summary を読み、metadata の `feedback_penalty` / `feedback_boost` と speakability に反映してから `CandidateSpeakPolicy` を実行するようにした
- `InitiativeLLMJudge` を追加し、`judge_initiative_candidate` command が設定済み judge を通して JSON result を `SessionEvent` として戻せるようにした
- `server/edge/main.py` で central / edge gateway の candidate runner に feedback store と LLM judge を接続した
- ローカル PostgreSQL に `docker/postgres/init/011_initiative_feedback.sql` を適用した

### 詰まったこと・解決したこと
- feedback を source だけで効かせると同じ source の別 topic まで強く抑制される
  → source は弱め、topic は強め、emotional_need bucket は中程度に重み付けして summary 化した
- 境界ケースの LLM judge に渡す desire / speakability snapshot は command payload へ直接持たせず、policy decision の signals から復元できるようにした

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase106_initiative_feedback_db.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### 次のセッションでやること
- Chrome 実セッションで自発発話後に「それ今じゃない」などを返し、次回 candidate score / decision log が下がることをログで確認する

## 2026-05-25 セッション20

### やること（開始時に書く）
- Phase 10.6 TomokoDesire / Speakability model を、DTO・load average 更新器・Personality 補正・CandidateSpeakPolicy・runtime 接続の順で実装する
- 自発発話を highest priority candidate 直行ではなく、desire / speakability / candidate metadata の決定的 policy で説明できる形にする
- `TomoroSession` には重い DB / LLM 判断を持たせず、既存の final gate と stale result policy を維持する

### やったこと
- `TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` / `CandidateSpeakMetadata` / `CandidateSpeakDecision` DTO を追加した
- `DesireLoadAverages` / `SpeakabilityLoadAverages` / `CandidateSpeakPolicy` を追加し、desire / speakability / personality / candidate metadata から `speak` / `wait` / `needs_llm_judge` を決定できるようにした
- `CandidateCommandRunner` が active candidate fetch 後に policy snapshot を組み立て、decision log を残して `TomoroSession` へ返すようにした
- `TomoroSession` で `policy_wait` と `initiative_llm_judge_requested` を扱い、judge 待ちの request id を stale result check に残すようにした
- LLM judge 用 JSON schema prompt builder / parser を追加し、未設定・malformed result は安全側に `wait` へ倒すようにした
- `PLAN.md` の Phase 10.6 チェックボックスと実装結果、`MEMORY.md` の確定判断を追記した

### 詰まったこと・解決したこと
- 最初の実装では `needs_llm_judge` に入る前に initiative request id を消していた
  → judge result が戻るまで request id を保持し、final `wait` / `speak` で clear するようにした
- `policy_wait` では candidate を dismiss しない
  → desire / speakability が変われば次回以降に話せる余地を残すため

### 検証
- `mise exec -- uv run ruff check server/gateway/initiative_policy.py server/gateway/candidate_commands.py server/session.py server/shared/models.py tests/unit/test_phase106_initiative_policy.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### 次のセッションでやること
- 実会話ログから rejection / acceptance feedback を source / topic / emotional_need に結びつける永続化を追加する
- 必要になった時だけ、`judge_initiative_candidate` command に実 LLM judge runner を接続する

## 2026-05-25 セッション19

### やること（開始時に書く）
- 複数クライアント同時対応を見据え、`TomoroSession` が WebSocket 実体ではなく接続状況の抽象 state を持てるようにする
- 接続がない時に initiative / arrival の online 発話が始まらない hard gate を追加する
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に接続状態と output target の方針を追記する

### やったこと
- `ClientConnection` / `ConnectedOutputState` DTO と `ClientConnectionRegistry` を追加した
- `TomoroRuntimeState.output_state` と `connected_output_state_changed` event を追加し、Session が接続 snapshot を観測できるようにした
- initiative / arrival の candidate fetch gate に `audio_target_available` を追加した
- `/ws` / `/edge/ws` で接続時の output state を Session に渡すようにした
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に接続状態と output target の方針を追記した

### 詰まったこと・解決したこと
- `TomoroSession` に WebSocket object や送信先一覧を持たせる案は否定し、adapter / gateway 側の registry が facts だけを集約する形にした
- 現時点では WebSocket 接続ごとに Session を作る構造を維持し、long-lived central Session 化は別 Phase に残した

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- long-lived central Session へ進む場合は、registry snapshot を既存 Session へ継続的に流す drain loop と output routing policy を別 Phase として切る

## 2026-05-25 セッション18

### やること（開始時に書く）
- markdown 編集制限の一時解除を受け、自発発話の「話したい欲」「発話可能性」「ユーザーフィードバック」「LLM 状況判断」の設計を文書化する
- `TomoroSession` にオンライン推論を増やさず、状態管理は決定的に保つ方針を `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に反映する

### やったこと
- `ARCHITECTURE.md` に「自発発話の欲求と発話可能性モデル」を追加した
- `TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` / `CandidateSpeakPolicy` の責務を整理した
- LLM judge は常時判定器ではなく、score が境界帯の時だけ JSON で状況判断する方針にした
- `PLAN.md` に Phase 10.6「TomokoDesire / Speakability model」を追加し、DTO、load average 更新器、personality drift、policy、LLM judge、runtime 接続に分解した
- `MEMORY.md` に確定判断として、自発発話を desire / speakability / policy に分ける方針を追記した

### 詰まったこと・解決したこと
- 45 秒 idle timer は「固定間隔で話す仕組み」ではなく、候補取得・発話判断の poll 間隔として残す整理にした
- `ambient_logs` がないことは人がいない証拠ではなく、presence signal の弱い一部として扱う方針にした
- ランダム性は状態遷移へ直接入れず、personality mood が desire gain / decay / threshold を補正する形にした

### 検証
- `git diff --check`

### 次のセッションでやること
- Phase 10.6 実装時は DTO と純粋判定器の unit test から始める
- runtime 接続前に、手元の候補・presence・feedback サンプルで score の妥当性を確認する

## 2026-05-25 セッション17

### やること（開始時に書く）
- MLX STT lane と CoreML STT lane を、Supertonic CoreML TTS + LFM MLX 会話推論の横負荷下で比較できる負荷ベンチを追加する
- Ctrl-C まで継続しつつ、STT latency だけでなく横負荷側の elapsed も同時に見られるようにする

### やったこと
- `_tools/soak_voice_stack_scenarios.py` を追加し、MLX STT stack と CoreML STT stack を同じ Supertonic CoreML TTS + LFM MLX 横負荷で交互に測れるようにした
- default scenario は `local_whisper_mlx_small` と `local_whisperkit_serve_small` の STT lane だけを変え、TTS は `supertonic_coreml_f1`、会話推論は `local_lfm25_12b_jp_mlx` に固定した
- 実行中は STT avg / p95 / max と load avg / p95 を表示し、sample / error / summary を `logs/voice-stack-soak.jsonl` に追記するようにした
- smoke 用に `--max-cycles` を追加し、通常は 0 のまま Ctrl-C まで継続する仕様にした
- default 横負荷を Supertonic TTS x2 + LFM conversation x6 に増やし、`--load-tts-repeats` / `--load-conversation-repeats` / workers 指定でさらに詰められるようにした
- `make soak-voice-stack` と README の説明を追加した
- `tests/unit/test_voice_stack_soak_tool.py` で scenario 構成と STT/load 集計を固定した

### 詰まったこと・解決したこと
- 「Supertonic CoreML + CoreML TTS」は同じ CoreML TTS lane を指すものとして扱い、default の CoreML TTS load backend を `supertonic_coreml_f1` にした
- 既存 `ConcurrentLoadRunner` を再利用し、同じ TTS/LLM load backend を scenario ごとに重複ロードしないよう load key で共有した
- 1回ずつの横負荷では load が 80-90ms で終わり、M4 Max では GPU / ANE を詰めきれていなかった
  → voice stack 専用の `StackLoadRunner` に切り替え、測定ごとに同じ backend instance で繰り返し load を走らせるようにした

### 検証
- `mise exec -- uv run ruff check _tools/soak_voice_stack_scenarios.py tests/unit/test_voice_stack_soak_tool.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_voice_stack_soak_tool.py tests/unit/test_stt_soak_tool.py tests/unit/test_stt_bench_tool.py`
- `mise exec -- uv run python _tools/soak_voice_stack_scenarios.py --max-cycles 1 --status-interval-sec 2 --output logs/voice-stack-soak-smoke.jsonl`
  - MLX STT stack: 109.6ms、load 95.7ms、error 0
  - CoreML STT stack: 212.8ms、load 89.9ms、error 0
- `mise exec -- uv run python _tools/soak_voice_stack_scenarios.py --max-cycles 1 --status-interval-sec 2 --output logs/voice-stack-soak-stress-smoke.jsonl`
  - MLX STT stack: 305.3ms、load 1286.6ms、error 0
  - CoreML STT stack: 429.3ms、load 1258.1ms、error 0
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 次のセッションでやること
- 長時間の実判断では `make soak-voice-stack` を数分以上回し、STT lane の差だけでなく LFM first-token 側の tail も別途見る

## 2026-05-25 セッション16

### やること（開始時に書く）
- Ctrl-C で止めるまで STT を生成し続ける負荷ベンチスクリプトを追加する
- 既存の STT backend / concurrent load 設定を再利用し、長時間の avg / p95 / throughput を見られるようにする

### やったこと
- `_tools/soak_stt_backends.py` を追加し、Ctrl-C / SIGTERM で止めるまで STT backend を継続実行できるようにした
- 実行中は avg / min / max / recent p95 / qps / error count を定期表示し、sample / error / summary を `logs/stt-soak.jsonl` に追記するようにした
- 既存の STT benchmark と同じ backend 指定、sample 音声生成、`--load-tts-backend` / `--load-conversation-backend` の横負荷指定を再利用した
- `make soak-stt` と README の実行例を追加した
- `tests/unit/test_stt_soak_tool.py` で percentile / running stats / recent-window summary を固定した

### 詰まったこと・解決したこと
- 最初に `ruff check` の対象へ `Makefile` を混ぜてしまい syntax error 表示が出た
  → Python ファイルだけを対象にし、最終的には `ruff check .` で全体検証した
- smoke では `local_whisper_mlx_small` を 12.7 秒回し、125 回、avg 101.4ms、recent p95 104.7ms、error 0 で Ctrl-C summary 出力を確認した

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_soak_tool.py tests/unit/test_stt_bench_tool.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run python _tools/soak_stt_backends.py --backends local_whisper_mlx_small --status-interval-sec 2 --output logs/stt-soak-smoke.jsonl`

### 次のセッションでやること
- 実運用の負荷確認では WhisperKit serve を起動したうえで `local_whisper_mlx_small,local_whisperkit_serve_small` を長時間回し、TTS / 会話推論横負荷ありの分布を見る

## 2026-05-25 セッション15

### やること（開始時に書く）
- Supertonic-3 CoreML F1 を正式な TTS backend として追加する
- default TTS を Kokoro MLX から Supertonic CoreML F1 へ切り替える
- startup warm-up と bench tool から同じ backend を使えるようにし、実測を `_docs/latency.md` に記録する

### やったこと
- `server/shared/inference/tts/supertonic_coreml.py` を追加し、Supertonic-3 CoreML を `TTSBackend` として使えるようにした
- `FluidInference/supertonic-3-coreml` の `.mlpackage` は `models/supertonic-3-coreml` に実ファイルコピーして使うようにした
- `Reza2kn/supertonic-3-coreml` の `F1` voice style を自動補完して使うようにした
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の default `tts_backend` を `supertonic_coreml_f1` に切り替えた
- `_tools/bench_tts_backends.py` と STT concurrent load validator に `supertonic_coreml` を追加した
- `coremltools` と `huggingface-hub` を直接依存に追加した
- `models/` を `.gitignore` に追加し、CoreML model assets を repo に同梱しないようにした
- README の default TTS と optional model download 説明を Supertonic 採用に合わせて更新した

### 詰まったこと・解決したこと
- Supertonic の model card / CoreML package は OpenRAIL family なので、MIT repo に model weights を同梱しない
  → `models/` は gitignore し、`make download-optional-models` または初回起動時の取得に寄せた
- Supertonic は内部的な逐次 audio streaming ではない
  → Tomoko 側の sentence flush ごとに 1 WAV chunk を返す backend として採用した

### 次のセッションでやること
- Chrome 実セッションで Supertonic F1 の発話開始タイミング、回り込み、barge-in 停止を確認する
- 音量や話速が気になる場合は `speed` / `total_step` を実測しながら調整する

### 検証
- `mise exec -- uv run python _tools/bench_tts_backends.py --targets supertonic_coreml_f1 --text 'こんにちは、トモコです。今日は少しだけ話してみます。' --output-dir logs/tts-supertonic-coreml-backend`
  - warm-up 9210.3ms、first/total 104.7ms、1 chunk、audio 4345.2ms
- FastAPI startup warm-up smoke
  - STT 1552.4ms、Supertonic TTS 7963.4ms、LFM conversation 1088.2ms、BGE-M3 embedding 6607.1ms
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

## 2026-05-25 セッション14

### やること（開始時に書く）
- embedding backend を MIT / Apache 系で評判の良い候補へ切り替える
- psycopg の LGPL-3.0-only が実運用で何を要求するか整理する
- LFM / Supertonic など任意ダウンロード系モデルの扱い方を README / Makefile 方針として整理する

### やったこと
- embedding backend を `intfloat/multilingual-e5-small` から `BAAI/bge-m3` へ切り替えた
- `BackendSpec.dimensions` と `BGEM3Backend` を追加し、1024次元 mismatch を起動時に検出できるようにした
- `config/central_realtime.toml` / `config/edge_kitchen.toml` の embedding backend を `local_bge_m3` に変更した
- `docker/postgres/init/006_bge_m3_embeddings.sql` を追加し、旧 e5 embedding を削除して pgvector columns を `vector(1024)` に移行するようにした
- ローカル PostgreSQL に migration を適用し、20件の conversation turn を BGE-M3 で backfill した
- `make download-models` と `make download-optional-models` を追加した
- README にモデル/依存ライブラリのライセンス扱い、psycopg LGPL、LFM / Supertonic optional download 方針を追記した

### 詰まったこと・解決したこと
- BGE-M3 は 1024次元なので、既存の `vector(384)` schema には入らない
  → 旧 embedding を削除し、DB column を `vector(1024)` に移行して再 backfill する migration にした
- LFM / Supertonic は MIT / Apache ではない
  → permissive model download と optional custom/OpenRAIL download を Makefile target で分けた

### 次のセッションでやること
- BGE-M3 採用後の memory search 品質を実会話ログで確認する
- session summary embedding は次回 summarizer 実行時に BGE-M3 で再生成する

### 検証
- BGE-M3 smoke: initial download + first embed 36990.5ms、cached fresh process first embed 7838.9ms、same process warm embed 32.8ms
- DB migration: `conversation_embeddings.embedding` / `conversation_sessions.summary_embedding` が `vector(1024)` になったことを確認
- Backfill: `conversation_embeddings` に `BAAI/bge-m3` 20件、`vector_dims=1024`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase86_session_summary_db.py`
- `git diff --check`

## 2026-05-25 セッション13

### やること（開始時に書く）
- Supertonic-3 CoreML の女性 voice style で日本語 smoke を実行し、音質評価用 WAV を出力する
- `F1`-`F5` の候補を同じ日本語文で生成し、latency と保存先を記録する
- smoke tool から女性 voice style を再現可能に使えるようにする

### やったこと
- `FluidInference/supertonic-3-coreml` には `voice_styles/M1.json` しか無かったため、`F1`-`F5` を含む `Reza2kn/supertonic-3-coreml` から女性 voice style JSON を取得した
- 既存 FluidInference CoreML model と `F1`-`F5` style JSON の互換性を確認し、全 voice で日本語 WAV を出力した
- `_tools/bench_supertonic_coreml_tts.py` に、missing voice style を `Reza2kn/supertonic-3-coreml` から補完する処理を追加した
- `tests/unit/test_supertonic_coreml_smoke_tool.py` に既存 voice style を再ダウンロードしない確認を追加した

### 詰まったこと・解決したこと
- 男性 `M1` は音質評価に不適切
  → `F1`-`F5` の女性 voice style を使い、`logs/supertonic-coreml-smoke/female/<voice>/ja-<voice>-run1.wav` を評価用サンプルにした

### 次のセッションでやること
- 人間が `F1`-`F5` の音質を聞き、許容 voice があれば `TTSBackend` 組み込み候補に進める

## 2026-05-25 セッション12

### やること（開始時に書く）
- Supertonic-3 CoreML TTS を単発 smoke し、日本語入力の生成可否、first/total latency、出力 WAV を確認する
- Tomoko 本体の TTS backend にはまだ組み込まず、`_tools` の候補評価に閉じる
- 結果を `_docs/latency.md` / `MEMORY.md` / `LOG.md` に残す

### やったこと
- HF `FluidInference/supertonic-3-coreml` の `infer.py` と `.mlpackage` 一式を取得した
- `_tools/bench_supertonic_coreml_tts.py` を追加し、CoreML model を通常ディレクトリへ実ファイルコピーしてから smoke できるようにした
- 日本語 `ja` / voice style `M1` / `CPU_AND_NE` で 5 runs 実測した
- 生成 WAV と summary JSON を `logs/supertonic-coreml-smoke/` に保存した
- `tests/unit/test_supertonic_coreml_smoke_tool.py` を追加し、集計 helper を unit test した

### 詰まったこと・解決したこと
- HF cache の `.mlpackage` が symlink になっており、`coremltools` prediction 時に CoreML compiler が `weight.bin` を見失った
  → `shutil.copytree(..., symlinks=False)` で実ファイルとして `logs/supertonic-coreml-smoke/model` に展開してから実行する CLI にした
- smoke 結果は model load 5533.7ms、warm synth avg 102.4ms、4.35秒音声で RTFx 38.5-43.9x

### 次のセッションでやること
- 出力 WAV を人間が聞いて、日本語品質と Tomoko らしい声として使えるかを確認する
- 音質が許容なら `TTSBackend` として組み込み、sentence flush 時の first audio latency を測る

## 2026-05-25 セッション11

### やること（開始時に書く）
- MLX Whisper と WhisperKit serve CoreML の STT latency を 30 runs で実測する
- idle / `kokoro_mlx` TTS 同時 / `kokoro_mlx` + LFM MLX 同時の p50 / p95 / max を比較する
- 平均ではなく tail latency を見て、CoreML STT を固定 200ms 枠として扱えるか確認する

### やったこと
- `_tools/bench_stt_backends.py` を `--runs 30` で3条件実行した
- idle 結果を `logs/stt-mlx-coreml-runs30-idle.json` に保存した
- `kokoro_mlx` TTS 同時負荷結果を `logs/stt-mlx-coreml-runs30-tts.json` に保存した
- `kokoro_mlx` + LFM MLX 同時負荷結果を `logs/stt-mlx-coreml-runs30-tts-llm.json` に保存した
- JSON の raw runs から avg / p50 / p95 / max を集計した

### 詰まったこと・解決したこと
- 前回3 runs では `kokoro_mlx` 同時負荷時に MLX Whisper max 300.5ms が出たが、30 runs では p95 165.2ms / max 167.8ms で再現しなかった
- WhisperKit serve CoreML は全条件で p95 216-222ms 程度に収まり、固定 200ms レーンとしては安定している
- ただし今回の30 runsでは、同時負荷時でも MLX Whisper の p95 が CoreML より速く、default を CoreML に変える理由はまだ弱い

### 次のセッションでやること
- さらに判断する場合は、より長い TTS 文、実録音 WAV、barge-in 中の STT など、実会話に近い負荷で再測定する

## 2026-05-25 セッション10

### やること（開始時に書く）
- STT benchmark CLI を単体 latency だけでなく、MLX LLM / TTS と同時実行した時の latency を測れる形へ拡張する
- CoreML STT が GPU/MLX workload と競合しにくいかを、同じサンプル音声と同じ測定手順で比較できるようにする
- 変更は bench tool と unit test に閉じ、実運用 backend / TomoroSession の挙動は変えない

### やったこと
- `_tools/bench_stt_backends.py` に `--load-tts-backend` / `--load-conversation-backend` / `--load-start-delay-ms` を追加した
- 各 STT 測定 run の直前に TTS / conversation workload task を起動し、その workload が走っている最中の STT latency を測るようにした
- JSON 出力に concurrent workload 設定と、各 run の `load_label` / `load_elapsed_ms` を保存するようにした
- helper unit test に concurrent load label と JSON 保存の確認を追加した
- `kokoro_mlx` 同時負荷、LFM MLX conversation 同時負荷、TTS + LLM 同時負荷の3条件を実測した

### 詰まったこと・解決したこと
- 短い LFM 生成は warm 状態だと 50ms 前後で終わり、STT と重なる時間が短かった
  → TTS 負荷も別条件として測り、MLX GPU/Metal 競合の影響が出るケースを分けて見られるようにした
- `kokoro_mlx` 同時負荷では MLX Whisper が 103ms 台から平均203.5msへ伸び、WhisperKit serve は平均215.9msでほぼ横ばいだった
  → CoreML STT は単体では遅いが、MLX TTS 同時実行時の余白としては評価する意味がある

### 次のセッションでやること
- Chrome 実セッションに近い録音 WAV と、実際の reply 長に近い TTS/LLM load text で tail latency を再測定する

## 2026-05-25 セッション9

### やること（開始時に書く）
- MLX Whisper と CoreML / WhisperKit serve STT backend を手元で比較できるベンチ CLI を追加する
- pytest perf ではなく、通常の `_tools` スクリプトとして warm-up / 複数回測定 / JSON 保存を行えるようにする
- 変更は bench tool と unit test に閉じ、STT backend / TomoroSession の挙動は変えない

### やったこと
- `_tools/bench_stt_backends.py` を追加し、config の backend 名から STT backend を生成して warmup + 複数回測定 + JSON 保存できるようにした
- `--backends` / `--runs` / `--text` / `--audio-file` / `--output` を受け取り、macOS `say` 生成音声または任意 WAV で比較できるようにした
- `tests/unit/test_stt_bench_tool.py` を追加し、backend 名 parsing / 測定値集計 / JSON の日本語保持を unit test で確認した
- `local_whisper_mlx_small` と `local_whisperkit_serve_small` を実測し、結果を `logs/stt-mlx-coreml-bench.json` に保存した

### 詰まったこと・解決したこと
- `write_json_summary()` が表より先に JSON path を表示して読みにくかった
  → CLI は表を先に出し、最後に `JSON: ...` を表示する形にした
- WhisperKit serve backend は今回も auto-start 後に `close()` で終了し、測定後に port 50060 が残っていないことを確認した

### 次のセッションでやること
- CoreML STT を default にする場合は、今回の CLI で Chrome 実セッションに近い録音 WAV を指定して再測定する

## 2026-05-25 セッション8

### やること（開始時に書く）
- WhisperKit `serve` を常駐させて叩く CoreML STT backend を追加する
- 既存の `mlx_whisper` / CLI 起動型 `whisper_coreml` と同じ STT 抽象に載せる
- 同じ `say` 合成音声で MLX Whisper と WhisperKit serve の速度ベンチを行う
- 変更は STT backend / config / bench / unit test に閉じ、TomoroSession には触れない

### やったこと
- `server/edge/pipeline/stt_whisperkit.py` に `WhisperKitServeSTT` を追加した
- `/health` が通る既存 WhisperKit server は再利用し、未起動なら backend が `whisperkit-cli serve` を起動するようにした
- `/v1/audio/transcriptions` に multipart `file` を送る形で transcription を実装した
- `server/edge/pipeline/stt_coreml.py` へ CLI 起動型 CoreML backend を分離し、`stt.py` を factory / MLX / faster-whisper 中心に戻した
- `config/central_realtime.toml` / `config/edge_kitchen.toml` に `local_whisperkit_serve_small` を追加した
- `tests/perf/test_stt_latency.py` の比較対象に `local_whisperkit_serve_small` を追加した
- `MEMORY.md` / `_docs/latency.md` に WhisperKit serve の判断と実測を追記した

### 詰まったこと・解決したこと
- WhisperKit server API は docs だけでなく実物確認した
  → `/` が endpoint 一覧を返し、`/v1/audio/transcriptions` は OpenAI 互換風の multipart `file` で動いた
- `stt.py` に CoreML 系を直接足すと 600 行超えになった
  → `stt_coreml.py` と `stt_whisperkit.py` に分割し、`stt.py` は 300 行未満に戻した

### 次のセッションでやること
- WhisperKit serve を default STT にする場合は、Chrome 実セッションで partial transcription / follow-up 誤起動を確認する
- 常駐 server を edge process の lifecycle で明示的に閉じる shutdown hook を追加するか検討する

### 検証
- WhisperKit serve API smoke: `GET /health` 200、`POST /v1/audio/transcriptions` で `ともこ 3たす3はいくつですか`
- STT perf: MLX warm 1111.8ms / measured 103.8ms、WhisperKit serve auto-start warm 4791.6ms / measured 214.3ms
- `mise exec -- uv run ruff check server/edge/pipeline/stt.py server/edge/pipeline/stt_coreml.py server/edge/pipeline/stt_whisperkit.py tests/unit/test_stt_backends.py tests/perf/test_stt_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py`
- `TOMOKO_STT_BENCH_BACKENDS=local_whisper_mlx_small,local_whisperkit_serve_small mise exec -- uv run pytest -m perf --tb=short tests/perf/test_stt_latency.py -s`

## 2026-05-25 セッション7

### やること（開始時に書く）
- Whisper を CoreML で動かす STT backend を追加し、既存 MLX Whisper と速度ベンチする
- Kokoro を CoreML で動かす TTS backend を追加し、既存 Kokoro MLX と速度ベンチする
- Kokoro は MLX / CoreML の聞き比べ用サンプル WAV を出力する
- 変更は backend 抽象、設定、ベンチツール、unit test に閉じ、TomoroSession の状態機械には触れない

### やったこと
- `WhisperCoreMLSTT` を追加し、`whisper-cli` / `whisperkit-cli` を設定の `command` で差し替えられるようにした
- `KokoroCoreMLBackend` を追加し、Python object の `generate_stream` または `kokoro say` CLI を使えるようにした
- `config/central_realtime.toml` / `config/edge_kitchen.toml` に CoreML STT / TTS backend 定義を追加した
- `_tools/bench_tts_backends.py` に `kokoro_mlx` / `kokoro_coreml` と `--targets` を追加した
- Kokoro CoreML の Japanese voice は `misaki[ja]` で IPA 化して CLI に渡すようにした
- `brew install whisperkit-cli jud/kokoro-coreml/kokoro` で実測用 CLI を導入した
- Kokoro MLX / CoreML の聞き比べ WAV を `logs/kokoro-mlx-coreml-bench/` に出力した
- `MEMORY.md` / `_docs/latency.md` に CoreML backend の制約と実測を追記した

### 詰まったこと・解決したこと
- Kokoro CoreML は Japanese voice に生テキストを渡すと CoreML shape error で落ちた
  → `misaki[ja]` で IPA 化し、`kokoro say --ipa` を使うことで合成できた
- Homebrew `kokoro` 0.11.0 は `--ipa` と `--stream` を同時に使えない
  → Japanese voice では stream を試した後、CLI error を検出して file generation に fallback する
- WhisperKit CLI は CoreML だが、現在の backend は transcription ごとに CLI を起動する
  → 実測値には process / model startup が乗るため、online 採用には `whisperkit-cli serve` など常駐化が必要

### 次のセッションでやること
- CoreML STT を実用候補にするなら、WhisperKit `serve` を起動済み前提にした persistent backend を追加する
- Kokoro CoreML の日本語 streaming が必要なら、`--ipa` + streaming が可能な runtime/API を探すか、MLX Kokoro を継続採用する

### 検証
- Kokoro TTS bench: MLX first 87.9ms / total 88.0ms、CoreML first 4816.4ms / total 4816.5ms
- STT perf: MLX measured 103.0ms、WhisperKit CoreML measured 4755.6ms
- `mise exec -- uv run ruff check server/shared/config.py server/edge/pipeline/stt.py server/shared/inference/tts/kokoro_coreml.py server/shared/inference/tts/__init__.py _tools/bench_tts_backends.py tests/unit/test_stt_backends.py tests/unit/test_kokoro_coreml_tts.py tests/perf/test_stt_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_kokoro_coreml_tts.py tests/unit/test_kokoro_mlx_tts.py tests/unit/test_phase0_config.py`
- `TOMOKO_STT_BENCH_BACKENDS=local_whisper_mlx_small,local_whisperkit_coreml_small mise exec -- uv run pytest -m perf --tb=short tests/perf/test_stt_latency.py -s`

## 2026-05-25 セッション6

### やること（開始時に書く）
- `lfm2.5-1.2b-jp-mlx` の `MLXLMBackend` 実モデル latency を実測する
- cold model load / warm-up / warm first text delta / total を測り、`_docs/latency.md` と `MEMORY.md` に追記する
- 実行構成の切り替えは必要最小限にし、Phase 10.5 の session runtime 実装には触れない

### やったこと
- 最初の `lfm2.5-1.2b-jp-mlx` model id では Hugging Face repo が見つからないことを確認した
- 公式 MLX repo id `LiquidAI/LFM2.5-1.2B-JP-MLX-bf16` に `config/central_realtime.toml` を補正した
- `MLXLMBackend` の `mlx_lm.stream_generate()` 呼び出しを、現行 `mlx-lm` API に合わせて `sampler=make_sampler(...)` 方式へ修正した
- `conversation_backend` を `local_lfm25_12b_jp_mlx` に切り替えた
- LFM backend 単体の cold / warm latency を `logs/lfm25-mlx-latency-smoke.json` に保存した
- FastAPI startup warm-up 経路で STT / TTS / LFM conversation / embedding の warm-up 時間を測り、`logs/lfm25-startup-warmup-smoke.log` に保存した
- `MEMORY.md` / `_docs/latency.md` に実 repo id と実測値を追記した

### 詰まったこと・解決したこと
- `lfm2.5-1.2b-jp-mlx` は短い呼び名としては通じるが、`mlx_lm.load()` に渡せる repo id ではなかった
  → 公式ページで確認できる `LiquidAI/LFM2.5-1.2B-JP-MLX-bf16` を採用した
- `stream_generate(..., temperature=0.0)` は現行 `mlx-lm` で `generate_step()` に通らなかった
  → `make_sampler(temp=0.0)` を作って `sampler=` として渡す形に修正した

### 次のセッションでやること
- Chrome 実セッションで `TomoroSession latency first_reply_text` と `first_audio_chunk` を確認し、体感品質と E2E を記録する

### 検証
- LFM backend smoke: cold first delta 4435.9ms / total 4485.4ms、warm first delta avg 26.6ms / total avg 76.0ms
- Startup warm-up: STT 2318.7ms、Kokoro 662.8ms、LFM conversation 3398.9ms、embedding 7291.9ms、total 13673.4ms
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

### 追記: ユーザー指定の LM Studio community 4bit 版へ切り替え
- ユーザー指定により `local_lfm25_12b_jp_mlx.model` を `lmstudio-community/LFM2.5-1.2B-Instruct-MLX-4bit` に変更した
- Hugging Face model card で MLX / 4-bit / `mlx_lm` 利用可能であることを確認した
- LFM 4bit backend 単体の cold / warm latency を `logs/lfm25-4bit-mlx-latency-smoke.json` に保存した
- FastAPI startup warm-up 経路の結果を `logs/lfm25-4bit-startup-warmup-smoke.log` に保存した
- LFM 4bit smoke: cold first delta 19022.9ms / total 19041.4ms、warm first delta avg 20.6ms / total avg 39.0ms
- Startup warm-up: STT 1936.1ms、Kokoro 321.6ms、LFM 4bit conversation 3478.3ms、embedding 7085.3ms、total 12822.4ms
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-25 セッション5

### やること（開始時に書く）
- Phase 10.5 の残チェック項目から、自発発話用の開始理由と priority policy を `TomoroSession` state / command に寄せる
- `wake_word` / `followup` / `initiative` / `arrival` / `resume_unspoken` を runtime state と command payload の共通語彙として固定する
- hard interrupt / withdrawn / human transcript / stale result の優先順位を unit test で明示する

### やったこと
- `StartReason` を追加し、`TomoroRuntimeState.last_start_reason` で直近の開始理由を読めるようにした
- human transcript の `called` / `invited` を runtime 上では `wake_word` / `followup` に正規化した
- conversation session の `start_reason` も `wake_word` / `followup` に寄せた
- initiative / arrival の fetch / start / mark command payload に `start_reason` を追加した
- `resume_unspoken` は共通語彙として予約し、実際の再提示経路はまだ追加しない形にした
- priority policy のうち、hard interrupt > playback echo、withdrawn > follow-up / initiative、human transcript > delayed initiative、current request > stale result を unit test で固定した
- `PLAN.md` の該当チェックボックスを更新し、`MEMORY.md` に判断を追記した

### 詰まったこと・解決したこと
- `called` / `invited` を消すと ambient log や user turn の意味が崩れる
  → 参加モードとしては残し、runtime の開始理由だけ `wake_word` / `followup` に正規化した
- `resume_unspoken` はまだ発話経路がない
  → 今回は型と state の予約語に留め、実際の候補消費は別 Phase へ残した

### 次のセッションでやること
- resume_unspoken を実装する場合は、interrupted turn / diary candidate からの command result に `start_reason="resume_unspoken"` と turn/candidate id を持たせる
- LLM delta / TTS chunk まで stale 判定を広げる場合は、`turn_id` / `chunk_id` の result event 化を進める

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase85_conversation_sessions.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run ruff check server/shared/models.py server/session.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase85_conversation_sessions.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-25 セッション4

### やること（開始時に書く）
- 他セッションで進行中の Phase 10.5 runtime hardening に触れず、`lfm2.5-1.2b-jp-mlx` 用のメイン推論バックエンドを追加する
- 変更範囲は `InferenceRouter` / inference backend / config / unit test に限定し、`TomoroSession` の実装競合を避ける

### やったこと
- `server/shared/inference/backends/mlx_lm.py` を追加し、`mlx_lm.load()` / `mlx_lm.stream_generate()` を使う汎用 `MLXLMBackend` を実装した
- `InferenceRouter` に `type = "mlx_lm"` を追加した
- `config/central_realtime.toml` に `local_lfm25_12b_jp_mlx` backend を追加した
- 進行中の Phase 10.5 と衝突しないよう、default `conversation_backend` は `lmstudio_gemma4_e2b` のまま維持した
- `tests/unit/test_mlx_lm_backend.py` と router test を追加した
- `MEMORY.md` / `_docs/latency.md` に今回の判断と検証を追記した

### 詰まったこと・解決したこと
- 既存 `gemma_mlx` は `mlx-vlm` 専用実装なので、LFM までそこへ寄せるとモデル種別の境界が曖昧になる
  → causal LM 系の汎用 backend として `mlx_lm` type を分けた
- 10.5 作業中に active config を切り替えると実行確認の前提が変わる
  → backend 定義だけ追加し、切り替えは `conversation_backend` の 1 行変更に留めた

### 次のセッションでやること
- Phase 10.5 の作業が落ち着いた後、`conversation_backend = "local_lfm25_12b_jp_mlx"` に切り替えて startup warm-up と first text delta を `_docs/latency.md` に追記する

### 検証
- `mise exec -- uv run ruff check server/shared/inference/backends/mlx_lm.py server/shared/inference/router.py tests/unit/test_mlx_lm_backend.py tests/unit/test_router.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_mlx_lm_backend.py tests/unit/test_router.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-25 セッション3

### やること（開始時に書く）
- Phase 10.5: TomoroSession runtime hardening を実施する
- `post_event()` の内部 event queue / drain loop を追加し、arrival / initiative / playback telemetry / transcript event の処理順を TomoroSession に閉じ込める
- command 結果が再び `SessionEvent` として戻る既存境界を保ちつつ、priority / stale result の見通しを unit test で固定する

### やったこと
- `TomoroSession.post_event()` の内側に `_event_queue` / `_event_drain_lock` / `_drain_events()` / `_process_event()` を追加した
- 複数 `post_event()` が同時に来ても reducer と即時反映 command が enqueue 順に処理されることを unit test で固定した
- `fetch_initiative_candidate` / `fetch_arrival_candidate` command に `request_id` を追加した
- `CandidateCommandRunner` が DB read 結果を event として戻す時に request id を引き継ぐようにした
- 古い initiative / arrival result を `stale_result` として捨てるようにした
- 人間発話などで attention が `ambient` でなくなった後に遅れて届いた initiative result は、既存の `not_speakable` priority で抑制されることを test で固定した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に Phase 10.5 の実装結果を追記した

### 詰まったこと・解決したこと
- `post_event()` を queue 化すると戻り値をどう維持するかが問題になる
  → 各 event に `Future[TransitionResult]` を対応させ、public API はこれまで通り `TransitionResult` を返す形にした
- candidate の DB read 結果が遅れて戻ると、後続の fetch 結果や人間発話後の状態に混ざる可能性があった
  → fetch command payload に request id を入れ、result event 側で一致しないものを stale として捨てるようにした
- 個別 `SessionEvent` dataclass 化は現時点では効果より変更範囲が大きい
  → 文字列 event 契約は維持し、queue / stale result / priority test で見通しを確保する粒度に留めた

### 次のセッションでやること
- `SessionEvent` の payload contract がさらに増えたら、`TranscriptFinalized` / `PlaybackStarted` / `CommandFailed` などの個別 dataclass 化を検討する
- resume_unspoken を実装する時は、今回の request id / stale result と同じ方針で `turn_id` / `candidate_id` を command result に持たせる

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase885_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run ruff check server/session.py server/gateway/candidate_commands.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-25 セッション2

### やること（開始時に書く）
- バックグラウンドプロセス、日記、edge / gateway など別プロセスで起動する機能の Makefile entry をメンテする
- config / log file / once / watch の入口を揃え、dry-run と unit test で壊れにくくする

### やったこと
- `Makefile` に `CENTRAL_CONFIG` / `EDGE_KITCHEN_CONFIG` と各 background process 用ログファイル変数を追加した
- `server` / `gateway` / `edge-kitchen` の起動を config 変数経由に揃え、`gateway-reload` / `edge-kitchen-reload` を追加した
- `session-summarizer` / `persona-updater` / `thinker` / `journalist` の once / watch target が `--config $(CENTRAL_CONFIG)` を明示するようにした
- background process の一括入口として `background-once` / `background-watch` / `background-dry-run` を追加した
- `tests/unit/test_makefile_process_entries.py` を追加し、Makefile の別プロセス入口を unit test で固定した

### 詰まったこと・解決したこと
- `thinker` / `journalist` / summarizer / persona updater は CLI 側に `--config` があるが、Makefile は一部だけ暗黙 default に頼っていた
  → 起動 target から中央 config を明示し、config 切り替え時のズレを減らした
- `make background-watch` を依存 target にすると常駐 process が直列実行で止まる
  → watch は別ターミナルで起動する target 名を表示するだけにし、実行する集合 target は `background-once` に限定した

### 次のセッションでやること
- 実運用では `make gateway` と必要な background process を別ターミナルで起動し、ログファイルの分離が見やすいか確認する
- docker-compose service 化は Phase 9 / Phase 12 の既存判断通り、app image 方針が固まってから行う

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_makefile_process_entries.py`
- `make -n background-dry-run`
- `make -n gateway-reload edge-kitchen-reload background-watch background-once`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-25 セッション1

### やること（開始時に書く）
- Phase 14: edge / gateway 間の WebSocket text event protocol を実装する
- edge と中央をプロセス分離できるように、edge 側 adapter と gateway 側 adapter を追加する
- 中央サーバーにも従来のブラウザ client 機能を残し、単体利用できる状態を維持する
- unit test を先に追加し、Phase 14 の境界と互換性を固定する

### やったこと
- `server/shared/edge_protocol.py` を追加し、edge -> gateway の `hello` / `presence` / `speech` /
  `playback_started` / `playback_ended` JSON protocol を固定した
- 中央サーバーに `/edge/ws` を追加し、既存 `/ws` と `/` client 配信は維持した
- `GatewayEdgeProtocolHandler` を追加し、presence report、primary edge 判定、duplicate 判定、
  stale / duplicate event discard を通して fresh な `speech` だけを `TomoroSession.process_transcript()` へ渡すようにした
- `TomoroSession.process_transcript()` を追加し、ローカル STT 済み transcript と remote edge transcript の入口を共通化した
- edge role かつ `node.gateway_ws_url` がある場合の `/ws` を remote edge adapter として動かし、
  ブラウザ音声を VAD/STT 後に `speech` event として中央へ送るようにした
- edge は gateway から返る `reply_text` / `emotion` / `reply_done` をブラウザへ転送し、
  edge local TTS で audio chunk を生成してブラウザへ送るようにした
- edge role の startup warm-up は `gateway_ws_url` がある場合 STT/TTS までに留めた
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を追記した

### 詰まったこと・解決したこと
- `role="edge"` は既存テストでは旧来の単体サーバーも表していた
  → `node.gateway_ws_url` がある場合だけ remote edge として扱い、中央 inference warm-up を skip する条件もそこに限定した
- central TomoroSession で TTS まで実行すると音声 bytes が edge 外へ出る境界と衝突する
  → `/edge/ws` 経路では gateway 側 `tts_backend=None` とし、返答 text event だけを edge へ返して edge local TTS で再生する形にした

### 次のセッションでやること
- reconnect backoff / heartbeat / connection health UI を Phase 14 hardening として追加する
- 実 LAN で `/edge/ws` の `speech sent -> received`、`reply sent -> received`、first audio latency を `_docs/latency.md` に記録する
- 複数 edge 接続をまたいだ長時間 soak test を追加する

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase14_presence_db.py`
- `make -n edge-kitchen gateway`
- `git diff --check`

## 2026-05-24 セッション57

### やること（開始時に書く）
- Phase 14: エッジ分離 + 回り込み除去を、PLAN の小 Phase に沿って進められるところまで実装する
- Phase 14.0 presence / edge_status の DB / DTO / store 契約を固定する
- Phase 14.1 DirectSpeakerResolver と Phase 14.2 DuplicateSpeechFilter をテスト先行で追加する
- Phase 14.3 / 14.4 は、ブラウザを dumb client のまま保つ前提で local config / Makefile の足場まで進める

### やったこと
- Phase 14.0 presence / edge_status の初段を実装した
  - `presence_reports` / `edge_status` DDL を追加した
  - `PresenceReport` / `EdgeStatus` DTO を追加した
  - `InMemoryPresenceStore` / `PostgresPresenceStore` を追加した
  - 音声 bytes を保存しないことを unit / integration test で固定した
- Phase 14.1 DirectSpeakerResolver を実装した
  - audio level 最大、同値なら recency、さらに同値なら device_id で deterministic に primary edge を選ぶ
  - DB write を持たない純粋判定器として追加した
- Phase 14.2 DuplicateSpeechFilter を実装した
  - 時間窓、device 差、文字列類似度で duplicate を判定する
  - embedding 類似度は使わない
  - hard interrupt keyword は duplicate より優先する
- Phase 14.4 local multi-process smoke の足場を追加した
  - `config/edge_kitchen.toml`
  - `make edge-kitchen` / `make gateway`
  - `TOMOKO_CONFIG` で `server.edge.main` の config path を切り替えられるようにした
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- Phase 14 を一気に完全分離すると、edge / gateway 間 protocol と TomoroSession の配置まで巻き込んで大きくなる
  → 今回は DB 契約と純粋判定器、local 起動足場までに絞った
- ブラウザに判断ロジックを置く方向ではない
  → `edge_kitchen` も Python server として起動し、ブラウザは引き続き音声 chunk / playback telemetry の事実送信に留める

### 次のセッションでやること
- Phase 14 をさらに進めるなら、edge が STT 後 text event を gateway へ送る adapter と、gateway 側で `DirectSpeakerResolver` / `DuplicateSpeechFilter` を実際の online path に挟む
- Phase 15 に進む場合は、今回追加した `edge_kitchen.toml` に edge local LLM fallback を載せる

### 検証
- `mise exec -- uv run ruff check server/shared/presence.py server/gateway/resolver.py server/gateway/dedup.py server/gateway/presence.py server/edge/main.py tests/unit/test_phase14_edge_split.py tests/integration/test_phase14_presence_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase14_edge_split.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase14_presence_db.py`
- `make -n edge-kitchen gateway`
- `mise exec -- uv run python - <<'PY' ...`

## 2026-05-24 セッション56

### やること（開始時に書く）
- Phase 12 を PLAN の順番に進める
- 同日 diary 再生成方針を仮決定し、`MEMORY.md` / `PLAN.md` に追記する
- Phase 12.1 Journalist input builder をテスト先行で実装する
- Phase 12.2 Diary writer、Phase 12.3 DiarySource、Phase 12.4 local process / Makefile を進められるところまで実装する

### やったこと
- Phase 12.0 の同日 diary 再生成方針を version 方式に決めた
  - `diary_entries.diary_version` を追加した
  - 同じ `diary_date` の追加生成は `1, 2, 3...` と版を積む
- Phase 12.1 Journalist input builder を実装した
  - `JournalistInputSnapshot` / session summary / conversation turn / ambient digest / dismissed candidate DTO を追加した
  - `PostgresJournalistSourceReader` で日付範囲の材料を読むようにした
  - ambient は raw 全量ではなく count と短い抜粋だけに絞る
- Phase 12.2 Diary writer を実装した
  - `server/journalist/main.py` に `DiaryWriter` を追加した
  - `InferenceRouter.select("diary", "privacy")` で生成するようにした
  - 空出力は error として扱い、原本を変更しない
- Phase 12.3 DiarySource を実装した
  - 昨日または直近 diary から短い `CandidateSeed` を生成する
  - dedupe key は `diary:<diary_id>` とした
- Phase 12.4 local process / Makefile を実装した
  - `background-process/run_journalist.py`
  - `make journalist-once` / `make journalist`
- `build_default_thinker()` に `DiarySource` を追加し、日記由来 seed が thinker に入るようにした
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 同日 diary を overwrite すると、日記が解釈ログであるにもかかわらず過去解釈を失う
  → `diary_version` を積む方式にした
- docker-compose service 化は thinker と同じく app image 方針が未定で半端になる
  → Phase 12 では local process と Makefile target までを完了範囲にし、service 化は M4 へ送った

### 次のセッションでやること
- Phase 13 に進む場合は InferenceRouter 強化の未チェック項目を確認する
- Journalist を実運用で試す場合は `make journalist-once JOURNALIST_DATE=YYYY-MM-DD` で日記生成を確認する

### 検証
- `mise exec -- uv run ruff check server/shared/diary.py tests/unit/test_phase120_diary_store.py server/journalist/input.py server/journalist/main.py server/thinker/sources/diary.py server/thinker/main.py tests/unit/test_phase121_journalist_input.py tests/unit/test_phase122_journalist_writer.py tests/unit/test_phase123_diary_source.py tests/unit/test_phase124_journalist_process.py tests/unit/test_router.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase120_diary_store.py tests/unit/test_phase121_journalist_input.py tests/unit/test_phase122_journalist_writer.py tests/unit/test_phase123_diary_source.py tests/unit/test_phase124_journalist_process.py tests/unit/test_router.py`
- `mise exec -- uv run ruff check tests/integration/test_phase120_diary_db.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase120_diary_db.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `make -n journalist-once journalist`
- `mise exec -- uv run python background-process/run_journalist.py --help`

## 2026-05-24 セッション55

### やること（開始時に書く）
- Phase 11.3 の cached audio 送信順を PLAN 通り `reply_text` / `audio_start` / binary / `audio_end` / `reply_done` に修正する
- `generated_audio` は first RIFF/WAVE chunk cache のまま維持する判断を `PLAN.md` / `MEMORY.md` に反映する
- multi-chunk 完全事前生成は別テーブル方針として DB/DTO/store の足場を追加する
- Phase 11.0 / 11.3 の未チェック項目をテストで倒す

### やったこと
- `TomoroSession` の通常 TTS 経路と precomputed reply 経路を、`reply_text` / `audio_start` / binary / `audio_end` / `reply_done` の順序へ揃えた
- `UtteranceCandidate` の `maturity=2` を `generated_text` + `generated_audio` 必須として DTO で固定した
- `generated_audio` は first RIFF/WAVE chunk cache として維持し、完全 multi-chunk 事前生成用に `pregenerated_audio_chunks` 別テーブルを追加した
- `PregeneratedAudioChunk` DTO と `InMemoryPregeneratedAudioChunkStore` / `PostgresPregeneratedAudioChunkStore` を追加した
- `PLAN.md` / `MEMORY.md` に、前回の `reply_done` before `audio_end` 方針を否定する追記と別テーブル方針を記録した
- Phase 11.0 / 11.3 / 11.4 の実装済みチェックを更新した

### 詰まったこと・解決したこと
- 既存経路は `reply_done` が `audio_end` より先だったが、今回の人間判断により PLAN 経路を正とした
  → cached audio だけでなく通常 TTS 経路も同じ順序へ修正した
- `generated_audio` に multi-chunk を詰めると first chunk cache と完全事前生成 manifest の意味が混ざる
  → first chunk cache は維持し、multi-chunk は `pregenerated_audio_chunks` へ分離した

### 次のセッションでやること
- Phase 11 をさらに進めるなら、pregenerator が全 chunk を `pregenerated_audio_chunks` に保存し、gateway が順序付き multi-chunk cache を送る実装へ進む
- ただし現時点の online 消費は first chunk cache の `generated_audio` で成立している

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase110_pregenerated_candidate.py tests/unit/test_phase10_candidate_command_runner.py`
- `mise exec -- uv run ruff check server/session.py server/shared/candidate.py tests/unit/test_phase110_pregenerated_candidate.py tests/unit/test_phase10_candidate_command_runner.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase113_pregenerated_audio_consumption.py`
- `mise exec -- uv run ruff check tests/unit/test_phase113_pregenerated_audio_consumption.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

## 2026-05-24 セッション53

### やること（開始時に書く）
- Phase 10: 自発発話 + 入室時の初手を、テスト先行で進められるところまで実装する
- 必要になった場合だけ Phase 10.5 runtime hardening に進む
- Phase 11〜15 は、実装が迷わないように先に `PLAN.md` へタスク分解を追記してから順番に進める
- M4 完了条件を確認し、不足している対応を進める

### やったこと
- Phase 10 の online candidate consumption 初段を実装した
  - `TomoroSession` に `session_started` / `idle_timer_elapsed` / `initiative_candidate_loaded` / `arrival_candidate_loaded` reducer を追加
  - `CandidateCommandRunner` を追加し、candidate fetch / mark / start reply を command として実行するようにした
  - `/ws` 接続時に arrival fetch、45秒 idle loop で initiative fetch を投げるようにした
- Phase 11 を実装しやすい粒度へ `PLAN.md` に追記し、初段を実装した
  - `UtterancePregenerator` を追加
  - `ThinkerProcess.run_once()` / candidate loop に pregeneration step を追加
  - `generated_audio` 付き candidate を gateway で優先し、TTS を呼ばずに cached audio を送れるようにした
- Phase 12 を実装しやすい粒度へ `PLAN.md` に追記し、Phase 12.0 diary store 初段を実装した
  - `diary_entries` DDL
  - `DiaryEntry` / `DiaryStore` / `InMemoryDiaryStore` / `PostgresDiaryStore`
- Phase 13〜15 を実装しやすい粒度へ `PLAN.md` に追記した
- Phase 13.0 / 13.1 の monitor 初段を実装した
  - `inference_metrics` DDL
  - `InferenceMetricSample` / metric stores / `BackendHealthMonitor`
  - router が error metric を fallback 判断対象にできるようにした
- `MEMORY.md` と `_docs/latency.md` に今回の判断・検証結果を追記した

### 詰まったこと・解決したこと
- `generated_audio` に複数 chunk を完全保存するには単一 bytea では表現が弱い
  → Phase 11 初段では「最初の再生可能 RIFF/WAVE chunk cache」として扱い、完全な multi-chunk 事前生成は必要になった時に別テーブルまたは manifest を検討する方針にした
- precomputed reply の `reply_done` / `audio_end` 順序は Phase 11.3 の追記案と既存 TTS 経路でズレがあった
  → 今回は既存経路に合わせて `reply_text` → `audio_start` → binary → `reply_done` → `audio_end` とし、順序変更は既存 TTS 経路全体の互換性確認後に回した
- Phase 12 の同日 diary 再生成方針は overwrite か version か未確定
  → writer 実装前の残項目として `PLAN.md` / `MEMORY.md` に残した

### 次のセッションでやること
- Phase 10 は browser 実測で arrival / idle initiative の first audio latency を `_docs/latency.md` に追記する
- Phase 11 は multi-chunk 事前生成を現行 `generated_audio` のまま進めるか、別テーブル化するか判断する
- Phase 12 は diary 同日再生成方針を決めてから Journalist input builder / writer へ進む
- Phase 13 は metric store の integration smoke と periodic probe runner を追加する
- Phase 14 / 15 は今回の PLAN 分解に沿って DB/DTO から進める
- M4 完了条件は Phase 14 / 15 が未実装のため未達

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase10_session_contract.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase1_echo.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase94_thinker_loop.py tests/unit/test_phase111_pregenerator.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase120_diary_store.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase13_inference_monitor.py tests/unit/test_router.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション54

### やること（開始時に書く）
- Phase 10 配下で人間判断が必要だった項目への回答を `PLAN.md` / `MEMORY.md` に反映する
- initiative / arrival 発話だけでは conversation session を開始しないよう修正する
- 対応する unit test を追加して `pytest -m unit` を確認する

### やったこと
- Phase 10 の人間判断を `PLAN.md` と `MEMORY.md` に反映した
- `TomoroSession.start_precomputed_reply()` から conversation session 開始を外した
  - initiative / arrival 発話では attention だけ `engaged` にする
  - 人間が返事した時に通常の参加判断経路で conversation session を開始する
- 自発発話判断45秒は発話固定間隔ではなく候補取得判断間隔であることを明記した
- Phase 10.5 は今は実施しない、Phase 10 は unit 実装済みで完了扱いにする判断を記録した

### 詰まったこと・解決したこと
- 自発発話で attention を開くことと conversation session を開始することが混ざっていた
  → attention は開くが session は人間の返答で開始する形に分離した

### 次のセッションでやること
- Phase 10 の実 browser 確認は体験確認として別途行う
- Phase 11 以降へ進む場合は PLAN の小 Phase に従う

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase10_session_contract.py`
- `mise exec -- uv run ruff check server/session.py tests/unit/test_phase10_candidate_command_runner.py`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション52

### やること（開始時に書く）
- markdown 編集制限ルールの一時解除を受け、`PLAN.md` の Phase 10 を実装しやすい粒度へ分解する
- `TomoroSession` が candidate / arrival を消費する event / command 境界を明文化する
- LLM がテスト先行で迷わない完了条件とテスト観点を追記する

### やったこと
- `PLAN.md` の Phase 10 を 10.0〜10.4 に分解した
  - 10.0: initiative / arrival の session 契約
  - 10.1: 自発発話 candidate の消費
  - 10.2: arrival candidate の消費
  - 10.3: online adapter / command runner 接続
  - 10.4: regression / 完了判定
- Phase 10 では event queue / drain loop / 個別 event dataclass へ進まず、既存の `SessionEvent` / `SessionCommand` 文字列契約で候補消費を固定する方針を明記した
- timer / WebSocket / DB result は adapter が event に変換し、最終判断は `TomoroSession` に閉じることを明記した
- `MEMORY.md` に Phase 10 分解方針を追記した

### 詰まったこと・解決したこと
- 元の Phase 10 は「timer」「候補 cleanup」「on_session_start」が混ざっており、DB read/write を session 内で直呼びするか、メイン層で behavior 判断するかが曖昧だった
  → 先に event / command 契約を固定し、DB read/write は command runner、判断は `TomoroSession` に寄せる粒度へ分解した

### 次のセッションでやること
- Phase 10.0 実装時は `tests/unit/test_phase10_session_contract.py` を先に追加し、`session_started` / `idle_timer_elapsed` が返す command を固定する

### 検証
- `git diff -- PLAN.md LOG.md`
- `rg -n "Phase 10|Phase 10\\.0|Phase 10\\.1|Phase 10\\.2|Phase 10\\.3|Phase 10\\.4|Phase 10\\.5|session_started|idle_timer_elapsed|fetch_initiative_candidate|fetch_arrival_candidate|start_arrival_reply" PLAN.md`
- `git diff --check -- PLAN.md LOG.md`

## 2026-05-24 セッション51

### やること（開始時に書く）
- Phase 9 全体の完了条件を確認する
- Phase 9.0〜9.4 の実装、テスト、PLAN 上の未チェック項目を突き合わせる
- 不足があれば対応し、`pytest -m unit` など必要な検証を行う

### やったこと
- Phase 9.0〜9.4 の完了条件、実装結果、実ファイル、テストを突き合わせた
- Phase 9.4 の docker-compose service 追加は、現行 compose / app image 方針が未定のため M4 に送る項目であり、Phase 9 の不足として扱わないことを `PLAN.md` / `MEMORY.md` に追記した
- `tests/integration/test_phase90_candidates_db.py` が既存候補データに干渉されて落ちる問題を修正した
  - テスト用 `device_id` / `context_tags` で隔離した
  - 開始時と終了時にテスト用 row を削除し、commit するようにした
  - 既存 fresh arrival candidate と競合しないよう、テスト時刻を将来固定値にした

### 詰まったこと・解決したこと
- Phase 9.0 integration test が、実 DB に残っていた active / fresh candidate を先頭候補として拾って失敗した
  → store の全体 fetch 契約は維持し、テスト側を自分が挿入した row に限定して検証するよう修正した

### 次のセッションでやること
- Phase 10 に進む場合は、`TomoroSession` から candidate / arrival を消費する境界をテストで固定する
- docker-compose service 化は M4 で app image 方針が決まってから扱う

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run python background-process/run_thinker.py --help`
- `make thinker-once`
- `git diff --check`

## 2026-05-24 セッション50

### やること（開始時に書く）
- M3 Phase 9.4: thinker process loop を実装する
- `server/thinker/main.py` と `background-process/run_thinker.py` を追加し、candidate generation と arrival precompute を once / watch で動かせるようにする
- Makefile entry、loop 観測ログ、unit / smoke test を追加する
- `pytest -m unit` と thinker-once smoke で確認する

### やったこと
- `server/thinker/main.py` を追加した
  - `ThinkerProcess` で source → seed 保存 → evaluator → text-ready 保存を実行する
  - `arrival_precompute_loop` と `candidate_generation_loop` を追加し、watch では `asyncio.gather(...)` で並行実行する
  - generated seed count / inserted seed count / kept candidate count / arrival behavior / elapsed_ms / error count を log に出す
- `background-process/run_thinker.py` を追加し、`--once` / `--watch` / interval options を受けるようにした
- `Makefile` に `thinker` / `thinker-once` を追加した
- `tests/unit/test_phase94_thinker_loop.py` と `tests/integration/test_phase94_thinker_smoke.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- source / evaluator の失敗を例外で loop 全体へ伝播させると background process が止まる
  → Phase 9.4 では error count と log に閉じ、次 interval で回復できる形にした
- docker-compose service 追加は、現時点ではアプリ用 Docker image / Dockerfile がないため半端な定義になる
  → local process entrypoint と Makefile までで止め、M4 のインフラ安定化で app image 方針を決めてから追加する

### 次のセッションでやること
- Phase 10 に進む場合は、`TomoroSession` から candidate / arrival を消費する境界を先にテストで固定する
- docker-compose の thinker service は、app image 方針が決まってから追加する

### 検証
- `mise exec -- uv run ruff check server/thinker/main.py background-process/run_thinker.py tests/unit/test_phase94_thinker_loop.py tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase94_thinker_loop.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run python background-process/run_thinker.py --help`
- `make -n thinker thinker-once`
- `make thinker-once`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `git diff --check`

## 2026-05-24 セッション49

### やること（開始時に書く）
- M3 Phase 9.3: arrival precompute を実装する
- `ArrivalPrecomputer` と arrival context / prompt / fallback 境界を追加する
- fresh arrival candidate 保存、LLM 失敗時 fallback、DTO round-trip、fake 構成 perf をテストで固定する
- `pytest -m unit` で確認する

### やったこと
- `server/thinker/arrival.py` を追加した
  - `ArrivalPrecomputer.precompute_once(now, device_id)` で 3 分 TTL の arrival candidate を保存する
  - active な urgent utterance candidate から `urgent_candidate_count` / `top_urgent_seeds` を組み立てる
  - optional な `ArrivalStatsReader` で session 統計と persona hint を注入できるようにした
- `ArrivalContextSnapshot` を Phase 9.3 schema へ更新した
  - `computed_at` / `local_time` / `time_since_last_session_sec` / `session_count_today` / `urgent_candidate_count` / `top_urgent_seeds` / `persona_hint`
  - 古い `observed_at` JSON は読み取り fallback として残した
- arrival prompt の出力 schema を `behavior` / `utterance_text` / `reason` に固定した
- LLM 失敗や invalid response は `wait_silent` fallback として保存するようにした
- `tests/unit/test_phase93_arrival_precompute.py` と `tests/perf/test_phase93_arrival_precompute_latency.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- Phase 9.0 の `ArrivalContextSnapshot` は arrival precompute 前の仮 schema だった
  → Phase 9.3 の必須項目へ更新しつつ、既存 JSON を読めるよう `observed_at` fallback を残した
- 入室前 context に session 統計をどう入れるかは DB 実装を増やすと Phase 9.3 の範囲を越える
  → 初段は `ArrivalStatsReader` protocol にして、Phase 9.4 以降の background loop / DB reader から差し込める形にした

### 次のセッションでやること
- Phase 9.4: thinker process loop で candidate generation と arrival precompute を定期実行する
- `ArrivalStatsReader` の実 DB reader が必要なら Phase 9.4 で追加する

### 検証
- `mise exec -- uv run ruff check server/shared/candidate.py server/thinker/arrival.py tests/unit/test_phase93_arrival_precompute.py tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase93_arrival_precompute.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase93_arrival_precompute.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`
- `git diff --check`

## 2026-05-24 セッション48

### やること（開始時に書く）
- M3 Phase 9.2: LLM evaluator を実装する
- `ThinkerEvaluationContext` / `EvaluatedUtterance` と `UtteranceEvaluator` 抽象を追加する
- `LLMUtteranceEvaluator` を追加し、privacy task / JSON schema / failure fallback を unit test で固定する
- `pytest -m unit` で確認する

### やったこと
- `ThinkerEvaluationContext` / `EvaluatedUtterance` を追加した
- `UtteranceEvaluator` 抽象と `LLMUtteranceEvaluator` を追加した
  - `InferenceRouter.select("candidate_gen", "privacy")` を使う
  - JSON schema は `should_keep` / `generated_text` / `priority` / `urgent` / `reason`
  - malformed JSON や backend failure は `None` として破棄する
- `InferenceRouter` と config に `candidate_gen` backend / fallback を追加した
- `CandidateStore.insert_evaluated_utterance_once()` を追加した
  - `should_keep=false` と evaluator failure は保存しない
  - `should_keep=true` は `maturity=1` candidate として保存する
- `tests/unit/test_phase92_llm_evaluator.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 既存の Phase 9.1 seed 保存と text-ready 保存を同じ dedupe key で扱うと、maturity 0 の seed が maturity 1 保存を塞ぐ可能性がある
  → Phase 9.2 の `insert_evaluated_utterance_once()` では maturity 1 以上の active candidate だけを dedupe 対象にした
- LLM evaluator に会話原文や DB row を渡すと境界が重くなる
  → `ThinkerEvaluationContext` の要約・用語・人格 subset だけを渡す DTO 境界にした

### 次のセッションでやること
- Phase 9.3 に進む場合は arrival precompute を実装する
- Phase 9.4 の thinker process loop で source → evaluator → store を常駐処理として接続する

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_router.py tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run ruff check server/shared/candidate.py server/shared/config.py server/shared/inference/router.py server/thinker tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-24 セッション47

### やること（開始時に書く）
- M3 Phase 9.1: deterministic source / selection を実装する
- `CandidateSeed` / `ThinkerSourceContext` と `InformationSource` を追加する
- deterministic `TimeBasedSource` と `HighestPriority` selection を追加し、dedupe 方針を unit test で固定する
- `pytest -m unit` で確認する

### やったこと
- `server/shared/candidate.py` に `CandidateSeed` / `ThinkerSourceContext` を追加した
- `server/thinker/sources/base.py` と `server/thinker/sources/time_based.py` を追加した
  - `TimeBasedSource` は時刻 bucket だけから deterministic seed を返す
  - 外部 API / LLM / DB read は呼ばない
- `server/thinker/selection/base.py` と `server/thinker/selection/highest.py` を追加した
  - priority 降順、urgent 優先、expires_at 昇順、created_at 昇順で選ぶ
- dedupe は `context_tags` の `dedupe:<dedupe_key>` で固定した
  - active candidate に同じ dedupe tag があれば insert しない
  - spoken / dismissed 済みは再生成可能にした
- `tests/unit/test_phase91_deterministic_sources.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- dedupe_key の保存先は専用カラムも考えられるが、Phase 9.1 では schema を増やす圧がまだない
  → `context_tags` に `dedupe:<dedupe_key>` を保存し、後続で検索圧や DB 一意性が必要になったら専用列 / index を検討する方針にした

### 次のセッションでやること
- Phase 9.2 に進む場合は `UtteranceEvaluator` / `ThinkerEvaluationContext` / `EvaluatedUtterance` から実装する
- LLM evaluator failure は online 会話を止めず、失敗 seed を捨てるか log に残すだけにする

### 検証
- `mise exec -- uv run ruff check server/shared/candidate.py server/thinker tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-24 セッション46

### やること（開始時に書く）
- M3 Phase 9.0: candidate schema / DTO / store を実装する
- `docker/postgres/init/006_candidates.sql`、`server/shared/candidate.py`、`PostgresCandidateStore` を追加する
- candidate DTO round-trip と active/fresh fetch の unit test、PostgreSQL store round-trip の integration test を追加する

### やったこと
- `docker/postgres/init/006_candidates.sql` を追加した
  - `utterance_candidates` / `arrival_candidates` と active / fresh fetch 用 index を追加した
  - `maturity` / `behavior` の CHECK 制約を追加した
  - `spoken_at` と `dismissed_at` が同時に立たない制約を追加した
- `server/shared/candidate.py` を追加した
  - `UtteranceCandidate` / `ArrivalCandidate` / `ArrivalContextSnapshot` DTO を追加した
  - `CandidateMaturity` / `ArrivalBehavior` の許可値を loader で検証するようにした
  - `CandidateStore` protocol、`InMemoryCandidateStore`、`PostgresCandidateStore` を追加した
- `tests/unit/test_phase90_candidates.py` を追加した
  - DTO round-trip
  - expired / spoken / dismissed 除外
  - priority 降順・created_at 昇順
  - fresh arrival fetch を固定した
- `tests/integration/test_phase90_candidates_db.py` を追加し、PostgreSQL DDL 適用後の store round-trip を確認した
- `PLAN.md` の Phase 9.0 チェックボックスと実装結果を更新した
- `MEMORY.md` / `_docs/latency.md` に Phase 9.0 の判断と検証結果を追記した

### 詰まったこと・解決したこと
- arrival candidate の device filter を JSONB だけで行うと store 契約が読みにくくなる
  → `device_id` は検索用の列としても持ち、同じ値を `ArrivalContextSnapshot` にも含める形にした
- Phase 9.0 で LLM evaluator や常駐 loop まで進めると責務が混ざる
  → schema / DTO / store のみで止め、Phase 9.1 以降へ送った

### 次のセッションでやること
- Phase 9.1 に進む場合は、`CandidateSeed` / `ThinkerSourceContext` と deterministic `time_based` source から実装する
- dedupe_key の保存先と一意性は Phase 9.1 で判断・実装する

### 検証
- `mise exec -- uv run ruff check server/shared/candidate.py tests/unit/test_phase90_candidates.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run ruff check .`
- `git diff --check`

## 2026-05-24 セッション45

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、`PLAN.md` の M3 Phase 9 を実装しやすい粒度へ分解する
- Phase 9 を DB/DTO/store、deterministic source、LLM evaluator、arrival precompute、process loop に分ける
- LLM がテスト先行で迷わず進められる完了条件とテスト観点を追記する

### やったこと
- `PLAN.md` の Phase 9 を 9.0〜9.4 に分解した
  - 9.0: candidate schema / DTO / store
  - 9.1: deterministic source / selection
  - 9.2: LLM evaluator
  - 9.3: arrival precompute
  - 9.4: thinker process loop
- Phase 9 では online `/ws` 経路や `TomoroSession` に接続せず、background 側の候補プール構築だけを担当することを明記した
- 各小 Phase に完了条件、テスト観点、失敗時 fallback、Redis / pub-sub を導入しない境界を追記した

### 詰まったこと・解決したこと
- 元の Phase 9 は部品名は明確だったが、DB lifecycle / DTO / source / evaluator / loop が混ざっており実装判断が入りやすかった
  → DB 契約から順に積む小 Phase に分解し、LLM がテスト先行で進められる形にした

### 次のセッションでやること
- Phase 9.0 実装時は `006_candidates.sql`、`server/shared/candidate.py`、`PostgresCandidateStore`、unit/integration test から着手する

### 検証
- `git diff -- PLAN.md LOG.md`
- `git diff --check`
- `rg -n "Phase 9|Phase 9\\.0|Phase 9\\.1|Phase 9\\.2|Phase 9\\.3|Phase 9\\.4|Phase 9 全体" PLAN.md`

## 2026-05-24 セッション44

### やること（開始時に書く）
- M2 Phase 8.8: ContextSnapshotBuilder 初段の完了状態を確認する
- 実装済みであれば `PLAN.md` の未チェック項目を実態に合わせてチェックする
- ドキュメント更新のみとして、必要な軽量検証後にコミットする

### やったこと
- `MEMORY.md` / `LOG.md` / `PLAN.md` / `README.md` / `ARCHITECTURE.md` と `_reference/` の必読資料を確認した
- `LOG.md` セッション41、`PLAN.md` の実装結果、実装ファイル、unit/perf test から Phase 8.8 初段が実装済みであることを確認した
- `PLAN.md` の Phase 8.8 チェックボックスを完了済みに更新した

### 詰まったこと・解決したこと
- Phase 8.8 直下には「append-only 制約により上のチェックボックスは直接変更しない」と書かれていたが、現在の `AGENTS.md` では `PLAN.md` のチェックボックス状態変更は許可されている
  → 実装結果と後続 Phase 8.8.1 / 8.8.5 の進行に合わせて、チェック状態だけを更新した

### 次のセッションでやること
- 特になし

### 検証
- `rg -n "Phase 8\\.8|ContextSnapshotBuilder|ContextBuild|TomokoContextSnapshot|server/gateway/context.py|test_phase88" PLAN.md`
- `rg -n "class ContextSnapshotBuilder|TomokoContextSnapshot|ContextBuildPolicy|ContextBuildTrace|ContextDepth|context_snapshot" server tests/perf tests/unit`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション43

### やること（開始時に書く）
- M2 Phase 8.8.5: TomoroSession 状態管理の最小足場を実装する
- `SessionEvent` / `TomoroRuntimeState` / `StateEmission` / `SessionCommand` / `TransitionResult` の DTO と `post_event()` / `_reduce()` の入口を追加する
- 既存挙動を壊さず、playback telemetry などの状態変更入口を event-shaped runtime へ寄せる regression test を追加する
- `pytest -m unit` で確認する

### やったこと
- `server/shared/models.py` に `TomoroRuntimeState` / `SessionEvent` / `StateEmission` / `SessionCommand` / `TransitionResult` を追加した
- `TomoroSession.get_now_state()` と `post_event()` / `_reduce()` の最小入口を追加した
- playback telemetry を `post_event()` 経由に寄せ、`handle_playback_telemetry()` は薄い互換入口にした
- transcript finalized の reducer 入口を追加し、active playback 中の echo と hard interrupt を `TransitionResult` として観測できるようにした
- `AudioTurnController` に runtime snapshot 用の read-only property を追加した
- `tests/unit/test_phase885_session_runtime.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 既存の実会話処理を一気に command runner 化すると変更範囲が大きすぎる
  → Phase 8.8.5 では playback telemetry の実処理だけを `post_event()` 経由にし、transcript finalized は reducer で判断を観測できる最小足場に留めた
- `AudioTurnController` の内部状態を runtime snapshot で読む必要があった
  → 状態変更 API は増やさず、read-only property だけを追加した

### 次のセッションでやること
- M3 の自発発話や arrival で競合が増えたら、`post_event()` の先に event queue / drain loop と command runner を追加する
- transcript finalized の既存 async 処理を command 実行に移す場合は、DB write / LLM / TTS / WebSocket send を段階的に `SessionCommand` 化する

### 検証
- `mise exec -- uv run ruff check server/shared/models.py server/gateway/audio_turn.py server/session.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase885_session_runtime.py tests/unit/test_session_concurrency.py tests/unit/test_barge_in.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

## テンプレート

```
## YYYY-MM-DD セッションN

### やったこと
- `_docs/evaluation.md` を追加した
  - 会話体験スコアを responsiveness / attended_feeling / turn_taking / interruption / memory / persona / recovery に分解した
  - 人間評価 JSON と機械ログ JSONL の初期案を置いた
  - 相関だけでなく回帰・特徴量重要度・失敗事例分析で最適化する方針を書いた
- `MEMORY.md` に、会話体験品質は人間評価と機械メトリクスの対応で最適化する判断を追記した

### 詰まったこと・解決したこと
- コード実装ではなく将来の評価設計なので、unit test は追加しなかった
- Markdown 差分に trailing whitespace がないことを `git diff --check` で確認した

### 次のセッションでやること
- 評価実装へ進む場合は、`logs/evals/*.jsonl` の出力 DTO と `turn_id` / `session_id` の join 方針から着手する
```

---

## 2026-05-24 セッション42

### やること（開始時に書く）
- M2 Phase 8.8.1: ContextSnapshotBuilder 運用 hardening を実装する
- process-local TTL cache と cache hit / age / ttl trace を追加する
- 遅い optional source が timeout しても degraded snapshot を返す regression test を追加する
- unit / perf test で ContextSnapshotBuilder の予算運用を確認する

### やったこと
- `ContextSnapshotBuilder` に process-local TTL cache を追加した
  - `same_session_turns` / `recent_turns` / `session_summaries` / `memory_hits` / `lexicon_terms` / `persona_slice` を source 単位で cache する
  - `ContextBuildTrace.cache_entries` に hit / age_ms / ttl_ms を残す
- `ContextBuildPolicy.max_parallel_sources` を追加し、context source の同時実行数を policy で制限できるようにした
- builder log に `cache_hits` と `max_parallel_sources` を含めた
- cache hit、TTL expiry 後の DB fallback、cache miss + DB timeout の regression test を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- cache を authoritative state に広げると状態の正が分散する
  → cache 対象は読み取り専用の context source に限定し、active session / attention / playback は対象外にした
- timeout した source の task が後から prompt に混ざると危険
  → `asyncio.wait(timeout=...)` の pending task を cancel し、done だけを assemble する既存方針を regression test で固定した

### 次のセッションでやること
- 実 DB データ量が増えたら、`normal` / `deep` の DB + embedding 込み perf を追加する
- 応答速度に応じた `fast` / `normal` / `deep` の動的選択を試す場合は、まず `ContextBuildTrace` と turn latency を見て policy を決める

### 検証
- `mise exec -- uv run ruff check server/gateway/context.py server/shared/models.py tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_context_snapshot_latency.py`

## 人間
short/normal/deepとかは応答速度を元に動的に切り替えても良いかも

## 2026-05-24 セッション41

### やること（開始時に書く）
- M2 Phase 8.8: ContextSnapshotBuilder 初段を実装する
- `TomokoContextSnapshot` / `ContextBuildPolicy` / `ContextBuildTrace` DTO と builder を追加する
- 既存 recent turns / session summary / turn memory / persona subset を budget 内で集約し、`TomoroSession` の文脈取得を builder 経由へ寄せる
- unit / perf test を追加し、timeout は degraded context として扱うことを保証する

### やったこと
- `server/shared/models.py` に `ContextDepth` / `ContextBuildPolicy` / `ContextBuildTrace` / `TomokoContextSnapshot` を追加した
- `server/gateway/context.py` に `ContextSnapshotBuilder` を追加した
  - same session recent turns / recent turns / session summaries / turn memory / lexicon / persona slice を source として扱う
  - `max_build_ms` 超過時は pending source を skipped にして degraded snapshot を返す
- `TomoroSession` の reply context 読み込みを builder 経由にした
  - `ThinkingInput.context` / `long_term_memory` / `context_snapshot` を snapshot から作る
- `ThinkFastMode` / `ThinkDeepMode` が `context_snapshot` の lexicon / persona slice を prompt に反映できるようにした
- `server/edge/main.py` で `PostgresPersonaSnapshotStore` を `TomoroSession` に渡すようにした
- `tests/unit/test_phase88_context_snapshot.py` と `tests/perf/test_context_snapshot_latency.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- timeout で pending task を cancel した時、未await coroutine warning が出た
  → source awaitable を `_timed()` 内で遅延生成する factory 方式にして warning を消した
- cache は初段で実体まで入れると authoritative state の境界が曖昧になる
  → `ContextBuildTrace.cache_hits` の境界だけを用意し、実 cache は Phase 8.8.1 に送った

### 次のセッションでやること
- Phase 8.8.1 に進む場合は、process-local TTL cache の実装、normal/deep の実 DB perf、cache age / ttl trace を追加する

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_context_snapshot_latency.py`

## 2026-05-24 セッション40

### やること（開始時に書く）
- M2 Phase 8.7: 用語集ログと人格スナップショットを実装する
- `persona_lexicon_versions` / `persona_state_versions` の JSONB DDL とモデルクラスを追加する
- background worker と round-trip / jsonb query test を追加し、online 経路には乗せない

### やったこと
- `docker/postgres/init/005_persona_snapshots.sql` を追加した
  - `persona_lexicon_versions` / `persona_state_versions` を作成する
  - snapshot / diff JSONB に GIN index を追加する
- `server/shared/models.py` に persona snapshot / diff / prompt subset DTO を追加した
  - `PersonaLexiconSnapshot`
  - `PersonaStateSnapshot`
  - `PersonaVersionDiff`
  - `LexiconTerm`
  - `PersonaPromptSlice`
- `server/shared/persona.py` を追加した
  - 最新 snapshot の読み込み
  - completed session material の読み込み
  - 新 version の JSONB 保存
- `server/background/persona_updater.py` を追加した
  - completed session summary から lexicon / persona state の version を作る
  - LLM extractor は JSON を返し、保存前に DTO loader / validator を通す
- `background-process/update_persona_snapshots.py` を追加し、`Makefile` に `persona-updater` / `persona-updater-once` を追加した
- `tests/unit/test_phase87_persona_snapshots.py` と `tests/integration/test_phase87_persona_snapshots_db.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 生 JSONB をプログラム中で持ち回らないため、DB 境界で必ず `PersonaLexiconSnapshot` / `PersonaStateSnapshot` / `PersonaVersionDiff` に変換する形にした
- 応答生成へ使う場合も snapshot 全量を prompt に入れず、`select_terms_for_prompt()` / `to_prompt_slice()` の subset DTO だけを使う契約にした

### 次のセッションでやること
- Phase 8.8 に進む場合は、`ContextSnapshotBuilder` から latest lexicon / persona snapshot を読み、budget 内で subset DTO に落とす

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/005_persona_snapshots.sql`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase87_persona_snapshots_db.py`

## 2026-05-24 セッション39

### やること（開始時に書く）
- `_tools/summarize_pending_sessions.py` をルートの `background-process/` へ移動する
- Makefile に session summarizer を起動する entry を追加する
- background process として定期実行できる入口を用意する

### やったこと
- `background-process/` ディレクトリを追加し、`summarize_pending_sessions.py` を移動した
- `summarize_pending_sessions.py` に `--watch` と `--interval-sec` を追加した
  - `--watch` なしなら 1 batch だけ処理して終了する
  - `--watch` ありなら pending session を定期 polling する
- `Makefile` に `session-summarizer` と `session-summarizer-once` を追加した
  - `session-summarizer` は `--watch` 付きで起動する
  - `session-summarizer-once` は 1 batch だけ処理する
- `PLAN.md` に background process 入口の配置補正を追記した

### 詰まったこと・解決したこと
- 特になし

### 次のセッションでやること
- `make server` と `make session-summarizer` を別ターミナルで起動して、実会話後の pending session が処理されるか確認する

### 検証
- `mise exec -- uv run python background-process/summarize_pending_sessions.py --help`
- `make -n session-summarizer`
- `make -n session-summarizer-once`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## 2026-05-24 セッション38

### やること（開始時に書く）
- M2 Phase 8.6: セッション要約索引を実装する
- `summary_status='pending'` の会話セッションを background worker が要約し、要約 embedding とともに `conversation_sessions` へ保存する
- online `TomoroSession` 経路では要約生成を呼ばないことをテストで保証する

### やったこと
- `server/background/session_summarizer.py` を追加した
  - pending session を処理し、要約と summary embedding を `conversation_sessions` に保存する
  - 失敗時は `summary_status='error'` と `summary_error` を残す
- `PostgresConversationSessionSummaryStore` を追加した
  - pending claim、session turn 読み出し、completed/error 更新、summary vector search を実装した
- `_tools/summarize_pending_sessions.py` を追加した
  - background worker 相当として pending session を処理できる
- `InferenceRouter` / `config/central_realtime.toml` に `session_summary` backend を追加した
- `TomoroSession` の deep memory 検索に completed session summary search を追加した
  - 要約生成系メソッドは online 経路で呼ばない
  - 既存 turn-level `conversation_embeddings` 検索は残した
- `conversation_sessions.summary_embedding` の HNSW index を DDL に追加し、ローカル PostgreSQL に適用した
- `tests/unit/test_phase86_session_summary.py` と `tests/integration/test_phase86_session_summary_db.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- integration test の cleanup で、`conversation_logs` が session を参照しているため session 行を先に消せなかった
  → cleanup で該当 `conversation_logs` を先に削除してから `conversation_sessions` を削除するようにした
- `SessionSummarizer` が要約生成後に model 名取得のため router を二度呼びそうになった
  → `_summarize()` が summary text と backend name を一緒に返す形にし、要約 LLM selection は一度だけにした

### 次のセッションでやること
- Phase 8.7 に進む場合は、`persona_lexicon_versions` / `persona_state_versions` の JSONB DDL と model round-trip test から始める

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/004_conversation_sessions.sql`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase86_session_summary_db.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

## 2026-05-24 セッション37

### やること（開始時に書く）
- M2 Phase 8.5: 会話セッション境界を実装する
- `conversation_sessions` と `conversation_logs.conversation_session_id` を追加する
- `TomoroSession` で active conversation session を開始・終了し、同一 session の文脈を優先して読む

### やったこと
- `docker/postgres/init/004_conversation_sessions.sql` を追加した
  - `conversation_sessions` を作成し、summary fields と `summary_embedding vector(384)` を同じ行に持たせた
  - `conversation_logs.conversation_session_id` と session / recorded_at index を追加した
- `PostgresConversationSessionStore` を追加した
  - session 開始と終了を DB に保存する
  - 終了時に `summary_status='pending'` へ進める
- `TomoroSession` に `active_conversation_session_id` を追加した
  - 最初の参加発話で session を開始する
  - follow-up では同じ active session を再利用する
  - `cooldown -> ambient` と `withdrawn` で session を閉じる
- user / tomoko turn 保存時に active session ID を紐づけるようにした
- 短期文脈読み出しを、同一 session 優先 + 不足分だけ recent completed turn で補完する形にした
- `tests/unit/test_phase85_conversation_sessions.py` を追加した
- `PLAN.md` / `MEMORY.md` / `_docs/latency.md` を更新した

### 詰まったこと・解決したこと
- 既存 unit test の in-memory `ConversationLogWriter` は `conversation_session_id` 引数を持たないものが多い
  → `TomoroSession` 側で writer の signature を見て、対応している writer にだけ keyword を渡す互換レイヤにした
- `conversation_sessions` の DDL は既存 `conversation_logs` 作成後に外部キーを張る必要がある
  → `004_conversation_sessions.sql` として追加し、ローカル PostgreSQL に適用して確認した

### 次のセッションでやること
- Phase 8.6 に進む場合は、online 経路では要約を呼ばず、`summary_status='pending'` の session を background worker が拾うテストから始める

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/004_conversation_sessions.sql`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

## 2026-05-24 セッション36

### やること（開始時に書く）
- 将来の最適化フェーズで使う会話体験評価設計を `_docs/evaluation.md` として追加する
- 人間評価と機械メトリクスを結び、設定調整を定量化できる形にする

### やったこと
-

### 詰まったこと・解決したこと
-

### 次のセッションでやること
-

## 2026-05-24 セッション35

### やること（開始時に書く）
- `Makefile` に `db-stop` / `db-down` / `db-dump` を追加する
- `db-stop` / `db-up` / `db-down` / `db-dump` を実際に動作確認する

### やったこと
- `Makefile` に `db-stop` を追加した
  - `$(COMPOSE) stop postgres`
  - コンテナ停止のみ。volume は残す
- `Makefile` に `db-down` を追加した
  - `$(COMPOSE) down`
  - コンテナと network を削除する。volume は削除しない
- `Makefile` に `db-dump` を追加した
  - `docker exec tomoko-postgres pg_dump -U tomoko -d tomoko`
  - `logs/db-dumps/tomoko-YYYYMMDD-HHMMSS.sql` に保存する

### 詰まったこと・解決したこと
- dump は git 管理対象にしたくない
  → 既存で ignore 済みの `logs/` 配下に保存するようにした
- `db-down` 後に DB が戻るか不安が残る
  → `db-down` 後に `db-up` し、既存テーブルが見えることを確認した

### 検証
- `make db-dump`
- `make db-stop`
- `make db-up`
- `make db-down`
- `make db-up`
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -c "\\dt"`

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション34

### やること（開始時に書く）
- ルートの `docker-compose.yml` を `docker/` 配下へ移動できるか確認する
- 移動する場合は `Makefile` の `db-up` が壊れないよう compose file の指定を更新する

### やったこと
- `docker-compose.yml` を `docker/docker-compose.yml` に移動した
- `Makefile` に `COMPOSE = docker compose --project-directory . -f docker/docker-compose.yml` を追加した
- `db-up` は `$(COMPOSE) up -d postgres` を使うようにした
- `ARCHITECTURE.md` の構成図を `docker/docker-compose.yml` 前提に更新した

### 詰まったこと・解決したこと
- compose file を単純に `docker/` 配下へ移すと相対パスや compose project 名が変わる可能性がある
  → `--project-directory .` を付け、build context / bind mount / volume project 名をルート基準のまま維持した

### 検証
- `docker compose --project-directory . -f docker/docker-compose.yml config`
- `make db-up`
- `git diff --check`

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション33

### やること（開始時に書く）
- Markdown 編集禁止ルールの一時解除を受け、`README.md` を現状のセットアップ手順に合わせて更新する
- `mise` 前提と `make deps` の導線を明記する

### やったこと
- `README.md` の必要なものに `mise` と Docker / Docker Compose を明記した
- Python / uv は `mise.toml` と `make deps` で揃える方針を書いた
- 現行 default の LM Studio / MLX Whisper / Kokoro MLX / embedding 構成を追記した
- 初回モデルダウンロードと warm-up に時間がかかること、LM Studio を使う場合の準備を追記した
- セットアップ手順を `make deps` / `make db-up` / `make server` に整理した

### 詰まったこと・解決したこと
- README の古い手順は Ollama / irodori 前提が残っていた
  → 現在の `config/central_realtime.toml` に合わせて、LM Studio + MLX/Kokoro 構成へ更新した

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション32

### やること（開始時に書く）
- `Makefile` に依存関係を解決する入口を追加する
- 既存の `mise exec -- uv ...` 運用に合わせる

### やったこと
- `Makefile` に `deps` target を追加した
- `deps` は `mise exec -- uv sync` を実行し、既存の `mise` / `uv` 運用に揃えた
- `README.md` のセットアップ手順を `uv sync` から `make deps` に更新した

### 詰まったこと・解決したこと
- 特になし

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション31

### やること（開始時に書く）
- LLM オーケストラツールが投入する `task.md` / `task.acceptance.md` / `summary.md` を git 管理対象から外す
- `.gitignore` に追加し、今後追跡されないようにする

### やったこと
- `.gitignore` に `task.md` / `task.acceptance.md` / `summary.md` を追加した
- `git rm --cached task.md task.acceptance.md summary.md` を実行し、ローカルファイルを残したまま git 管理対象から外した

### 詰まったこと・解決したこと
- 3ファイルはいずれも tracked だった
  → index からだけ削除し、以後は ignored file として扱うようにした

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション30

### やること（開始時に書く）
- 開発用ディレクトリである `tools/` を `_tools/` にリネームする
- 開発時の計測結果ディレクトリである `docs/` を `_docs/` にリネームする
- 過去実装参照ディレクトリである `reference/` を `_reference/` にリネームする
- コード・設定・ドキュメント内の参照を更新して、既存機能を壊さない

### やったこと
- `tools/` を `_tools/` にリネームした
- `docs/` を `_docs/` にリネームした
- `reference/` を `_reference/` にリネームした
- `AGENTS.md` / `README.md` / `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` / `LOG.md` / `_docs/latency.md` / `pyproject.toml` の参照を新ディレクトリ名へ更新した
- `_tools` 配下の Python import と unit test の import を `tools.*` から `_tools.*` に更新した
- `pyproject.toml` の ruff exclude を `_reference` に更新した

### 詰まったこと・解決したこと
- `_tools` import の軽い確認コマンドで戻り値型を勘違いしたワンライナーを実行して失敗した
  → 正しいアクセスに直して import 確認し、unit test でも `_tools` import が通ることを確認した

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション29

### やること（開始時に書く）
- ルートの `asset-factory_for_work` を `_tools/` 配下へ移動する
- 既存の参照パスを更新して、機能を維持する
- ruff / unit test で壊れていないことを確認する

### やったこと
- `asset-factory_for_work/asuka.wav` を `_tools/asset-factory_for_work/asuka.wav` へ移動した
- `asset-factory_for_work/generate_tomoko_assets.py` を `_tools/asset-factory_for_work/generate_tomoko_assets.py` へ移動した
- 移動後も `assets/images/` に出力できるよう、スクリプトの repository root 解決を `parents[2]` に更新した
- `_docs/latency.md` の `asuka.wav` 参照を新しい `_tools/asset-factory_for_work/asuka.wav` に更新した

### 詰まったこと・解決したこと
- 移動前の `ROOT = Path(__file__).resolve().parents[1]` は `_tools/` 配下へ移すと `_tools/` を root と誤認する
  → `parents[2]` に変えて、引き続きリポジトリルート配下の `assets/images/` へ生成するようにした

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション28

### やること（開始時に書く）
- TTS ベンチ出力スクリプトの出力先を git 管理外の `logs/` 配下へ変更する
- `artifacts/` ディレクトリを削除する
- `artifacts/` 配下の WAV を git 管理対象から外す

### やったこと
- `_tools/bench_tts_backends.py` の default 出力先を `artifacts/tts-bench` から `logs/tts-bench` に変更した
- `.gitignore` に `artifacts/` を追加し、再作成されても git 管理対象にならないようにした
- `git rm -r artifacts` で tracked WAV 8 ファイルを削除し、git 管理対象から外した
- `_docs/latency.md` の TTS ベンチ WAV 保存先を `logs/tts-bench/` に更新した
- `PLAN.md` / `MEMORY.md` に、過去の `artifacts/` 保存運用を否定して `logs/` 配下へ補正する判断を追記した

### 詰まったこと・解決したこと
- `artifacts/` は実体としても git index としても不要な生成物だった
  → 出力先を `logs/` に変更し、既存 tracked WAV は削除した

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション27

### やること（開始時に書く）
- ルートディレクトリにある `test_ollama*.py` を `tests/` 配下へ移動する
- pytest / ruff の対象として問題なく扱われることを確認する
- ルートディレクトリの見通しを良くする

### やったこと
- ルート直下の `test_ollama*.py` 7 ファイルを `tests/manual/ollama/` に移動した
- これらは pytest 形式の unit test ではなくトップレベル実行される手動確認スクリプトだったため、`pyproject.toml` の pytest 設定で `tests/manual` を collection 対象から外した

### 詰まったこと・解決したこと
- `test_ollama*.py` を `tests/` 直下へ置くと pytest collection 時に実行される可能性があった
  → `tests/manual/ollama/` に置き、通常の `pytest -m unit` では拾わない形にした

### 次のセッションでやること
- 追加対応なし

## 2026-05-24 セッション26

### やること（開始時に書く）
- 外部LLMから追加指摘された TomoroSession state/control 設計を評価する
- 妥当な内容を `PLAN.md` / `ARCHITECTURE.md` / `MEMORY.md` / `AGENTS.md` に反映する
- `_reference/2026-05-24-1200_設計評価と改善提案.md` は原文として参照のみ行い、変更しない

### やったこと
- 指摘は妥当と判断した
  - M2 では本格的な event-driven architecture ではなく、`post_event()` / reducer / command 境界の最小足場に留める
  - M3 で自発発話や arrival による競合が増えた段階で、event queue / drain loop / 個別 event dataclass を強化する
- `PLAN.md` に Phase 8.8.5 `TomoroSession 状態管理の最小足場` を追加した
- `PLAN.md` に Phase 10.5 `TomoroSession runtime hardening` を追加した
- `ARCHITECTURE.md` に `TomoroSession` の stateful control core / one-way control flow / reducer / command / stale result 方針を追記した
- `MEMORY.md` に state と制御判断の集約、event-shaped runtime、stale result 破棄の判断を追記した
- `AGENTS.md` に今後の作業規約として `TomoroSession` state/control 境界を追記した

### 詰まったこと・解決したこと
- `git diff --check` はユーザー更新済みの `_reference/2026-05-24-1200_設計評価と改善提案.md` に含まれる trailing whitespace で失敗した
  → 原文ファイルは変更せず、こちらが編集した Markdown 5 ファイルだけに絞って `git diff --check -- AGENTS.md ARCHITECTURE.md LOG.md MEMORY.md PLAN.md` を実行し、問題なしを確認した

### 次のセッションでやること
- 実装へ進む場合は Phase 8.8 の `ContextSnapshotBuilder` DTO / unit test か、Phase 8.8.5 の `SessionEvent` / `TransitionResult` DTO から着手する

## 2026-05-24 セッション20

### やること（開始時に書く）
- M2 Phase 8: 長期記憶（エピソード記憶）を実装する
- `conversation_logs` からローカル embedding を生成して pgvector に保存する
- `ThinkDeepMode` で類似検索した過去会話をプロンプトに差し込む
- 短い発話は fast、深い話題は deep に振り分ける最小モード選択を入れる

### 作業メモ
- 既存の Phase 7 方針を維持し、`conversation_logs` は role 行形式のまま長期記憶の原本として扱う
- WebSocket エンドポイントは増やさず、クライアント側判断も追加しない
- embedding 生成と記憶検索は DTO / DB 境界を守り、`server/gateway/thinking/*.py` は FastAPI に依存させない

### やったこと
- `MemoryHit` と `ThinkingInput.long_term_memory` を追加した
- `server/shared/inference/embedding/` に `intfloat/multilingual-e5-small` 用 embedding backend を追加した
- `conversation_embeddings` テーブルと pgvector HNSW index を追加した
- `PostgresConversationMemoryStore` を追加し、backfill / embedding 保存 / 類似検索を実装した
- `ThinkDeepMode` を追加し、top-K の過去会話を system prompt に差し込むようにした
- `should_use_deep_memory()` で短い発話は fast、記憶 cue や長めの相談文は deep に振り分けるようにした
- `TomoroSession` に deep mode / embedding backend / memory store を接続した
- 現在の user transcript 自身が deep memory 検索に混ざった場合は除外するようにした
- 起動時 warm-up に embedding backend を追加した
- `_tools/embed_conversation_logs.py` を追加し、既存 `conversation_logs` の embedding backfill を可能にした
- ローカル PostgreSQL に `conversation_embeddings` を適用し、既存 turn 3件を backfill した
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` / `ARCHITECTURE.md` に Phase 8 の結果を追記した

### 詰まったこと・解決したこと
- `_tools/embed_conversation_logs.py` を直接実行すると `server` package が import できなかった
  → repository root を `sys.path` に入れ、script 単体でも動くようにした
- `sentence-transformers` 追加後、初回 lock / install で `scikit-learn` などが入った
  → `uv.lock` を更新し、unit test で依存指定の PEP 508 妥当性を確認した
- 現在発話の embedding が非同期保存された場合、検索結果に自分自身が入る可能性がある
  → deep memory hits から同一 user transcript を除外する回帰テストを追加した

### 検証
- `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/003_conversation_embeddings.sql`
- `mise exec -- uv run python _tools/embed_conversation_logs.py --limit 3`
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -c "SELECT count(*) FROM conversation_embeddings;"`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

### 次のセッションでやること
- Chrome 実セッションで「この前話してた〇〇覚えてる？」が deep memory を引くか確認する
- 実会話ログが増えたら `_tools/embed_conversation_logs.py --limit 100` で backfill する
- 必要なら deep/fast selector の cue と長さ閾値を実ログに合わせて調整する

## 2026-05-24 セッション18

### やること（開始時に書く）
- M2 Phase 7: 短期記憶を進められるところまで実装する
- `conversation_logs` に会話ターンを保存し、`ThinkFastMode` の入力へ直近会話ターンを差し込む
- 疑問点は人間の希望に従い LOG.md に記録しつつ、破壊的でない範囲は仮実装で進める

### 作業メモ・疑問点（仮実装で進める）
- Phase 7 の会話ログ schema は PLAN では `(user_text, tomoko_text, timestamp, emotion)` だけ指定されている。実装では将来の device/speaker/attention 分析に備えて `device_id` / `speaker` / `participation_mode` / `attention_mode` / `created_at` も持たせる。ただし短期文脈ではまず `user_text` / `tomoko_text` / `emotion` / `created_at` だけ使う。
- `TomoroSession` は reply/TTS を background task 化済みなので、会話ログ保存タイミングは `reply_done` 後にする。hard interrupt / cancel された未完了返答は短期記憶に保存しない仮方針で進める。

### 作業メモの訂正
- 上の schema メモにある `attention_mode` / `created_at` は今回の `conversation_logs` には追加しない。既存 schema は `recorded_at` / `device_id` / `speaker` / `role` / `transcript` / `emotion` / `participation_mode` で足りるため、Phase 7 ではテーブル変更なしで進めた。

### やったこと
- `ConversationLogWriter.read_recent_turns()` を追加し、PostgreSQL から直近 `conversation_logs` を `ConversationTurn` として読めるようにした
- `ThinkFastMode` が `ThinkingInput.context` を OpenAI 互換 messages の `user` / `assistant` role として current user message の前に差し込むようにした
- `TomoroSession` が reply 生成時に直近 12 turn を読み込むようにした
- user turn は reply task 起動前に保存済みなので、現在の transcript と同じ末尾 user turn は context から除外して重複を避けた
- `tests/unit/test_phase4_thinking.py` に短期文脈差し込みの unit test を追加した
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` に Phase 7 の結果を追記した

### 詰まったこと・解決したこと
- Phase 7 の PLAN は「会話ターンごとに `(user_text, tomoko_text, timestamp, emotion)`」と書いているが、既存実装は `role=user` / `role=tomoko` の行として保存していた
  → テーブルを作り直さず、role 行を `ConversationTurn` に戻して短期文脈として使う方針にした
- current user turn を先に DB へ保存するため、そのまま読むと current user message が context と current input の両方に入る
  → `TomoroSession._load_recent_context()` で末尾の同一 user turn を落とすようにした

### 検証
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

### 次のセッションでやること
- Chrome 実セッションで「さっき言った〇〇のことだけど」が文脈付きで返るか確認する
- M2 Phase 8: embedding / pgvector / `ThinkDeepMode` の設計に進む

## 2026-05-24 セッション19

### やること（開始時に書く）
- 人間判断を反映する
  - `conversation_logs` は role 形式のままで進める
  - `conversation_logs.status TEXT NOT NULL DEFAULT 'completed'` を追加する
  - 止められた Tomoko 返答は `status='interrupted'` で保存する

### やったこと
- `conversation_logs` に `status TEXT NOT NULL DEFAULT 'completed'` を追加する DDL を入れた
- 既存ローカル PostgreSQL に `ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'completed';` を適用した
- `ConversationLogStatus = completed | interrupted | cancelled | error` を DTO 側に追加した
- 通常完了した Tomoko 返答は `status='completed'`、hard interrupt で止められた生成済み途中返答は `status='interrupted'` で保存するようにした
- 短期記憶の `read_recent_turns()` は `status='completed'` だけを context に使うようにした
- hard interrupt 中の TTS cancel で interrupted turn が保存される unit test を追加した

### 詰まったこと・解決したこと
- `reply_done` 前に cancel されると、`ReplyPipeline` 内の途中テキストがローカル変数のまま消える
  → `CancelledError` を受けた `_reply_to()` で `reply.reply_text` を `interrupted` として保存するようにした
- `_start_reply_task()` でも既存 reply task を cancel するため、全部を `interrupted` にすると「止められた」以外も混ざる
  → `_cancel_reply_generation(status=...)` で理由を渡し、hard interrupt だけ `interrupted` にした

### 検証
- `docker exec tomoko-postgres psql -U tomoko -d tomoko -c "\\d conversation_logs"` で `status` カラム確認
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

### 次のセッションでやること
- Chrome 実セッションで hard interrupt した時に `conversation_logs.status='interrupted'` が残ることを確認する
- M2 Phase 8: embedding / pgvector / `ThinkDeepMode` の設計に進む

## 初回（設計フェーズ）2026-05-23

### やったこと
- ARCHITECTURE.md / PLAN.md / AGENTS.md / README.md / LICENSE を作成
- マイルストーン M1〜M5 を設定
- 層間 DTO の型定義を設計
- TTS: M1フェーズは say、完了後に kokoro-mlx の方針を確定
- LLM: Ollama → MLX の移行方針を確定

### 確定した主な設計判断
- WebSocket は 1 本
- PostgreSQL が唯一の真実、pub/sub なし
- プールは utterance_candidates 一本、取り出しは SelectionStrategy
- arrival_candidates は使い捨て前提（3分ごとに作り直す）
- VAD ホットループはプリミティブのまま、境界でだけ DTO に包む
- git push origin は人間のみ許可

### 次のセッションでやること
- Phase 0: 環境構築から開始
- MEMORY.md に確定済み判断を整理してから実装開始

## 2026-05-23 セッション1

### やること（開始時に書く）
- M1 Phase 0: Python/uv、PostgreSQL、Ollama/MLX、TTS、STT/VAD、pytest、初期設定ファイルの環境構築

### やったこと
- `mise.toml` に uv を追加し、Python 3.11.15 + uv 0.11.16 の開発環境を作成
- `pyproject.toml` / `uv.lock` / pytest markers / ruff 設定を追加
- `config/central_realtime.toml` を作成し、M1 は Ollama `qwen2.5:7b` + `say` を使う設定にした
- PostgreSQL 用 `docker-compose.yml` と `docker/postgres/` を追加し、pgvector / PGroonga 拡張を有効化
- `NodeConfig` の最小 TOML ローダーと Phase 0 unit test を追加
- Ollama を Homebrew で導入し、`qwen2.5:7b` を pull 済み
- `mlx-lm` を導入し、`mlx-community/Qwen2.5-7B-Instruct-4bit` のロード確認済み
- faster-whisper small と Silero VAD のロード確認済み
- macOS `say -v Kyoko` の音声ファイル生成確認済み

### 詰まったこと・解決したこと
- Silero VAD のロードに `torchaudio` が必要だったため依存に追加して解決
- Docker Desktop が未起動だったため起動し、PostgreSQL コンテナの healthcheck と拡張確認まで実施
- `irodori-tts` は Homebrew/PyPI に無く、ローカルサービスも未起動。公式 GitHub 実装を外部依存としてどう扱うかは人間確認待ち

### 次のセッションでやること
- `irodori-tts` を外部リポジトリとして導入するか、M1 では `say` のみで進めるか判断する
- Phase 1: WebSocket 1本で float32 エコーバックを実装する

## 2026-05-23 セッション2

### やること（開始時に書く）
- M1 Phase 0 の残対応確認。環境構築項目を再検証し、未完了項目が実作業で解消可能なら対応する

### やったこと
- Phase 0 の環境を再検証した
  - Python 3.11.15 / uv 0.11.16 は `mise exec -- uv ...` で利用可能
  - PostgreSQL コンテナは healthy、`vector` / `pgroonga` 拡張を確認
  - Ollama `qwen2.5:7b` が pull 済みであることを確認
  - `mlx_lm` / faster-whisper small / Silero VAD / macOS `say -v Kyoko` を確認
- `pyproject.toml` に混入していた不正な依存指定を削除し、`uv run pytest -m unit` が通る状態に戻した
- PEP 508 として壊れた依存指定を検出する unit test を追加した
- 公式 `Aratako/Irodori-TTS-Server` を隣接ディレクトリ `../Irodori-TTS-Server` に clone し、`uv sync` と `GET /health` 200 応答を確認した

### 詰まったこと・解決したこと
- 通常の shell PATH では `uv` が見えなかった
  → Tomoko リポジトリ内では `mise exec -- uv ...` を使えば動作することを確認
- `uv run pytest -m unit` が `pyproject.toml` の `pytest=*` / `pytest-asyncio=*` で失敗した
  → 不正行を削除し、PEP 508 検証テストを追加して再発を検出できるようにした
- irodori は PyPI/Homebrew パッケージではなく、公式 OpenAI 互換サーバーを外部サービスとして扱うのが現時点の最小導入と判断した

### 次のセッションでやること
- Phase 1: WebSocket 1本で float32 エコーバックを実装する
- irodori の音声合成実推論は M1 完了後、または TTSBackend 実装時にモデルロード時間と品質を測る

## 2026-05-23 セッション3

### やること（開始時に書く）
- M1 Phase 1: WebSocket 1本で float32 エコーバックを実装する

### やったこと
- `server/edge/main.py` を追加し、`/ws` で受け取ったバイナリを変換せずそのまま返す Phase 1 エコーバックを実装した
- `client/audio-worklet.js` で AudioWorklet から 32ms チャンクの float32 を取得する実装を追加した
- `client/main.js` / `client/index.html` / `client/styles.css` を追加し、マイク入力を WebSocket に送り、返ってきた float32 を AudioContext で再生する最小画面を作った
- `/ws` のバイナリエコーを検証する unit test を追加した
- `_docs/latency.md` にローカル `/ws` echo round trip の実測値を追記した

### 詰まったこと・解決したこと
- AudioWorkletNode は音声グラフ上で接続されていないと処理が止まる可能性があるため、無音 GainNode 経由で destination に接続して処理を維持するようにした
- in-app browser の `iab` がこの環境で利用できず画面の自動確認はできなかったため、HTTP 配信は `curl`、WebSocket は TestClient と実測スクリプトで確認した

### 次のセッションでやること
- Chrome で `http://127.0.0.1:8000` を開き、マイク許可後に実音声エコーが返ることを手動確認する
- Phase 2: Silero VAD で発話開始・終了の状態遷移を実装する

## 2026-05-23 セッション4

### やること（開始時に書く）
- M1 Phase 2: Silero VAD ラッパー、TomoroSession 初版、state イベント送信、クライアント state 表示、状態遷移 unit test を実装する

### やったこと
- `server/edge/pipeline/vad.py` に `SileroVAD` ラッパーと `VADProcessor` を追加した
- `server/session.py` に `TomoroSession` 初版を追加し、`idle` / `listening` / `processing` の状態遷移と state イベント送信を実装した
- 既存 `/ws` に VAD 処理を組み込み、バイナリエコーを維持したまま `{type: "state"}` JSON イベントを送るようにした
- クライアントに VAD state 表示を追加した
- `tests/unit/test_vad.py` と WebSocket state イベントテストを追加した
- `_docs/latency.md` に 300 / 400 / 500ms の無音閾値検出タイミングを追記した

### 詰まったこと・解決したこと
- unit test では Silero 実モデルをロードしないよう、VAD scorer を注入できる設計にした
- in-app browser の `iab` がこの環境で利用できず画面の自動確認はできなかったため、HTTP 配信は `curl`、WebSocket は TestClient で確認した

### 次のセッションでやること
- Chrome で `http://127.0.0.1:8000` を開き、マイク許可後に実音声で "listening" / "processing" が表示されることを手動確認する
- Phase 3: 常時 STT と ParticipationJudge の実装に進む

## 2026-05-23 セッション5

### やること（開始時に書く）
- M1 Phase 3: 常時 STT、ambient_logs、ParticipationJudge / WakeWordJudge、参加判断 unit test を実装する

### やったこと
- `ambient_logs` テーブル DDL を追加し、既存ローカル PostgreSQL にも適用した
- `Transcript` / `ParticipationDecision` DTO を追加した
- `FasterWhisperSTT` ラッパーを追加し、発話終了後に `SpeechSegment` を文字起こしする流れを実装した
- `ParticipationJudge` 抽象と `WakeWordJudge` を追加した
- `TomoroSession` に STT → ambient_logs → 参加判断 → idle 復帰の処理を追加した
- Wake word と常時 STT 経路の unit test を追加した
- Phase 3 の参加判断・DB 書き込みレイテンシーを `_docs/latency.md` に追記した

### 詰まったこと・解決したこと
- 初期化済み PostgreSQL には新しい docker init SQL が自動適用されないため、同じ DDL を手元 DB に直接適用して確認した
- faster-whisper の同期 transcribe がイベントループを塞がないよう、`asyncio.to_thread` に逃がした

### 次のセッションでやること
- Chrome 実音声で「トモコ」が `participation` イベントを出し、それ以外が `ambient_logs` にのみ残ることを手動確認する
- Phase 4: LLM ストリーミングで返答テキストを実装する

## 2026-05-23 セッション6

### やること（開始時に書く）
- Phase 2 の Chrome 手動確認結果を受けて、手動テストを妨げるエコーバック再生を止める
- M1 Phase 3 実装済み範囲を再確認し、常時 STT と ParticipationJudge の unit test が通る状態にする

### やったこと
- `/ws` が受け取った float32 バイナリを返送しないようにし、Phase 1 のエコーバックを停止した
- クライアントからエコー再生と往復レイテンシー計測を削除し、`participation` JSON イベントを status に出すようにした
- WebSocket unit test を「バイナリを受けるが返さない」「state JSON は送る」前提へ更新した
- Phase 3 実装済みの常時 STT / ambient_logs / WakeWordJudge 経路を unit test で再確認した

### 詰まったこと・解決したこと
- Chrome / in-app browser の自動接続はこの環境では利用できなかった
  → 既存 uvicorn reload サーバーと `curl http://127.0.0.1:8000/` でページ配信は確認済み。実マイク確認はユーザー側 Chrome で行う
- `mise exec -- ruff` は PATH 上に `ruff` が無かった
  → `mise exec -- uv run ruff check .` で確認した

### 次のセッションでやること
- Chrome 実音声で「トモコ」が `participation:called` 表示になり、それ以外が `ambient_logs` にのみ残ることを確認する
- Phase 4: LLM ストリーミングで返答テキストを実装する

## 2026-05-23 セッション7

### やること（開始時に書く）
- M1 Phase 4: LLM ストリーミングで返答テキストが完了しているか確認し、未完了箇所があればテスト先行で対応する

### やったこと
- `ThinkingInput` / `ThinkingEvent` DTO を追加し、`ThinkFastMode` から token delta を DTO で返すようにした
- `TomoroSession` で参加判定後に `InferenceRouter` 経由で LLM backend を選び、`reply_text` delta を WebSocket に順次送信する経路を確認した
- `InferenceRouter` に実測 latency による fallback と privacy 時の非 private fallback 禁止を追加した
- Phase 4 の thinking/session/router unit test を追加した
- Ollama `qwen2.5:7b` の初回 text delta レイテンシーを測定し、`_docs/latency.md` に記録した

### 詰まったこと・解決したこと
- 既存実装は `ThinkingMode -> session` が `str` 直渡しだった
  → `ThinkingEvent` DTO に包み、`reply_done` も明示イベントにした
- Ollama cold start 込みの初回 delta が 17931.7ms と大きい
  → Phase 4 の機能完了とは別に、M1 800ms 目標には warm-up / MLX / 事前生成の検討が必要

### 次のセッションでやること
- Chrome 実音声で「トモコ」に対して `reply_text` が画面にストリーミング表示されることを手動確認する
- Phase 5: TTS ストリーミングで声を出す

## 2026-05-23 セッション8

### やること（開始時に書く）
- M1 Phase 5: `say` ベースの TTSBackend、句読点単位の TTS 起動、WebSocket 音声バイナリ送信、クライアント再生、TTS レイテンシー計測を実装する

### やったこと
- `TTSInput` / `AudioChunkOut` DTO と `TTSBackend` 抽象を追加した
- `SayBackend` を追加し、`config/central_realtime.toml` の `tts_backend = "say"` から生成できるようにした
- `TomoroSession` で LLM の `text_delta` を蓄積し、句点・感嘆符・疑問符で TTS に流すようにした
- `/ws` で TTS 音声チャンクをバイナリ送信するようにした
- クライアントで WebSocket バイナリを `decodeAudioData` し、`AudioBufferSourceNode` をスケジューリング再生するようにした
- Phase 5 の unit test と perf test を追加し、`_docs/latency.md` に実測値を追記した

### 詰まったこと・解決したこと
- macOS `say` は真の逐次 PCM ストリーミングではなく AIFF ファイル生成型なので、M1 では句読点単位で 1 AIFF チャンクとして送る実装にした
- in-app browser はこの環境では `iab` が利用できず画面の自動確認はできなかった
  → `curl http://127.0.0.1:8000/` で HTTP 200、unit/perf で WebSocket/TTS 経路を確認した

### 次のセッションでやること
- Chrome 実音声で「トモコ」に対して声が返ることを手動確認する
- Phase 6a: 感情情報を DOM に出し、TTS に流す本文から emotion 行を分離する

## 2026-05-23 セッション9

### やること（開始時に書く）
- M1 Phase 5 の手動確認で「返答テキストは来るが音声が再生されない」問題を切り分け、テスト先行で修正する

### やったこと
- Chrome の `decodeAudioData` 互換性を優先し、`SayBackend` の出力を AIFF から 16kHz/16bit RIFF/WAVE に変更した
- クライアントで音声チャンク再生前に `AudioContext.resume()` を呼ぶようにした
- Phase 5 の unit/perf test を WAV 前提に更新した

### 詰まったこと・解決したこと
- `say -o speech.wav` だけでは `Opening output file failed: fmt?` で WAV を生成できなかった
  → `--data-format=LEI16@16000 -o speech.wav` を指定すると RIFF/WAVE を生成できることを確認した

### 次のセッションでやること
- Chrome 実音声で「トモコ」に対して声が返ることを再確認する
- まだ無音なら Chrome コンソールの `audio-error` と WebSocket バイナリ受信有無を確認する

### 人間確認
音声出ました。
codex tomoko: Phase 6a: 感情情報を DOM に出し、TTS に流す本文から emotion 行を分離する を対応して下さい。

## 2026-05-23 セッション10

### やること（開始時に書く）
- M1 Phase 6a: `EMOTION:<value>` 行を返答本文から分離し、emotion イベントを DOM に表示し、TTS には本文だけを流す

### やったこと
- `prompts/base_persona.md` に `EMOTION:<emotion>` 形式の出力指示を追加した
- `ThinkFastMode` でストリーム先頭の emotion 行を分離し、`ThinkingEvent(type="emotion")` を送るようにした
- `TomoroSession` で emotion イベントを WebSocket JSON として送信し、TTS style に反映した
- TTS には emotion 行を含めず、本文の `text_delta` だけを流すようにした
- クライアント DOM に `#emotion` 表示を追加した
- Phase 6a の unit test を追加し、`_docs/latency.md` に perf 再測定を追記した

### 詰まったこと・解決したこと
- LLM ストリームで `EMOTION:` が複数チャンクに分割される可能性がある
  → 先頭行だけバッファし、`EMOTION:` の prefix 途中なら待つ実装にした
- in-app Browser はこの環境で `iab` が利用できず視覚確認はできなかった
  → `uvicorn` + `curl` で HTML/JS 配信と `#emotion` DOM の存在を確認した

### 次のセッションでやること
- Chrome 実音声で emotion 表示が返答内容に応じて変わり、音声に `EMOTION:` が読み上げられないことを手動確認する
- Phase 6b: 静止画切り替えと声のトーンに進む

### 添付画像確認
- `/Users/seijiro/Sync/sync_work/by-llms/tomoko/.fyws/inputs/1507640092045475910/CleanShot_2026-05-23_at_16.03.162x.png`: 936x938。Tomoko UI、`stopped`、State `listening`、STT `-`、Sent `2226176`、Start/Stop ボタン、下部に「こんにちは！何か手伝えることがありますか？」が見える。

### 人間確認
#### Chrome 実音声で emotion 表示が返答内容に応じて変わり、音声に `EMOTION:` が読み上げられないことを手動確認する

## 2026-05-23 セッション11

### やること（開始時に書く）
- M1 Phase 6a 手動確認で `EMOTION:happy` が読み上げ・本文表示に漏れた問題を、テスト先行で修正する

### やったこと
- 添付画像から、LLM が `EMOTION:happy` の後に改行を入れず本文を続けたケースであることを確認した
- `ThinkFastMode` に `EMOTION:<value> 本文` 形式の分離を追加した
- `EMOTION:happy` が `reply_text` と TTS に流れない unit test を追加した

### 詰まったこと・解決したこと
- 既存実装は `EMOTION:<value>\n本文` だけを分離対象にしていた
  → 許可済み emotion の直後に空白と本文が続く場合も、emotion イベントと本文 delta に分離するよう修正した

### 次のセッションでやること
- Chrome 実音声で `EMOTION:` が読み上げられず、本文表示にも出ないことを再確認する
- Phase 6b: 静止画切り替えと声のトーンに進む

## 2026-05-23 セッション12

### やること（開始時に書く）
- Phase 6b: emotion イベントで静止画を音声より先に切り替え、TTS の声色を emotion に応じて変える
- `asset-factory_for_work/` で立ち絵画像を生成し、`assets/images/` に配置する

### やったこと
- `asset-factory_for_work/generate_tomoko_assets.py` を追加し、7 emotion 分の Tomoko 静止画 SVG を `assets/images/` に生成した
- `/assets` を FastAPI の StaticFiles として配信するようにした
- emotion イベントに `image` フィールドを追加し、音声チャンク送信より先に届くことを unit test で固定した
- クライアントは WebSocket で届いた `image` を `#tomoko-image` に表示するようにした
- `SayBackend` の emotion rate マッピングに `surprised` を追加し、全 emotion の rate を unit test で固定した
- `_docs/latency.md` に Phase 6b の perf 再測定を追記した

### 詰まったこと・解決したこと
- in-app Browser はこの環境で `iab` が利用できず視覚確認できなかった
  → `GET /` 200 と `/assets/images/tomoko-happy.svg` 200 (`image/svg+xml`) で配信確認した
- irodori backend はまだ Tomoko リポジトリ内に無いため、Phase 6b の声色は既存 `SayBackend` の rate 表現として実装した

### 次のセッションでやること
- Chrome 実音声で emotion に応じて静止画が切り替わり、声の速さが変わることを手動確認する
- M2 Phase 7: 短期記憶に進む

## 2026-05-23 セッション13

### やること（開始時に書く）
- wake word 後の会話継続、ambient 聞き取り復帰、「聞いてなかった」扱いを今後の前提設計として `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に追記する

### やったこと
- `ARCHITECTURE.md` に AttentionMode と `recorded` / `attended` / `remembered` の分離を追記した
- `PLAN.md` に Phase 7 の前に Phase 6.5 を追加する方針と完了条件を追記した
- `MEMORY.md` に Phase 7 前に AttentionMode を実装する判断を追記した

### 詰まったこと・解決したこと
- 既存 PLAN では Phase 6b の次が Phase 7 だった
  → 既存内容は編集せず、追記でその順序を否定して Phase 6.5 を先に実装する方針にした

### 次のセッションでやること
- Phase 6.5: `TomoroSession` に `attention_mode` を追加し、wake word 後の会話継続と ambient 復帰を unit test 先行で実装する

## 2026-05-23 セッション14

### やること（開始時に書く）
- MacBook スピーカー音声が内蔵マイクへ回り込む問題に対して、まずクライアント側 AEC だけを有効化して改善するか確認できる状態にする

### やったこと
- `client/main.js` の `getUserMedia` 制約で `echoCancellation` と `noiseSuppression` を有効化した

### 詰まったこと・解決したこと
- サーバー側の speaker echo filter はまだ入れない
  → AEC だけで劇的に改善する可能性があるため、最小変更で実測する

### 次のセッションでやること
- Chrome 実音声で Tomoko の TTS が再度 STT/参加判定に入るか確認する
- まだ回り込む場合は、TTS 再生中の時間窓 + 文字列類似度で speaker_echo を observer 扱いにする

## 2026-05-23 セッション14

### やること（開始時に書く）
- Phase 6.5: `TomoroSession` に `attention_mode` を追加し、wake word 後の会話継続、cooldown 経由の ambient 復帰、withdrawn、ambient/conversation log 境界を unit test 先行で実装する

### やったこと
- `AttentionMode` / `ParticipationMode` / `ParticipationContext` を DTO として `server/shared/models.py` に追加した
- `TomoroSession` に `attention_mode` を集約し、`ambient -> engaged -> cooldown -> ambient` と `withdrawn` の遷移を実装した
- `WakeWordJudge` を `attention_mode` 前提に拡張し、`engaged` / `cooldown` 中の継続発話を `invited` として扱うようにした
- `ambient_logs` に `attention_mode` / `attended` / `participation_mode` を保存し、`conversation_logs` には attended な会話ターンだけを書く writer を追加した
- 既存ローカル PostgreSQL に Phase 6.5 の DDL を適用した
- `tests/unit/test_attention_mode.py` を追加し、wake word 後の継続、ambient 復帰、withdrawn、会話ログ境界を固定した
- `_docs/latency.md` に Phase 6.5 の perf 再測定結果を追記した

### 詰まったこと・解決したこと
- 既存 unit/perf の in-memory ambient writer が旧シグネチャだった
  → `attention_mode` / `attended` / `participation_mode` を受け取る形に更新した
- init SQL は既存 PostgreSQL volume には自動再適用されない
  → `docker exec -i tomoko-postgres psql -U tomoko -d tomoko < docker/postgres/init/002_ambient_logs.sql` で手元 DB に適用した

### 次のセッションでやること
- Chrome 実音声で「トモコ」後の wake word なし継続発話が `invited` になり、無発話で `cooldown` / `ambient` に戻ることを確認する
- 「静かにして」で `withdrawn` になり、以後の発話に返答しないことを手動確認する
- Phase 7: attended な `conversation_logs` を短期記憶として `ThinkFastMode` の context に差し込む

## 2026-05-23 セッション15

### やること（開始時に書く）
- Tomoko 発話中の「ちょっと待って」「違う違う」「待って待って」などの割り込み検出を Phase 6.6.0 として `PLAN.md` / `MEMORY.md` に追記する

### やったこと
- `PLAN.md` に Phase 6.6.0 TurnTaking / BargeInDetector を追記した
- `MEMORY.md` に Tomoko 発話中もマイク入力を止めず、割り込みを分類する判断を追記した

### 詰まったこと・解決したこと
- 旧 Unity 実装では `isAITalking` 中に録音処理を止めていた
  → 今回は人間の割り込みを拾うため、その方針を否定し、STT 継続 + BargeInDetector で分類する設計にした

### 次のセッションでやること
- Phase 6.6.0: `BargeInDetector` を unit test 先行で実装し、echo / backchannel / soft_interrupt / hard_interrupt / new_question を分類する

## 2026-05-23 セッション16

### やること（開始時に書く）
- AEC だけでは MacBook スピーカー回り込みを防げなかったため、Phase 6.6.0 の初期実装として `BargeInDetector` と TTS 再生時間窓ベースの speaker echo / 割り込み分類を追加する

### やったこと
- `BargeInContext` / `BargeInDecision` DTO を追加した
- `server/gateway/turn_taking/barge_in.py` に `BargeInDetector` を追加し、`echo` / `backchannel` / `soft_interrupt` / `hard_interrupt` / `new_question` を分類するようにした
- `TomoroSession` が TTS 音声送信後に再生時間窓を推定し、その窓内の transcript を `BargeInDetector` に通すようにした
- `/ws` のデフォルト `TomoroSession` 生成時にも `BargeInDetector` を渡すようにした
- `echo` / `backchannel` は `observer` 相当にして返答ループへ入れず、hard interrupt は通常の参加判定へ進めるようにした
- `tests/unit/test_barge_in.py` を追加した
- `_docs/latency.md` と `MEMORY.md` に AEC だけでは不十分だった判断と Phase 6.6.0 初期実装を追記した

### 詰まったこと・解決したこと
- Tomoko 発話開始直後の startup grace が hard interrupt まで潰していた
  → `echo` 判定の次に hard interrupt を優先し、その後に startup grace を適用する順序にした
- 現在の `/ws` ループは返答生成/TTS送信を `process_audio_chunk()` 内で await している
  → 今回は TTS 送信後の推定再生窓で回り込みを分類する初期実装に留め、真の同時割り込みは後続の並行化対象にする

### 次のセッションでやること
- Chrome 実音声で Tomoko 音声の回り込みが `barge_in: echo` になり、返答ループしないことを確認する
- Tomoko 発話中の「違う違う」「待って待って」が `hard_interrupt` になり、通常の参加判定へ進むことを確認する
- 必要なら `/ws` 受信ループと reply/TTS 生成を並行化し、真の同時 barge-in に対応する

## 2026-05-23 セッション17

### やること（開始時に書く）
- Phase 6.6.1 AudioPlaybackControl を追加し、`turn_id` 付きの `audio_start` / `audio_end` / `audio_control stop` とクライアント側 source 停止を実装する

### やったこと
- `PLAN.md` に Phase 6.6.1 AudioPlaybackControl を追記した
- `MEMORY.md` に `turn_id` と `audio_start` / `audio_end` / `audio_control stop` の判断を追記した
- `TomoroSession` が返答ごとに `turn_id` を発行し、最初の音声バイナリより前に `audio_start`、返答完了時に `audio_end` を送るようにした
- hard interrupt 時に `audio_control stop` を送るようにした
- クライアントが `turn_id` ごとに `AudioBufferSourceNode` を保持し、サーバー命令で再生中/予約済み source を止めるようにした
- `tests/unit/test_phase5_tts.py` と `tests/unit/test_barge_in.py` に audio control の regression test を追加した
- `_docs/latency.md` に Phase 6.6.1 の検証結果を追記した

### 詰まったこと・解決したこと
- `audio_end` 後もクライアントでは再生が残っている可能性がある
  → `turn_id` は `audio_end` 後もサーバー側に保持し、再生時間窓内の hard interrupt で stop できるようにした

### 次のセッションでやること
- Chrome 実音声で hard interrupt 時に再生中/予約済み音声が止まることを確認する
- 必要なら `playback_started` / `playback_ended` テレメトリを追加して、サーバー側の推定再生窓を実測に置き換える

## 2026-05-23 セッション18

### やること（開始時に書く）
- git log と実装済みファイルを確認し、Phase 6.6.1 までの完了済み項目を `PLAN.md` のチェックボックスへ反映する

### やったこと
- `git log` / `LOG.md` / `MEMORY.md` / 実装ファイルを照合し、Phase 0 〜 Phase 6.6.1 の完了済み項目を `PLAN.md` でチェックした
- KokoroMLXBackend、Phase 7 以降、`playback_started` / `playback_ended` テレメトリなど未実装の項目は未チェックのまま残した

### 詰まったこと・解決したこと
- 作業ツリーには Phase 6.6.1 相当の未コミット変更が含まれていたため、既存変更を戻さず `PLAN.md` のチェック更新だけに絞った

### 次のセッションでやること
- Chrome 実音声で hard interrupt 時に再生中/予約済み音声が止まることを確認する
- 必要なら `playback_started` / `playback_ended` テレメトリを追加して、サーバー側の推定再生窓を実測に置き換える

## 2026-05-23 セッション19

### やること（開始時に書く）
- デバッグしやすいように、サーバーから届く `attention` イベントを画面に表示する

### やったこと
- クライアントのメーターに `Attention` 表示を追加した
- `client/main.js` で `{type: "attention", mode: ...}` を受け取り、`ambient` / `engaged` / `cooldown` / `withdrawn` をそのまま表示するようにした

### 詰まったこと・解決したこと
- サーバー側は既に attention イベントを送っていたため、クライアント表示だけの変更で対応した

### 次のセッションでやること
- Chrome 実音声で `Attention` が `engaged -> cooldown -> ambient` に遷移することを確認する

## 2026-05-23 セッション20

### やること（開始時に書く）
- クライアントから `playback_started` / `playback_ended` telemetry を `/ws` に返し、サーバー側で受け取れる土台を作る

### やったこと
- `PlaybackTelemetry` DTO を追加した
- クライアントが `AudioBufferSourceNode` の再生予定時刻に `playback_started`、`ended` イベントで `playback_ended` を `/ws` へ JSON 送信するようにした
- `/ws` 受信ループを binary 音声チャンクと JSON text event の両対応にした
- `TomoroSession` が playback telemetry を保持して log.info に残すようにした
- telemetry はまだ barge-in 判定には使わず、実測後に料理する前提にした

### 詰まったこと・解決したこと
- 既存 `/ws` は `receive_bytes()` 固定だった
  → `receive()` に変更し、bytes は音声、text は playback telemetry として分岐するようにした

### 次のセッションでやること
- Chrome 実音声で `playback_started` / `playback_ended` がサーバーログに出ることを確認する
- telemetry と回り込み transcript の時刻差を見て、speaker echo 窓への反映方法を決める

## 2026-05-23 セッション21

### やること（開始時に書く）
- サーバーログの見方を明確にし、正しいサーバー起動コマンドを `Makefile` に追加する

### やったこと
- `Makefile` を追加し、現在の正しい ASGI app である `server.edge.main:app` を `make server` / `make server-reload` で起動できるようにした
- `server.*` logger の info ログが uvicorn のターミナルに出るようにした

### 詰まったこと・解決したこと
- `README.md` の `server.gateway.main:app` は現状の実装とズレている
  → Makefile では現行の `server.edge.main:app` を正として固定した

### 次のセッションでやること
- `make server` で起動し、Chrome 実音声で playback telemetry の `log.info` が表示されることを確認する

## 2026-05-23 セッション22

### やること（開始時に書く）
- `make server-reload` のターミナルに playback telemetry の `logger.info` が出ない原因を確認して修正する

### やったこと
- `server.*` logger を `uvicorn.error` の handler に接続し、`server.session` の `log.info` が uvicorn 起動ターミナルに出るようにした

### 詰まったこと・解決したこと
- uvicorn のアクセスログは出ていたが、アプリ側 `server.*` logger は handler に接続されていなかった
  → `server.edge.main` の import 時に `server` logger へ uvicorn handler を接続した

### 次のセッションでやること
- `make server-reload` を再起動し、Chrome 実音声で `TomoroSession playback telemetry...` が出ることを確認する

## 2026-05-23 セッション23

### やること（開始時に書く）
- `make server-reload` でまだアプリ側 `server.*` ログが出ない問題を修正する

### やったこと
- uvicorn handler が import 時点で取得できない場合でも `server.*` ログを stderr に出す fallback handler を追加した

### 詰まったこと・解決したこと
- 前回修正は `uvicorn.error` handler が存在する前提だった
  → handler が空の場合に `server_logger.propagate = False` でログが捨てられていたため、専用 `StreamHandler` を追加した

### 次のセッションでやること
- `make server-reload` を再起動し、WebSocket 接続時に `INFO:server.edge.main:phase4 websocket connected` が出ることを確認する

## 2026-05-23 セッション24

### やること（開始時に書く）
- playback telemetry を chunk 単位にし、`playback_ended` 後の回り込み猶予で自己会話ループを抑止する

### やったこと
- クライアントの `playback_started` / `playback_ended` payload に `chunk_id` / `scheduled_audio_time` / `sent_audio_time` を追加した
- サーバーの `PlaybackTelemetry` DTO と `/ws` JSON parser を chunk 単位 telemetry に対応させた
- `TomoroSession` に `playback_ended + 1200ms` の speaker echo grace を追加した
- grace 中の transcript は hard interrupt 以外 `echo` / `continue_speaking` として通常参加判定へ流さないようにした
- regression test を追加した

### 詰まったこと・解決したこと
- 同一 `turn_id` に複数 audio chunk があるため、turn 単位 telemetry だけでは再生区間を特定しにくかった
  → chunk ごとに `chunk_id` を振り、開始/終了イベントを対応付けられるようにした

### 次のセッションでやること
- Chrome 実音声で `playback_ended` 直後の回り込みが `reason=playback_ended_grace` で observer 扱いになることを確認する

## 2026-05-23 セッション25

### やること（開始時に書く）
- サーバーのアプリログを確実に出し、必要ならファイルから LLM が読めるようにする

### やったこと
- `server.*` logger を uvicorn handler 依存から外し、stderr と `TOMOKO_LOG_FILE` の両方へ必ず出すようにした
- 通常起動では `logs/server.log` に出力するようにした
- `make server-debug` を追加し、DEBUG レベルで起動しつつ stdout/stderr も `logs/server-debug.log` へ `tee` するようにした
- `README.md` にログファイルと `make server-debug` を追記した

### 詰まったこと・解決したこと
- uvicorn reload 時の import 順序により、uvicorn handler へ接続する方式は不安定だった
  → アプリ側で専用 stderr handler と file handler を持つ方式にした

### 次のセッションでやること
- `make server-debug` で実音声確認し、`logs/server-debug.log` から playback telemetry と barge-in の時系列を読む

## 2026-05-23 セッション26

### やること（開始時に書く）
- `make server-debug` の実ログから回り込み軽減の効き方を確認し、ログが読みにくい問題を直す

### やったこと
- `logs/server-debug.log` を確認し、`playback_ended_grace` により少なくとも1回は自己会話候補が `echo` 扱いで抑止されていることを確認した
- `server-debug` で同じ `server.session` ログが二重にファイルへ入る原因を修正した
- `server-debug` は `tee` で stdout/stderr を1本の `logs/server-debug.log` に集約し、アプリ側 file handler は使わないようにした
- `server-debug` の uvicorn log level を `info` に戻し、WebSocket binary frame dump で会話ログが埋もれないようにした

### 詰まったこと・解決したこと
- `TOMOKO_LOG_FILE=logs/server-debug.log` と `tee -a logs/server-debug.log` を同時に使っていたため、アプリログだけ二重記録されていた
  → `TOMOKO_LOG_FILE` を空にしたときは file handler を張らないようにし、debug target では `tee` だけで記録する方式にした

### 次のセッションでやること
- 新しい `make server-debug` で実音声確認し、重複なしのログで `playback_ended_grace` と `attention_engaged_followup` の残り方を比較する

## 2026-05-23 セッション27

### やること（開始時に書く）
- `playback_ended` 後の回り込み猶予を2秒へ延長し、再生中 active chunk 区間でも自己会話を抑止する

### やったこと
- `TomoroSession` の `playback_echo_grace_ms` デフォルトを 1200ms から 2000ms に変更した
- `playback_started` / `playback_ended` の `(turn_id, chunk_id)` を active playback chunk として管理するようにした
- active playback chunk が存在する間は hard interrupt 以外を `echo` / `continue_speaking` に倒し、通常の参加判定へ流さないようにした
- active chunk 中の通常 follow-up 抑止、active chunk 中の hard interrupt 通過、2秒猶予の unit test を追加した

### 詰まったこと・解決したこと
- `playback_ended` 後の猶予だけでは、次 chunk が再生中のタイミングで拾った音が `new_question` として参加判定へ進む
  → クライアント telemetry の active chunk 状態もサーバー側 barge-in 判定に使うようにした

### 次のセッションでやること
- `make server-debug` で実音声確認し、`reason=playback_active_chunk` と `reason=playback_ended_grace` が自己会話候補を抑止しているか確認する

## 2026-05-23 セッション28

### やること（開始時に書く）
- 実ログ確認後、回り込み猶予を1.2秒へ戻し、ログ timestamp をミリ秒単位にする

### やったこと
- `logs/server-debug.log` を確認し、改善の主因が `playback_active_chunk` であることを確認した
- `TomoroSession` の `playback_echo_grace_ms` デフォルトを 2000ms から 1200ms に戻した
- アプリログの timestamp を `YYYY-MM-DD HH:MM:SS.mmm` 形式に変更した
- 1.2秒猶予の unit test に更新した

### 詰まったこと・解決したこと
- 秒精度ログでは `playback_ended` から `playback_ended_grace` までの正確な差分が見えなかった
  → Python logging formatter に `%(msecs)03d` を追加し、次回ログからミリ秒単位で読めるようにした

### 次のセッションでやること
- 空にした `logs/server-debug.log` で実音声確認し、`playback_ended` から `playback_ended_grace` までの ms 差分を見る

## 2026-05-23 セッション29

### やること（開始時に書く）
- STT が何を認識して会話継続しているかをログで見えるようにする

### やったこと
- 発話終了後の `transcriber.transcribe()` 直後に transcript 全件を `server.session` logger に出すようにした
- ログには `text` / `speaker` / `audio_level_db` / `attention_mode` / `state` を含める

### 詰まったこと・解決したこと
- 小さな物音や誤 STT が `attention_engaged_followup` / `attention_cooldown_followup` に流れているかをログだけでは判別しづらかった
  → participation 判定前の transcript を必ず出すことで、次回ログから原因を追えるようにした

### 次のセッションでやること
- `make server-debug` のログで transcript と participation / barge-in の対応を見て、短すぎる transcript や空に近い STT を observer 扱いにする条件を決める

## 2026-05-23 セッション30

### やること（開始時に書く）
- Phase 6.6.1.2 を `PLAN.md` に追加し、小さな物音や Whisper hallucination による follow-up 誤起動を抑制する

### やったこと
- `PLAN.md` に Phase 6.6.1.2: Follow-up 誤起動の抑制を追加した
- `ParticipationContext` に `audio_level_db` を渡すようにした
- `WakeWordJudge` で engaged/cooldown follow-up 前に低信頼 transcript を `observer` に倒すようにした
- 空文字、1〜2文字、低音量短文、Whisper 定型 hallucination を低信頼 follow-up として扱うようにした
- 低信頼 observer 発話では attention idle を延長しないようにした
- attention decay は `idle` 状態の無音 chunk だけで積算するようにした
- participation / attention の unit test を追加した

### 詰まったこと・解決したこと
- 発話終了待ちの silence chunk まで attention idle に積算すると、発話中に cooldown/ambient へ進みすぎる
  → `process_audio_chunk` の順序を調整し、state 遷移後に `state == idle` のときだけ無音積算するようにした

### 次のセッションでやること
- `make server-debug` で実音声確認し、`reason=low_confidence_followup` が小さな物音や定型 hallucination を observer に落としているか確認する

## 2026-05-23 セッション31

### やること（開始時に書く）
- STT を MLX Whisper 推論へ切り替え可能にし、ストリーミング partial transcript と速度ベンチを実装する

### やったこと
- `BackendSpec` に `streaming` / `stream_interval_ms` / `stream_min_audio_ms` を追加した
- `MlxWhisperSTT` を追加し、`mlx_whisper` で一時 WAV を transcribe する経路を実装した
- `TomoroSession` が VAD listening 中に streaming STT partial を出し、発話終了時に stream buffer を reset するようにした
- `config/central_realtime.toml` の STT backend を `local_whisper_mlx_small` に切り替えた
- `make bench-stt` と `tests/perf/test_stt_latency.py` を追加し、faster-whisper small と MLX Whisper small を同じ音声で比較できるようにした
- `mlx-whisper` を optional dependency に追加した

### 詰まったこと・解決したこと
- `mlx-community/whisper-small` は存在しない/取得できないモデル ID だった
  → `mlx-community/whisper-small-mlx` に修正してベンチが通るようにした
- 初回 MLX はモデル取得と cache 作成が乗るため `warm_ms=13404.8` と重いが、warm 後の measured は `102.1ms` だった

### 次のセッションでやること
- Chrome 実音声で `transcript_partial` の出方と WebSocket 処理への詰まりを確認する
- partial STT を同期 await ではなく background task 化する必要があるか、実ログで判断する

## 2026-05-23 セッション32

### やること（開始時に書く）
- サーバー起動時の一般的な warm-up 仕組みを作り、MLX Whisper STT の初回コストを接続前に払う

### やったこと
- FastAPI lifespan startup で `_warm_up_app()` を実行する初期化フックを追加した
- STT transcriber に `warm_up()` を持たせ、`FasterWhisperSTT` / `NullTranscriber` は no-op、`MlxWhisperSTT` は短い無音 `SpeechSegment` を1回 transcribe するようにした
- startup warm-up の開始/完了と `elapsed_ms` を `server.edge.main` logger に出すようにした
- startup warm-up と STT backend warm-up の unit test を追加した
- キャッシュ済み MLX Whisper small で `_warm_up_app()` を直接実行し、`elapsed_ms=2015.5` を確認した

### 詰まったこと・解決したこと
- FastAPI の `on_event("startup")` は非推奨警告が出るため使わず、lifespan handler に切り替えた

### 次のセッションでやること
- `make server-debug` 起動時に startup warm-up log が出てから WebSocket 接続されることを確認する
- Chrome 実音声で最初の STT に MLX 初回コストが乗らないことを確認する

## 2026-05-23 セッション33

### やること（開始時に書く）
- Phase 6.6.2: STT Hallucination Filter を実装し、MLX Whisper の反復 hallucination を partial 表示・参加判定・ambient_logs の手前で抑制する

### やったこと
- `TranscriptFilterDecision` DTO と `server/edge/pipeline/stt_filter.py` を追加した
- final transcript の `drop` は participation 判定・ambient_logs へ進めず、partial の `suppress_partial` は UI へ送らないようにした
- `TomoroSession` に filter を接続し、判定結果を `server.session` log に出すようにした
- 実ログで見えた `また` 反復、`Have` 反復、`今日は日曜日の日曜日です`、低音量の「お疲れ様でした」、`ご視聴ありがとうございました` 系を unit test で固定した
- `PLAN.md` に append-only で Phase 6.6.2 実装結果を追記した

### 詰まったこと・解決したこと
- 最初の反復検出では単発の「今日は日曜日の日曜日です」が accept になった
  → 実ログ例に合わせて `日曜日の日曜日` を明示的な repetition hint として扱った
- drop 済み transcript を ambient_logs に保存するか迷う余地があった
  → 今回は記憶土台を汚さないことを優先し、filter `drop` は完全に保存しない方針にした

### 次のセッションでやること
- `make server-debug` の実音声ログで `TomoroSession transcript filter ... action=drop` が出ることを確認する
- 過剰 drop があれば、対象語を減らすより音量・長さ条件を足して調整する

## 2026-05-23 セッション34

### やること（開始時に書く）
- Phase 6.6.3 のうち、kokoro/irodori TTS 差し替え前に効果が大きい最小限の TomoroSession 状態保護だけを実装する

### やったこと
- `TomoroSession` に `asyncio.Lock` を追加し、audio turn と playback telemetry の状態更新だけを短く保護した
- `audio_start` / `audio_end` / `audio_control stop` は lock 内で送信イベントを予約し、実際の `send_event` は lock 外で行うようにした
- `_audio_sequence` 採番と `_tomoko_speaking_until` 更新を lock 内に入れた
- `handle_playback_telemetry` を async 化し、`server/edge/main.py` から await するようにした
- `tests/unit/test_session_concurrency.py` を追加し、並行 start/stop の二重送信防止と telemetry async 契約を固定した
- `PLAN.md` に Phase 6.6.3 の最小実装結果を append-only で追記した

### 詰まったこと・解決したこと
- 現状の `/ws` 受信ループ自体は直列なので、大規模な actor/queue 化は今やる必要がない
  → kokoro / irodori TTS 前に効く audio turn / playback state の保護だけに絞った
- `send_event` を lock 内で await すると逆に詰まりやすい
  → lock 内ではイベント内容の予約と状態確定だけを行い、I/O は lock 外へ出した

### 次のセッションでやること
- kokoro / irodori TTS 差し替え時に、必要なら `/ws` 受信ループと reply/TTS 生成の本格並行化を検討する

## 2026-05-23 セッション35

### やること（開始時に書く）
- 別 LLM からの `TomoroSession` 肥大化リスクに関するフィードバックを確認し、妥当なら `PLAN.md` に Phase 6.6.4 として対応方針を追記する

### やったこと
- `server/session.py` の責務集中状況を確認し、フィードバックは妥当と判断した
- `PLAN.md` に Phase 6.6.4: TomoroSession responsibility split を append-only で追記した

### 詰まったこと・解決したこと
- 実装変更ではなく計画追記のため、テスト実行は不要と判断した

### 次のセッションでやること
- Phase 6.6.4 に入る場合は、先に characterization test を追加してから `AudioTurnController` / `ReplyAudioPipeline` の境界を切る

## 2026-05-23 セッション36

### やること（開始時に書く）
- Phase 6.6.4: `TomoroSession` の責務分割を実装する
- 先に既存挙動を固定する characterization test を追加し、その後 `AudioTurnController` / reply-TTS helper の境界を切る

### やったこと
- `AudioTurnController` を追加し、audio turn / playback telemetry / audio sequence / speaker echo grace を `TomoroSession` から切り出した
- `ReplyAudioPipeline` を追加し、`ThinkingEvent` から emotion / reply text / TTS flush command への変換を切り出した
- `TomoroSession` は既存 public entrypoint と WebSocket protocol を変えず、会話状態機械と送信順序のオーケストレーションに寄せた
- `ARCHITECTURE.md` に状態所有ルールを追記した
- `tests/unit/test_audio_turn_controller.py` と `tests/unit/test_reply_audio_pipeline.py` を追加した
- `PLAN.md` と `MEMORY.md` に Phase 6.6.4 の実装結果と確定判断を追記した

### 詰まったこと・解決したこと
- 既存テストが `TomoroSession` の private helper を直接触っていた
  → 挙動互換の delegate helper / property を残し、実処理だけ controller に移した
- `ruff` で import 順の指摘が出た
  → `ruff check . --fix` で整列した

### 次のセッションでやること
- kokoro / irodori TTS 差し替え時に、必要なら `ReplyAudioPipeline` の command 境界を拡張する
- `/ws` 受信ループと reply/TTS 生成の本格並行化が必要になったら、今回切った `AudioTurnController` 境界を前提に進める

## 2026-05-23 セッション37

### やること（開始時に書く）
- `ReplyAudioPipeline` が emotion-to-image の asset mapping まで持っている境界の妥当性を確認し、必要なら修正する

### やったこと
- 指摘どおり、`ReplyAudioPipeline` が `/assets/images/...` を知るのは UI asset mapping への関心漏れと判断した
- `ReplyAudioPipeline` から画像 path 対応表と `image` field を削除した
- emotion event の `image` 付与は WebSocket event を組み立てる `TomoroSession` 側へ戻した
- `tests/unit/test_reply_audio_pipeline.py` を、pipeline が emotion 値と style だけを返す期待に更新した

### 詰まったこと・解決したこと
- 画像は TTS flush 変換ではなく表示 event の enrichment なので、`ReplyAudioPipeline` では扱わない方が境界として自然だった

### 次のセッションでやること
- emotion-to-image mapping がさらに肥大化する場合は、`TomoroSession` 直置きではなく `EmotionAssetMapper` のような表示 event 用 helper へ切り出す

## 2026-05-23 セッション38

### やること（開始時に書く）
- reply 周辺の境界を `session -> reply -> audio/emotion/image` の依存方向へ修正する
- `TomoroSession` から emotion-to-image mapping を外し、reply 配下の image helper へ移す

### やったこと
- `server/gateway/reply/` を追加し、reply 配下を `pipeline.py` / `audio.py` / `emotion.py` / `image.py` に分けた
- `TomoroSession` は `ReplyPipeline` だけを import する形にした
- `ReplyPipeline` が `ReplyAudioPlanner` / `ReplyEmotionState` / `EmotionImageMapper` を内部利用する構造にした
- 既存互換のため `server/gateway/reply_audio.py` は re-export の shim にした
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` に境界修正を追記した

### 詰まったこと・解決したこと
- 直前修正では image mapping が `TomoroSession` 側に戻っていた
  → 「session は reply だけを見る」という依存方向を優先し、image mapping は reply 配下の helper に移した

### 次のセッションでやること
- reply command が増える場合も、`TomoroSession` が audio/emotion/image の個別 helper を直接 import しないように保つ

## 2026-05-23 セッション39

### やること（開始時に書く）
- reply 周辺の境界を `session -> reply -> audio/display` に修正する
- display 側に emotion と画像 asset 解決をまとめ、将来の pose / animation / 追加表示媒体に備える

### やったこと
- `server/gateway/reply/display.py` を追加し、emotion 状態と image asset mapping を `ReplyDisplayPlanner` に集約した
- `ReplyPipeline` は `ReplyAudioPlanner` と `ReplyDisplayPlanner` の二系統だけを内部利用する形にした
- 旧境界の `reply/emotion.py` と `reply/image.py` を削除した
- display planner の単体テストを追加した

### 詰まったこと・解決したこと
- `audio/emotion/image` の三分割だと、将来 image 以外の表示要素が増えた時に display concern が散らばる
  → emotion は表示状態を駆動する入力でもあるため、reply 配下では `display` にまとめる判断に変更した

### 次のセッションでやること
- 表示要素が image 以外に増える場合も、`ReplyDisplayPlanner` で display command を拡張し、`TomoroSession` に個別表示ルールを戻さない


## 人間メモ

1. kokoroをmlxかつstreaming　する TTS backend を入れる
2. say の同期実行前提を外す
3. ReplyPipeline から sentence flush された単位を即 TTS streaming に流す
4. audio chunk を生成され次第 /ws で送る
5. barge-in 時に生成中 TTS / playback を止められるようにする

## 2026-05-23 セッション40

### やること（開始時に書く）
- kokoro-mlx streaming TTS backend を追加する
- say の同期実行前提を外し、sentence flush 単位で TTS streaming task に流す
- 生成された audio chunk を即 `/ws` に送り、barge-in 時に生成中 TTS と playback を停止できるようにする

### やったこと
- `KokoroMLXBackend` を追加し、`kokoro_mlx.KokoroTTS.generate_stream()` の numpy chunk を RIFF/WAVE `AudioChunkOut` として返すようにした
- `config/central_realtime.toml` の TTS backend を `kokoro_mlx` に切り替えた
- `pyproject.toml` / `uv.lock` に `kokoro-mlx` と `misaki[ja]` を追加し、import 可能なことを確認した
- `TomoroSession` の reply 生成を background task 化し、TTS worker が sentence flush を queue から順次 streaming する構造にした
- hard interrupt で reply task / TTS worker を cancel し、`audio_control stop` を送るようにした
- Kokoro backend と streaming/cancel の unit test を追加した
- `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` / `_docs/latency.md` に今回の判断と検証結果を追記した

### 詰まったこと・解決したこと
- `process_audio_chunk()` が reply/TTS を await すると `/ws` のマイク入力が止まり、barge-in が真に並行処理できない
  → 参加判断後は reply generation task を起動して戻る構造へ変更した
- Kokoro の streaming chunk は raw numpy audio なので、そのまま送ると既存クライアントの `decodeAudioData` で読めない
  → chunk ごとに RIFF/WAVE に包み、クライアント側ロジックを増やさない形にした
- 既存テストは reply 完了を `process_audio_chunk()` の戻りと同時だと仮定していた
  → 必要なテストだけ `_wait_for_reply_task()` で明示的に待つよう更新した

### 次のセッションでやること
- Kokoro 実モデルの初回 download / warm-up と real latency を測り、`_docs/latency.md` に記録する
- Chrome 実音声で Kokoro の日本語品質、chunk 境界の途切れ、hard interrupt 停止を確認する

## 2026-05-23 セッション41

### やること（開始時に書く）
- kokoro TTS に切り替え後、音声が再生されない原因をサーバーログから確認する

### やったこと
- `logs/server-debug.log` を確認し、wake word 後の返答生成で kokoro の日本語G2Pが `fugashi` / `unidic` の辞書未導入により落ちていることを特定した
- `KokoroMLXBackend` が日本語音声では `pyopenjtalk` 版の `misaki.ja.JAG2P` を使うようにした
- `jf_` / `jm_` voice では kokoro に `language="ja"` を明示するようにした
- kokoro backend の回帰テストを追加し、実 backend でも短い日本語から RIFF/WAVE chunk が出ることを確認した

### 詰まったこと・解決したこと
- `misaki[ja]` は `unidic` パッケージを入れるが、辞書本体の `mecabrc` は存在しなかった
  → Tomoko 側では `unidic` 辞書に依存せず、既に通る `pyopenjtalk` 経路へ固定して解決した

### 次のセッションでやること
- `make server-debug` で Chrome 実音声確認し、kokoro 音声の再生、chunk 境界、初回 warm-up レイテンシーを確認する

## 2026-05-24 セッション1

### やること（開始時に書く）
- kokoro TTS で音声が出たり出なかったりする原因をサーバーログから確認する

### やったこと
- `logs/server-debug.log` を確認し、音声が出たターンでは `playback_started` / `playback_ended` telemetry が返っていることを確認した
- 音声が出なくなったターンでは `Voice file not found: .../voices/jf_beta.safetensors` で reply/TTS 生成が落ちていることを特定した
- 手元の Kokoro モデルに存在する日本語 voice が `jf_alpha`, `jf_gongitsune`, `jf_nezumi`, `jf_tebukuro`, `jm_kumo` で、`jf_beta` が存在しないことを確認した
- `KokoroMLXBackend` で未存在 voice を選んだ場合、設定上の既定 voice へフォールバックするようにした
- 実 kokoro backend で `sad` style の日本語から RIFF/WAVE chunk が出ることを確認した
- 英語・中国語混入についてログを確認し、`因为` / `washed` / `TTTT...` などはSTT側の hallucination と判断した
- 低音量のASCII-only transcriptを `TranscriptFilter` で drop するようにした
- `prompts/base_persona.md` に本文は日本語だけで返す指示を追加した

### 詰まったこと・解決したこと
- emotion style の `sad` / `thinking` / `gentle` が `jf_beta` に固定されていた
  → model に存在しない voice だったため、その感情の返答だけ無音になっていた
  → voice の存在確認と fallback を入れて解決した
- `washed` のような英語単語だけの低信頼STTが accept されていた
  → 低音量ASCII-only発話は `low_audio_ascii_text` として落とすようにした

### 次のセッションでやること
- `make server-debug` を再起動して Chrome 実音声で `sad` / `thinking` 系 emotion でも音声が出ることを確認する
- 英語・中国語混入が続く場合は、`reply_text` delta もサーバーログへ出して STT由来かLLM出力由来かを分離する

### 追加対応
- `TomoroSession` が `reply_text` delta を WebSocket 送信する直前に `server.session` log へ出すようにした
- `ruff check server/session.py` と `pytest -m unit` が通過した

## 2026-05-24 セッション2

### やること（開始時に書く）
- 応答に英語が残る問題について、まずサーバーログから STT 由来か LLM 返答由来かを切り分ける

### やったこと
- `logs/server-debug.log` を確認し、英語混入が STT hallucination と LLM 返答本文由来の2種類あることを切り分けた
- `gallery gallery...` / `llllllll...` は `TranscriptFilter` で drop/suppress されていた
- `hear you` / `TAXONOMY` / `Goes from remembering to evaluating.` は `reply_text delta` に出ていたため、LLM 返答本文由来と判断した
- `ReplyPipeline` に `ReplyTextSanitizer` を追加し、表示用 `reply_text` と TTS 用 `tts_text` の両方へ流す前に ASCII 英字などの日本語外文字を除去するようにした
- 分割 token の `TAX` `ON` `OM` `Y` も表示/TTSへ出ない unit test を追加した

### 詰まったこと・解決したこと
- プロンプトでは「本文は日本語だけ」と指示済みだったが、LLM が `reply_text delta` に英語を出すケースが残っていた
  → プロンプトだけに依存せず、サーバー側 reply 境界で出力契約を守るようにした

### 次のセッションでやること
- Chrome 実音声で `reply_text` に英語が残らないことを確認する
- まだ不自然な空白や記号だけが残る場合は、`ReplyTextSanitizer` の許可文字を追加で絞る

### 追加対応
- 最初に `Irodori-TTS-Server` の OpenAI 互換 HTTP backend を `irodori_mlx` として実装したが、これは mlx-audio 版ではないため撤回した
- GitHub 最新の `mlx-audio` を `uv add "mlx-audio @ git+https://github.com/Blaizzy/mlx-audio.git"` で追加した
- `IrodoriMLXBackend` を `mlx_audio.tts.utils.load_model()` で `mlx-community/Irodori-TTS-500M-v3-8bit` を直接ロードする実装に置き換えた
- `config/central_realtime.toml` の `tts_backend` を `irodori_mlx` に切り替えた
- Irodori v3 は mlx-audio 側で真の `stream=True` が未実装なので、Tomoko の sentence flush / TTS queue により文単位で逐次生成する
- 実モデル smoke で `こんにちは。` から RIFF/WAVE chunk が返ることを確認した
- `_docs/latency.md` にキャッシュ済み短文合成 2959.1ms を追記した
- `ruff check .` と `pytest -m unit` が通過した

## 2026-05-24 セッション3

### やること（開始時に書く）
- Irodori backend が本当に streaming / MLX / Irodori で動いているか確認する
- 起動時 TTS warm-up の有無を確認し、未実装なら追加する
- mlx-audio の Irodori v2 で streaming 可能か確認し、可能なら v2 へ切り替える

### やったこと
- `irodori_mlx` は `mlx_audio.tts.utils.load_model()` で `mlx-community/Irodori-TTS-500M-v3-8bit` を直接ロードしており、MLX + Irodori v3 で動いていることを確認した
- 一方で `mlx-audio` の Irodori 実装は `Model.generate(..., stream=True)` が v2/v3 共通で `NotImplementedError` になるため、真の streaming ではないことを確認した
- v2 にしても streaming は有効にならないため、v3 recommended / sway sampling / automatic duration の利点を優先して v3 のままにした
- `TTSBackend.warm_up()` を追加し、`IrodoriMLXBackend` は短文 `あ。` を一度生成してモデルロードと初回生成コストを起動時に払うようにした
- `_create_default_tts_backend()` が `app.state._default_tts_backend` に backend をキャッシュし、warm-up 済み backend を `/ws` session で再利用するようにした
- `_warm_up_app()` が STT に続いて TTS backend も warm-up するようにした
- cached `_warm_up_app()` 実測で STT 1262.1ms、Irodori MLX TTS 2831.9ms を確認し、`_docs/latency.md` に追記した

### 詰まったこと・解決したこと
- 「streaming」と呼べるのは現時点では Tomoko の文単位 queue streaming であり、Irodori モデル内部の chunk streaming ではなかった
  → v2 切り替えでは解決しないため、v3 のまま起動時 warm-up で初回遅延を前払いする方針にした

### 次のセッションでやること
- Chrome 実音声で Irodori MLX の音質と、起動後初回返答の体感レイテンシーを確認する
- Irodori の 3秒級レイテンシーが厳しい場合は、Kokoro を通常会話用、Irodori を高品質モード用にする切り替え方針を検討する

## 2026-05-24 セッション4

### やること（開始時に書く）
- MLX + Irodori v2/v3 + streaming 必須の制約で、レイテンシーを下げられる別 Irodori TTS backend を作れるか確認する
- 真の model-internal streaming が未実装なら、Irodori MLX のまま先頭音声を早く返す実用的な streaming backend を実装する

### やったこと
- mlx-audio の Irodori 実装を確認し、v2/v3 共通で `stream=True` が未実装であることを再確認した
- `seconds` 明示と `num_steps=6` により、warm-up 済み短文生成が 100ms 前後まで下がることを実測した
- `IrodoriMLXStreamBackend` を追加し、text を短い日本語発話単位に分割して各単位を Irodori v3 + MLX で逐次生成するようにした
- `config/central_realtime.toml` の default TTS backend を `irodori_mlx_stream` に切り替えた
- 実モデル smoke で warm-up 後 `うん、わかった。少し待ってね。` が 2 chunk、first 107.0ms、total 206.9ms で返ることを確認した
- `_docs/latency.md` に実測値を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- Irodori の真の model-internal streaming は現時点の mlx-audio では使えない
  → backend 境界で短い Irodori 生成を複数回走らせ、生成できた単位から `/ws` に流す方式にした
- 初回プロセスではモデルロード込みで first chunk が約 2.9 秒かかる
  → 既存の起動時 TTS warm-up と backend cache に乗せることで、会話時の first chunk を約 107ms にした

### 次のセッションでやること
- Chrome 実音声で `irodori_mlx_stream` の文節ごとの自然さと音切れを確認する
- 必要なら `max_chars` と `seconds` 推定式を実音声の聞こえ方に合わせて微調整する

## 2026-05-24 セッション5

### やること（開始時に書く）
- Qwen3-TTS の小さいモデルと大きいモデルを `mlx-audio` 経由の Tomoko TTS backend として追加する
- `irodori_mlx` / `irodori_mlx_stream` / Qwen3 小モデル / Qwen3 大モデルを同じ日本語文でベンチし、first chunk と総時間を `_docs/latency.md` に記録する

### やったこと
- `Qwen3MLXTTSBackend` を追加した
  - `mlx-audio` の `Model.generate(..., stream=True)` を使う
  - 同期 generator を worker thread で消費し、chunk が出るたび `AudioChunkOut` に変換する
  - `lang_code="Japanese"` 固定、emotion style は `instruct` / `speed` へ変換する
- `config/central_realtime.toml` に `qwen3_tts_mlx_small` と `qwen3_tts_mlx_large` を追加した
- `_tools/bench_tts_backends.py` を追加し、4 backend を同じ文で測れるようにした
- 初回実行で Qwen3 small / large を Hugging Face から取得した
- キャッシュ済み再実行で以下を確認した
  - `irodori_mlx`: first 659.2ms / total 659.2ms / 1 chunk
  - `irodori_mlx_stream`: first 96.6ms / total 192.7ms / 2 chunks
  - `qwen3_tts_mlx_small`: first 142.6ms / total 545.3ms / 8 chunks
  - `qwen3_tts_mlx_large`: first 216.7ms / total 820.5ms / 8 chunks
- 聞き比べ用 WAV を `artifacts/tts-bench-cached/` に保存した
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` に実測と判断を追記した

### 詰まったこと・解決したこと
- Qwen3 の初回ベンチはモデルダウンロード時間が warm-up に混ざった
  → 同じスクリプトを再実行し、キャッシュ済みの比較値を採用した
- 自然さは自動判定できない
  → WAV を保存し、人間の試聴判断に回す

### 次のセッションでやること
- `artifacts/tts-bench-cached/*.wav` を聞いて、Tomoko の声として許容できる候補を選ぶ
- Qwen3 が自然なら default backend の切り替え、Irodori stream が自然なら現状維持を判断する

## 2026-05-24 セッション6

### やること（開始時に書く）
- LLM の返答に英語が混じる前提で、TTS 用に軽量 LLM で日本語化する実験プログラムを追加する
- 日本語化の出力品質とレイテンシーを確認できるベンチプログラムを作り、実測結果を残す

### やったこと
- `_tools/normalize_tts_text_mlx.py` を追加した
  - 単一テキストを TTS 用日本語へ正規化する
  - 既定は `mlx-vlm` + `mlx-community/gemma-4-e2b-it-4bit`
  - 出力 JSON に load / first token / first text / total latency を残す
- `_tools/bench_tts_text_normalizer_mlx.py` を追加した
  - 日本語文、英語混じり文、時刻、API用語など複数サンプルを同一モデルで測る
  - `logs/tts-text-normalizer/gemma4-e2b/summary.md` と `results.jsonl` を出力する
- `mlx-vlm` を依存追加した
- `mlx_lm` の Gemma 4 E2B text-only 変換を試したが、手元の `mlx_lm` では重み不一致でロード不可だった
- `mlx-community/gemma-4-e2b-it-4bit` を `mlx-vlm` 経由でロードし、正規化出力とレイテンシーを確認した
- warm 後の複数サンプルでは first token 87.4〜184.1ms、first text 162.7〜243.6ms、total 166.5〜247.3ms だった

### 詰まったこと・解決したこと
- `jorch/gemma-4-e2b-it-lm-4bit` と `mlx-community/Gemma4-E2B-IT-Text-int4` は `mlx_lm.load()` で重み不一致になった
  → `mlx-vlm` を導入し、`mlx-community/gemma-4-e2b-it-4bit` を使う形に切り替えた
- `mlx-vlm` の stream は token ごとに空文字を返し、最後にまとまった日本語 text が出ることがある
  → latency は `first_token_ms` と `first_text_ms` を分けて記録するようにした

### 次のセッションでやること
- TTS pipeline に入れる場合は、`ReplyPipeline` の TTS flush 直前に `ReplySpeechNormalizer` として統合する
- その際は毎文 LLM 正規化するのではなく、ASCII 英字や時刻が混じる文だけ正規化対象にする

## 2026-05-24 セッション7

### やること（開始時に書く）
- Irodori stream を x1 速度に戻す
- TTS 直前で日本語以外・時刻・英字混入を検出し、混入時だけ Gemma 4 E2B で TTS 用日本語化する
- 起動時に Gemma 正規化器を warm-up し、混入時の初回レイテンシーを前払いする

### やったこと
- `ReplySpeechNormalizer` を追加した
  - 純日本語はそのまま返す
  - ASCII 英字、時刻、非日本語文字体系を検出した時だけ `mlx-vlm` + `mlx-community/gemma-4-e2b-it-4bit` で TTS 用日本語へ変換する
- `TomoroSession._flush_tts_text()` に正規化を入れ、TTS backend へ渡す直前の音声用テキストだけ変えるようにした
- `ReplyPipeline` は表示用には sanitizing 済み delta を使い、TTS buffer には raw delta を保持するようにした
  - これで英字を削ってから TTS へ渡すのではなく、Gemma が英語混じり文を日本語化できる
- `_warm_up_app()` で Gemma 正規化器も warm-up し、モデルロードと初回生成を起動時に前払いするようにした
- `irodori_mlx_stream` の秒数推定を x1 に戻した
- 実Gemma smoke で warm 後 `トモコ、today の meeting は 3pm からだよ。` が 163.2ms で `トモコ、今日の会議は午後三時からですよ。` になった
- `_docs/latency.md` / `PLAN.md` / `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 既存の `ReplyTextSanitizer` が TTS 前に英字を削っていた
  → 表示用は sanitizing を維持し、TTS 用 buffer だけ raw delta を保持して正規化器に渡す形にした

### 次のセッションでやること
- Chrome 実音声で、英語混じり LLM 返答が表示では日本語契約を守りつつ、TTS では自然な日本語音声になるか確認する
- 必要なら `ReplySpeechNormalizer` の検出条件を、実ログに出る混入パターンへ合わせて追加する

## 2026-05-24 セッション8

### やること（開始時に書く）
- irodori_mlx_stream の発話秒数を x1.2 にして品質寄りに調整する
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream のサンプルWAVを再出力する
- レイテンシーと出力先を記録する

### やったこと
- `irodori_mlx_stream` の `seconds` 推定に品質用スケール x1.2 を掛けるようにした
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream x1.2 のサンプルWAVを出力した
  - `logs/tts-normalized-irodori-asuka-x1_2/gemma-normalized-irodori-stream-asuka-x1_2.wav`
  - 正規化: 191.0ms
  - TTS first chunk: 432.4ms
  - TTS total: 1198.2ms
  - audio duration: 4.68秒
  - chunks: 3
- `_docs/latency.md` と `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- x1 はレイテンシーは良いが、asuka 参照音声では長め文の品質が厳しい
  → x1.2 で発話秒数に余裕を持たせる判断にした

### 次のセッションでやること
- x1.2 のWAVを試聴し、まだ厳しければ `num_steps` を6から8へ上げるか、stream unitをさらに短くする

## 2026-05-24 セッション9

### やること（開始時に書く）
- irodori_mlx_stream の発話秒数を x1.5 に上げる
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream のサンプルWAVを再出力する
- レイテンシーと出力先を記録する

### やったこと
- `irodori_mlx_stream` の品質用スケールを x1.2 から x1.5 へ上げた
- clamp 上限も x1.5 に合わせて 3.6秒へ戻した
- Gemma 日本語正規化 + asuka 参照音声 + Irodori stream x1.5 のサンプルWAVを出力した
  - `logs/tts-normalized-irodori-asuka-x1_5/gemma-normalized-irodori-stream-asuka-x1_5.wav`
  - 正規化: 191.7ms
  - TTS first chunk: 463.0ms
  - TTS total: 1264.6ms
  - audio duration: 5.88秒
  - chunks: 3
- `_docs/latency.md` と `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- x1.2 でも品質が厳しかった
  → 発話秒数の余裕をさらに増やし、まず x1.5 を default として試聴判断する

### 次のセッションでやること
- x1.5 のWAVを試聴し、まだ厳しければ duration ではなく `num_steps` や unit 分割幅を調整する

## 2026-05-24 セッション10

### やること（開始時に書く）
- Kokoro MLX stream に Gemma 日本語正規化を前段適用したサンプルWAVを出力する
- 英語混じり原文のKokoro出力と、正規化後Kokoro出力を比較できるように保存する
- レイテンシーと出力先を記録する

### やったこと
- 同じ英語混じり文で Kokoro MLX の raw 出力と Gemma 正規化後出力を作成した
  - raw: `logs/tts-normalized-kokoro-mlx/kokoro-mlx-raw-mixed.wav`
  - normalized: `logs/tts-normalized-kokoro-mlx/kokoro-mlx-gemma-normalized.wav`
  - summary: `logs/tts-normalized-kokoro-mlx/summary.json`
- Gemma 正規化結果:
  - `トモコ今日の会議は午後三時からだからスケジュールを確認して終わったらすぐに教えて`
- 実測:
  - Gemma 正規化: 188.6ms
  - raw Kokoro: first 293.8ms / total 293.9ms / audio 13.25秒 / 1 chunk
  - normalized Kokoro: first 142.8ms / total 142.9ms / audio 7.175秒 / 1 chunk
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- Kokoro の `generate_stream()` はこの長さの文でも 1 chunk だけ返した
  → 少なくとも今回の条件では、Kokoro は実質一括生成に近い挙動だった

### 次のセッションでやること
- WAVを試聴し、Kokoro + Gemma正規化が Irodori x1.5 より自然か判断する
- Kokoroを再候補にする場合は、長文を `ReplyAudioPlanner` 側でさらに短くflushするか確認する

## 2026-05-24 セッション11

### やること（開始時に書く）
- Kokoro + Gemma 正規化の文章崩れとレイテンシーを確認する
- 生成済みKokoro WAVをSTTで戻し、raw / normalized / per-flush の違いを見る
- 方針判断のための分析と提案をまとめる

### やったこと
- 生成済み `kokoro-mlx-raw-mixed.wav` と `kokoro-mlx-gemma-normalized.wav` を MLX Whisper で戻した
  - raw は英字をそのまま読もうとして崩れた
  - normalized は句読点なしの長い文になっており、内容も一部崩れて見えた
- 句読点付きの手動正規化文で Kokoro サンプルを追加生成した
  - `logs/tts-normalized-kokoro-mlx-analysis/kokoro-punctuated-full.wav`
  - `logs/tts-normalized-kokoro-mlx-analysis/kokoro-punctuated-split.wav`
- 実アプリに近い文ごと flush の Gemma 正規化 + Kokoro サンプルを生成した
  - `logs/tts-normalized-kokoro-mlx-analysis/kokoro-gemma-normalized-per-flush.wav`
  - first chunk 100.0ms / TTS total 178.7ms / 2 chunks
  - 正規化は1文目168.7ms、2文目103.4ms
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- Kokoro音質そのものより、Gemma正規化が句読点を落とすことでTTS用文章が壊れていた可能性が高い
  → Kokoroに戻すなら、句読点保持・補完を正規化プロンプトに入れる必要がある

### 次のセッションでやること
- 方針決定後、Kokoroをdefaultに戻すか、Kokoro専用のTTS正規化プロンプトとflushルールを実装する

## 2026-05-24 セッション12

### やること（開始時に書く）
- default TTS を Kokoro MLX に戻す
- Gemma TTS 正規化を句読点保持・文単位前提に修正する
- Kokoro + Gemma正規化の回帰テストとサンプル生成で確認する

### やったこと
- `config/central_realtime.toml` の default TTS を `kokoro_mlx` に戻した
- `ReplySpeechNormalizer` のプロンプトを Kokoro 向けに修正した
  - 入力の文数と順序を保つ
  - 複数文を一文へ結合しない
  - 句読点を保持・補完する
  - 文末に句点・疑問符・感嘆符を付ける
  - 一般英語はカタカナより自然な日本語訳を優先する
- モデルが文末句読点を落とした場合に、source の文末に合わせて補完する後処理を追加した
- `クイックに` のような不自然な読み上げ語を `すぐに` へ寄せる軽い後処理を追加した
- 採用版の Kokoro サンプルを出力した
  - `logs/tts-kokoro-gemma-adopted/kokoro-gemma-punctuation-preserved.wav`
  - 正規化後:
    - `トモコ、今日の会議は午後三時からだから、スケジュールを確認して。`
    - `終わったらすぐに教えて。`
  - 正規化: 211.1ms / 158.4ms
  - Kokoro first chunk: 112.8ms
  - Kokoro total: 166.6ms
  - chunks: 2
- `_docs/latency.md` と `MEMORY.md` に採用結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- プロンプトを強めても `quick` が `クイックに` になるケースが残った
  → TTS用途では不自然なので、限定的な後処理で `すぐに` へ寄せた

### 次のセッションでやること
- Chrome 実音声で Kokoro + Gemma句読点保持正規化の体感品質と初回音声タイミングを確認する

## 2026-05-24 セッション13

### やること（開始時に書く）
- Kokoro向けに読点/文節flushを実装しつつ、短すぎる `トモコ、` 単独flushを避ける
- 文節flush版のKokoro + Gemma正規化サンプルを再出力する
- レイテンシーと品質確認用WAVを記録する

### やったこと
- `ReplyAudioPlanner` に日本語読点の soft flush を追加した
- 10文字未満の短すぎる断片は soft flush しないようにした
  - `トモコ、` 単独TTSを避ける
  - ASCII comma は soft flush 対象から外した
- 回帰テストを追加した
  - `トモコ、` ではTTSしない
  - `トモコ、today の meeting は 3pm からだから、` まで来たらTTSする
  - 既存の英語混じりASCII commaケースは勝手にflushしない
- 文節flush版の Kokoro + Gemma サンプルを再出力した
  - `logs/tts-kokoro-gemma-bunsetsu-merged/kokoro-gemma-bunsetsu-merged.wav`
  - raw units:
    - `トモコ、today の meeting は 3pm からだから、`
    - `schedule を確認して。`
    - `終わったら quick に教えて。`
  - normalized:
    - `トモコ、今日の会議は午後三時からだから。`
    - `スケジュールを確認してください。`
    - `終わったらすぐに教えて。`
  - first chunk 75.6ms / TTS total 191.6ms / 3 chunks
- `_docs/latency.md` と `MEMORY.md` に結果を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 最初の実装は先頭の短い読点しか見ず、後続の読点まで待てなかった
  → 全文字を走査して、flush可能な読点だけ候補にするよう修正した
- ASCII comma でもflushして既存の英語混じりテストを壊した
  → soft flush は日本語読点だけに限定した

### 次のセッションでやること
- 文節flush版を試聴し、文体が丁寧語に寄りすぎる場合はGemma正規化プロンプトに「文体を変えない」をさらに強く入れる

## 2026-05-24 セッション14

### やること（開始時に書く）
- 会話LLMに「TTSでそのまま読める日本語」を指示した場合の追従率を測るベンチを追加する
- 現行プロンプトとTTS向け追加プロンプトで、英字混入率・Gemma正規化必要率・初回本文レイテンシーを比較する

### やったこと
- `_tools/bench_tts_ready_prompt.py` を追加した
  - 現行プロンプト、TTS読み上げ用追加プロンプト、few-shot付き追加プロンプトを同じ入力で比較する
  - `EMOTION:` ヘッダを除いた本文を評価する
  - Gemma正規化が必要な英字・時刻・非日本語混入と文末句読点を判定する
- 判定ロジックのユニットテストを追加した
- `qwen2.5:7b` で8入力のベンチを実行した
  - baseline: TTS ready 2/8、Gemma必要 6/8
  - TTS-ready prompt: TTS ready 0/8、Gemma必要 8/8
  - few-shot prompt: TTS ready 2/8、Gemma必要 6/8
  - 結果: `logs/tts-ready-prompt-bench-examples/summary.md`
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- 最初の判定では中国語の簡体字がCJK漢字として通ってしまった
  → ベンチ判定側に簡体字の軽い検出を追加した
- TTS-ready promptを強めても、`Zoom`、`GitHub Actions`、`LLM`、`TTS` などの入力コピーが残った
  → プロンプトだけでGemma層を外すのは危険と判断した

### 次のセッションでやること
- 上流LLMプロンプトへTTS向け日本語ルールを入れる場合も、Gemma正規化fallbackは維持する

## 2026-05-24 セッション15

### やること（開始時に書く）
- Gemma 4 E2B MLX をメイン会話推論候補として、TTS-ready追従率とレイテンシーを測る
- qwen2.5:7b の結果と同じベンチ条件で比較する

### やったこと
- `_tools/bench_tts_ready_prompt.py` に `--backend gemma_mlx` を追加した
  - `mlx-vlm` 経由で `mlx-community/gemma-4-e2b-it-4bit` を会話プロンプトに使う
  - ベンチ開始前に warm-up を走らせ、起動時ホット化後に近い値を測る
- Gemma 4 E2B MLX で8入力のTTS-readyベンチを実行した
  - baseline: TTS ready 8/8、Gemma必要 0/8、平均 first body 204.7ms
  - TTS-ready prompt: TTS ready 7/8、Gemma必要 1/8、平均 first body 312.1ms
  - few-shot prompt: TTS ready 8/8、Gemma必要 0/8、平均 first body 385.2ms
  - 結果: `logs/tts-ready-prompt-bench-gemma4-e2b-warm/summary.md`
- `_docs/latency.md` と `MEMORY.md` に結果を追記した

### 詰まったこと・解決したこと
- 初回計測では最初の1件にモデルロードが乗り、baseline平均が歪んだ
  → Gemma backendにwarm-upを追加して測り直した
- TTS-ready追加プロンプトは追従率を上げる一方で出力が長くなり、first bodyが悪化した
  → Gemmaを会話LLMにするなら、まず短い現行persona寄りのプロンプトで試す判断

### 次のセッションでやること
- Gemmaを実アプリの会話backendにする場合は、`InferenceRouter` に MLX会話backendを追加して、実セッションでTTS正規化fallbackの発火率を見る

## 2026-05-24 セッション16

### やること（開始時に書く）
- メイン会話推論を Gemma 4 E2B MLX に切り替える
- Kokoro は sentence flush のみで流し、読点による文節flushとGemma TTS正規化を無効にする
- 起動時warm-upと実セッションで使うbackendを共有し、レイテンシーログを追加する

### やったこと
- `GemmaMLXBackend` を追加し、`InferenceRouter` の `gemma_mlx` backend として使えるようにした
- `config/central_realtime.toml` の `conversation_backend` を `local_gemma4_e2b_mlx` に切り替えた
  - fallback は既存の `local_qwen7b`
  - `tts_backend` は `kokoro_mlx`
  - `speech_normalizer_enabled = false`
- FastAPI startup warm-up で conversation backend も温めるようにした
- `_create_default_router()` を `app.state._default_router` にキャッシュし、warm-up済み Gemma backend をWebSocketセッションでも再利用するようにした
- `ReplyAudioPlanner` は読点soft flushをやめ、`。！？` の sentence flush だけに戻した
- `TomoroSession` にレイテンシーログを追加した
  - `speech_end`
  - `transcript` / STT elapsed
  - `reply_start`
  - `first_reply_text`
  - `tts_start`
  - `first_audio_chunk`
- `logs/gemma-kokoro-warmup-smoke.log` で実 warm-up を確認した
  - STT warm-up: 1356.5ms
  - Kokoro warm-up: 277.8ms
  - Gemma conversation warm-up: 3751.6ms
  - TTS text normalizer は disabled で skip
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 最初は Gemma生成を `asyncio.to_thread` に逃がしたが、`mlx-vlm` が worker thread 上で `There is no Stream(gpu, 2)` を出して落ちた
  → Gemma会話backendでは `mlx-vlm.stream_generate()` を同じスレッドで同期消費する形にした
- 起動時にGemmaを温めても、routerを毎回作るとWebSocketセッションで別インスタンスが作られてcold startに戻る
  → default routerをキャッシュして同じbackendインスタンスを再利用するようにした

### 次のセッションでやること
- Chrome 実セッションで `TomoroSession latency first_audio_chunk` の `speech_end_to_first_audio_ms` を確認する
- Gemma同期生成がマイク入力処理に体感影響を出す場合は、`mlx-vlm` の専用生成スレッド/stream初期化方式を追加調査する

## 2026-05-24 セッション17

### やること（開始時に書く）
- LM Studio の OpenAI互換 streaming API を会話backendとして追加する
- メイン会話推論を内蔵 `mlx-vlm` Gemma から LM Studio の `gemma-4-e2b-it-mlx` に切り替える
- 起動時 warm-up と unit test でストリーミングSSE処理を固定する

### やったこと
- `LMStudioBackend` を追加した
  - OpenAI互換 `/v1/chat/completions` の `stream:true` SSE を読み、`delta.content` をそのまま `chat_stream()` で流す
  - base URL が `/v1` あり/なしのどちらでも動くようにした
  - 起動時 `warm_up()` で短い日本語応答を一度流してホット化する
- `InferenceRouter` に `lm_studio` backend type を追加した
- `config/central_realtime.toml` を LM Studio 採用構成に切り替えた
  - `conversation_backend = "lmstudio_gemma4_e2b"`
  - `conversation_fallback = "local_gemma4_e2b_mlx"`
  - URL は `http://192.168.11.66:1234`
  - model は `gemma-4-e2b-it-mlx`
- unit test を追加/更新した
  - SSE parser
  - LM Studio endpoint URL 組み立て
  - backend streaming payload
  - config/router の default backend
- `logs/lmstudio-kokoro-warmup-smoke.log` で実 warm-up を確認した
  - STT warm-up: 1854.2ms
  - Kokoro warm-up: 279.8ms
  - LM Studio conversation warm-up: 243.0ms
  - 依存同期後の再実行でも LM Studio conversation warm-up は 257.1ms で通過
  - TTS text normalizer は disabled で skip
- `httpx` と `mlx-whisper` を default dependency に明示した
  - `httpx` は LM Studio backend が直接使うため
  - `mlx-whisper` は default config の `local_whisper_mlx_small` が使うため
- `_docs/latency.md` と `MEMORY.md` に採用判断と実測値を追記した
- `ruff check .` と `pytest -m unit` が通過した

### 詰まったこと・解決したこと
- 通常サンドボックスでは `uv` cache と LAN の LM Studio API に触れず warm-up 確認が失敗した
  → 権限昇格で実機 warm-up を実行して確認した
- LM Studio の URL は `/v1` なしで渡される想定だが、将来 `/v1` 付きに変えても二重にならないよう helper で吸収した

### 次のセッションでやること
- Chrome 実セッションで `TomoroSession latency first_audio_chunk` の `speech_end_to_first_audio_ms` を確認する
- LM Studio 側でロード済みモデルが変わった場合に、設定の `model` と実際の応答 `model` がズレないかログで見る

## 2026-05-24 セッション21

### やること（開始時に書く）
- M2 に会話セッション境界の新 Phase を追記する
- `attention_mode` と `conversation_sessions` の関係、短期文脈の読み出し順を PLAN.md に明文化する

### やったこと
- `PLAN.md` の M2 Phase 8 と M2 完了条件の間に Phase 8.5「会話セッション境界」を追記した
- `ambient -> engaged` または最初の `should_participate=True` で session を開始し、`cooldown -> ambient` / `withdrawn` で閉じる方針を明文化した
- 短期文脈は同一 session の completed turn を優先し、足りない場合だけ最近の completed turn で補う方針を追記した

### 詰まったこと・解決したこと
- 既存 Phase 7/8 の記憶実装は否定せず、会話境界がない点だけを補う Phase として追加した

### 次のセッションでやること
- Phase 8.5 を実装する場合は、DDL と unit test を先に書いてから `TomoroSession` / `ConversationLogWriter` を更新する

## 2026-05-24 セッション22

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、ARCHITECTURE.md / AGENTS.md / PLAN.md を整理する
- `conversation_sessions` に会話境界・要約・要約 embedding を集約する設計へ更新する
- 要約と embedding 生成をオンライン経路から外し、別プロセスで pending session を処理する Phase を追加する

### やったこと
- `AGENTS.md` に会話セッション境界とセッション要約索引の実装規約を追加した
- `ARCHITECTURE.md` に `conversation_sessions`、`session_summarizer`、session summary search の設計を追記した
- `PLAN.md` の Phase 8.5 を `conversation_sessions` 一本で summary / embedding を持つ設計へ修正した
- `PLAN.md` に Phase 8.6「セッション要約索引」を追加し、pending session を別プロセスで要約・embedding 化する流れを定義した
- `MEMORY.md` に、会話セッション要約は `conversation_sessions` に集約し、オンライン経路から外す判断を追記した

### 詰まったこと・解決したこと
- 要約 embedding を別テーブルに分ける案は、現時点では管理対象を増やす割に利点が薄い
  → 複数モデルや複数要約種別が必要になるまで `conversation_sessions.summary_embedding` に一本化する

### 次のセッションでやること
- Phase 8.5 実装時は DDL と unit test を先に書き、session 開始/終了と `conversation_logs.conversation_session_id` の保存を固定する
- Phase 8.6 実装時は online `TomoroSession` 経路で summarizer が呼ばれないことをテストで保証する

## 2026-05-24 セッション23

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、用語集ログと人格スナップショットの versioned JSONB 設計を追記する
- PostgreSQL `jsonb` とプログラム側モデルクラスで、分析しやすく型も扱いやすい構成にする方針を文書化する

### やったこと
- `ARCHITECTURE.md` に `persona_lexicon_versions` / `persona_state_versions` の versioned JSONB snapshot 設計を追記した
- `lexicon_json` / `state_json` / `diff_json` の役割と JSON 形状例を追加した
- PostgreSQL `jsonb` / jsonpath / GIN index を外部分析に使い、アプリケーションではモデルクラスに変換して扱う方針を明文化した
- `AGENTS.md` に JSONB snapshot の実装規約を追加した
- `PLAN.md` に Phase 8.7「用語集ログと人格スナップショット」を追加し、M5 Phase 17 を DB の versioned snapshot 前提に補正した
- `MEMORY.md` に確定した判断として追記した

### 詰まったこと・解決したこと
- 用語や人格状態を細かい正規化テーブルで持つ案もあるが、変動点トレースと外部分析を優先し、まずは JSONB snapshot + diff に寄せる
- 生 JSON をプログラム中で持ち回ると境界が曖昧になるため、DB 入出力時に schema version 付きモデルクラスへ変換する規約にした

### 次のセッションでやること
- Phase 8.7 実装時は JSONB DDL、モデルクラス、round-trip test、jsonb query test を先に書く

## 2026-05-24 セッション24

### やること（開始時に書く）
- markdown 編集禁止ルールの一時解除を受け、`ContextSnapshotBuilder` を設計に盛り込む
- LLM に渡す文脈取得を一箇所に集約し、depth と perf benchmark でレイテンシー管理できる Phase を追加する

### やったこと
- `AGENTS.md` に `ContextSnapshotBuilder` の実装規約を追加した
- `ARCHITECTURE.md` に `ContextSnapshotBuilder` の責務、DTO、depth、初段 fallback、perf 目標を追記した
- `PLAN.md` に Phase 8.8「ContextSnapshotBuilder 初段」を追加した
- `MEMORY.md` に LLM 文脈取得は builder に集約し、depth ごとの latency を固定して監視する判断を追記した

### 詰まったこと・解決したこと
- すべての記憶系機能が実装されるまで待つと抽象化が遅すぎる
  → 初段は未実装 source を空 list / None で返し、既存 recent turns と Phase 8 memory search から段階的に移行する

### 次のセッションでやること
- Phase 8.8 実装時は `TomokoContextSnapshot` DTO、builder unit test、perf test を先に追加する

## 2026-05-24 セッション25

### やること（開始時に書く）
- 別LLMからの ContextSnapshotBuilder hardening 指摘を確認し、妥当な内容を設計へ反映する
- `ContextBuildPolicy` / `ContextBuildTrace` / parallel DB I/O / degraded context / TTL cache の方針を Markdown に追記する

### やったこと
- 指摘内容はプロジェクトのレイテンシー管理方針と整合しているため、設計へ取り込んだ
- `ARCHITECTURE.md` の ContextSnapshotBuilder 節に best-effort runtime、parallel DB I/O、`ContextBuildPolicy`、`ContextBuildTrace`、process-local TTL cache、TomoroSession 管制塔方針を追記した
- `PLAN.md` の Phase 8.8 を `ContextBuildPolicy` / `ContextBuildTrace` / timeout degraded context / parallel source / cache trace 前提に拡張した
- `PLAN.md` に Phase 8.8.1「ContextSnapshotBuilder 運用 hardening」を追加した
- `AGENTS.md` に context build の timeout / degraded / trace / TTL cache 規約を追加した
- `MEMORY.md` に確定判断と気づきを追記した

### 詰まったこと・解決したこと
- context source を増やすほど待ち時間も増える設計は危険
  → source を parallel DB I/O として読み、deadline で打ち切る best-effort runtime とする
- cache を入れると状態の真実が分散しやすい
  → process-local TTL cache は DB read の speed-up に限定し、authoritative state は cache しない方針にした

### 次のセッションでやること
- Phase 8.8 実装時は `ContextBuildPolicy` / `ContextBuildTrace` を DTO として先に作り、timeout/degraded path の unit test を最初に書く

### 外部LLMとの会話原文
[会話原文](_reference/2026-05-24-1200_設計評価と改善提案.md)

## 2026-05-25 セッション28

### やること（開始時に書く）
- Perplexity / Codex Computer Use による外部情報収集を、Tomoko 本体から切り離した Phase として PLAN.md に追記する
- 生 Markdown を filesystem に残し、Tomoko の解釈を PostgreSQL に残す二層構造を明文化する
- 不安定な Markdown / Computer Use / LLM normalize を前提に、validate / failed / archived の取り込み手順へ分解する

### やったこと
- `PLAN.md` に Phase 18「外部観測 Markdown と Tomoko 解釈パイプライン」を追記した
- `informations/work` / `archived` / `failed` / `prompts` の directory contract を定義した
- raw Markdown artifact、normalizer、DB schema、ingest Makefile、Tomoko persona interpretation、thinker / journalist 連携、Perplexity / Codex Computer Use recipe の順に小 Phase へ分解した

### 詰まったこと・解決したこと
- Perplexity の Markdown 出力や Codex Computer Use 操作は安定しない前提にした
  → Tomoko 本体へ直接接続せず、raw artifact と schema validation で隔離する方針にした
- ルールベースでニュース内容を理解する方針は否定した
  → ルールは外枠 validation と file movement に限定し、内容理解は LLM normalize / interpretation worker に寄せる方針にした

### 検証
- `git diff --check -- PLAN.md LOG.md`

### 次のセッションでやること
- Phase 18.0 を実装する場合は、`informations/` directory、`.gitignore`、sample artifact、frontmatter schema validator の unit test から始める

## 2026-05-25 セッション37

### やること（開始時に書く）
- `vad_silence_ms` を 800ms に戻し、1000ms との体感差を比較できるようにする
- 新しい `audio_start` が来た時、古い turn の再生キューと `nextPlaybackTime` が残って次の返答を遅らせないようにする
- 短い相槌で Supertonic CoreML TTS が遅く見える件をログ観測ベースで整理する

### やったこと
- `config/central_realtime.toml` の `vad_silence_ms` を 800ms に戻した
- `audio_start` で turn が切り替わる時、クライアントが古い再生キューを停止し `nextPlaybackTime` を現在時刻へ戻すようにした
- config 契約テストの期待値を 800ms に更新した

### 詰まったこと・解決したこと
- 1000ms は発話の食い込み対策としては効くが、発話終了後の体感待ちが増えるため比較用に否定して 800ms へ戻した
- `ruff` を JS ファイルに直接当てると Python として解釈されるため不適切だった
  → JS は `node --check`、Python は `ruff check .` で確認した

### 検証
- `node --check client/main.js`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_streaming_tts_pipeline.py`
  - 7 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 297 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` の実ブラウザ体感で、800ms が発話被りと返答遅れのバランスとして良いか確認する
- Supertonic F1 の短文相槌で TTS 外れ値が再現するなら、固定相槌音声キャッシュまたは短文用バックエンドを検討する

## 2026-05-25 セッション38

### やること（開始時に書く）
- WhisperKit serve の STT backend を large 系モデルへ切り替え、small 由来の誤認識を減らせるか比較する
- 既存 small serve と混ざらないよう、large 用 backend は別 port で起動できる設定にする
- `prompts/base_persona.md` を音声会話向けに調整し、聞き取り不確実時の確認・短い自然な応答・メタ発話への受け答えを明示する

### やったこと
- `config/central_realtime.toml` の active STT backend を `local_whisperkit_serve_large` に変更した
- `local_whisperkit_serve_large` を追加し、`large-v3-v20240930_626MB` を `127.0.0.1:50061` で serve する設定にした
- `prompts/base_persona.md` に音声会話向けのルールを追加した
  - 聞き取りが怪しい時は断定せず確認する
  - Tomoko の動作や遅延についての発話には、開発中のTomokoとして一緒に確認する
  - 相槌だけで終わらせず、必要なら短い確認質問を一つだけ添える
- config と persona prompt の契約テストを更新した

### 詰まったこと・解決したこと
- WhisperKit serve は起動時の `--model` が重要なので、small と同じ port だと既存 small server を健康と見なしてしまう可能性がある
  → large backend は `50061` に分け、small server と混ざらないようにした
- large モデルの初回 download / compile は重くなり得るため、このセッションでは実モデル perf までは走らせず、次回 `make server-debug` の startup warm-up ログで確認する

### 検証
- `whisperkit-cli help serve`
  - `--model` / `--host` / `--port` が利用可能であることを確認
- `mise exec -- uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_phase4_thinking.py tests/unit/test_stt_backends.py`
  - 24 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 298 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` を再起動し、WhisperKit large の startup warm-up / 実 transcript / 体感遅延を確認する
- large が遅すぎる場合は `distil large-v3` または `medium` を比較候補にする

## 2026-05-25 セッション39

### やること（開始時に書く）
- 応答推論 LLM に渡す system prompt と messages をログへ出す
- 会話が噛み合わない時に、STT 結果・persona prompt・会話履歴がどう渡ったかを `logs/server-debug.log` で追えるようにする

### やったこと
- `ThinkFastMode` が `backend.chat_stream()` を呼ぶ直前に、応答推論へ渡す prompt payload を INFO ログへ出すようにした
- ログ payload には `system_prompt` / `messages` / `device_id` / `speaker` を JSON として含める
- `tests/unit/test_phase4_thinking.py` に、LLM prompt payload がログ対象になることを固定する unit test を追加した

### 詰まったこと・解決したこと
- 最初のテストは `caplog` で捕まえようとしたが、プロジェクトの logging 設定では stderr へは出ても `caplog.text` に乗らなかった
  → `server.gateway.thinking.fast.logger.info` を monkeypatch して、呼び出し引数そのものを検証する形にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_phase4_thinking.py`
  - 9 passed
- `mise exec -- uv run ruff check .`
  - pass
- `mise exec -- uv run pytest -m unit`
  - 299 passed, 17 deselected

### 次のセッションでやること
- `make server-debug` の実会話で `ThinkFastMode llm_prompt` 行を確認し、STT transcript と実際の LLM messages のズレを見る

## 2026-05-25 セッション43

### やること（開始時に書く）
- `work/audio-recordings/` に保存された実録音 WAV を使い、gate / spectral filter の CPU コストと STT への影響を測る
- 実験結果を `work/noise-filter-experiments/` に保存し、今後の STT 前処理の判断材料にする

### やったこと
- `_tools/bench_audio_filters.py` を追加し、noise WAV と input WAV から segment gate / frame gate / spectral gate の比較 WAV と summary JSON を生成できるようにした
- `tests/unit/test_audio_filter_bench.py` を追加し、短い synthetic audio で gate / spectral filter の基本挙動を固定した
- `20260525T122451Z-noise.wav` と `20260525T122454Z-read_aloud.wav` を使い、filter ごとの CPU コストと MLX Whisper large-v3-turbo-q4 の transcription を測定した

### 詰まったこと・解決したこと
- noise 録音は `rms_db=-120.0` / `peak_db=-120.0` のほぼデジタル無音で、spectral profile としては弱かった
  → この素材では spectral gate は有効な除去対象を持たず、Whisper では `反反反...` の反復幻聴を誘発した
- read-aloud 録音は `rms_db=-47.4`、active frame ratio 20.4% とかなり低信号で、raw / frame gate では `ご視聴ありがとうございました` の定型幻聴が残った
- CPU コストは十分軽く、frame gate は約 0.014ms/audio sec、spectral gate は約 2.06ms/audio sec だった
- このケースでは「加工して聞かせる」より、segment-level の品質判定で STT 投入前に reject する方が効きそうだと判断した

### 検証
- `mise exec -- uv run python _tools/bench_audio_filters.py --noise work/audio-recordings/20260525T122451Z-noise.wav --input work/audio-recordings/20260525T122454Z-read_aloud.wav --repeat 100`
  - summary: `work/noise-filter-experiments/20260525T122454Z-read_aloud/summary.json`
- `mise exec -- uv run python _tools/bench_stt_backends.py --backends local_whisper_mlx_large_turbo_q4 --runs 1 --audio-file ...`
  - raw / frame_gate_-45db: `ご視聴ありがとうございました`
  - spectral_gate: `反反反...`
- `mise exec -- uv run pytest -m unit`
  - 305 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- STT hot path に入れるなら、まず `SpeechSegment` の `rms_db` / active frame ratio / duration を使った reject 判定を追加し、今回の低信号 read-aloud artifact が STT に入らないことを unit test で固定する
- spectral filter を続けるなら、空調やMacファン音など実ノイズが入った startup noise profile を取り直してから再評価する

## 2026-05-25 セッション44

### やること（開始時に書く）
- STT 投入直前に軽量な audio signal gate を入れ、低品質 segment で Whisper を無駄に叩かないようにする
- central session と edge remote の両方で、低信号 segment が transcriber へ届かないことを unit test で固定する

### やったこと
- `server/edge/pipeline/stt_gate.py` を追加し、duration / rms_db / peak_db / active_frame_ratio で STT 前 reject を判断できるようにした
- `TomoroSession._handle_finished_speech()` に STT signal gate を入れ、reject 時は transcriber を呼ばず VAD / streaming transcriber を reset して idle に戻すようにした
- `EdgeRemoteAudioSession` にも同じ gate を入れ、edge node 側でも gateway へ transcript を送る前に低品質音声を落とすようにした
- streaming partial STT についても、明らかに弱い chunk では `process_stream_chunk()` を呼ばないようにした
- `tests/unit/test_stt_signal_gate.py` / `tests/unit/test_edge_remote_stt_gate.py` を追加し、`tests/unit/test_phase3_stt.py` に central session の reject test を追加した

### 詰まったこと・解決したこと
- 既存の一部 unit test は `vad_processor=object()` や 100ms の synthetic segment を直接 `_handle_finished_speech()` に渡していた
  → runtime fallback として sample_rate は `getattr(..., 16000)` にし、短すぎ判定は実運用に影響しにくい 80ms へ緩めた
- 今回の主目的は「実録音のような低信号・低 active ratio segment を Whisper に入れない」ことなので、`rms_db < -45` かつ `active_frame_ratio < 0.25` を sparse low signal として reject する方針にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_signal_gate.py tests/unit/test_phase3_stt.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_phase8_memory.py tests/unit/test_reply_speech_normalizer.py tests/unit/test_edge_remote_stt_gate.py`
  - 25 passed
- `mise exec -- uv run pytest -m unit`
  - 312 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザ録音で `TomoroSession latency speech_end ... stt_gate_action=reject/accept` を確認し、reject が強すぎる場合は `low_signal_rms_db` / `low_signal_max_active_ratio` を調整する

## 2026-05-25 セッション45

### やること（開始時に書く）
- STT 前処理を単一 gate ではなく filter chain として扱える構造へ広げる
- `signal_gate` / `short_segment_merge` / `spectral_subtraction` を programmatic に ON/OFF できるようにする
- startup noise profile 相当の profile をいつでも capture できる API を用意する

### やったこと
- `SttAudioFrontend` を追加し、`enabled_filters=()` なら素通り、`("signal_gate",)` なら既存 gate、`("short_segment_merge", "signal_gate")` なら短い segment の pending / merge、`("spectral_subtraction", ...)` なら noise profile がある場合のみ spectral subtraction を通す構造にした
- `NoiseProfile` / `build_noise_profile()` / `spectral_subtract()` を追加し、startup noise profile を runtime から capture して後段 filter に渡せるようにした
- `TomoroSession` と `EdgeRemoteAudioSession` は `SttSignalGate` 直接ではなく `SttAudioFrontend` を呼ぶようにした
- 既存 `TranscriptFilter` は STT 後段の hallucination filter として残し、audio frontend とは別レイヤにした
- `tests/unit/test_stt_signal_gate.py` に filter OFF、short pending merge、spectral subtraction profile capture の unit test を追加した

### 詰まったこと・解決したこと
- short segment pending は timer を持たないため、単独の短い segment は pending のまま次 segment が来るまで STT に投げない
  → 次 segment が merge window 内なら結合して STT、window 外なら古い pending を捨てて新しい segment を評価する方針にした
- spectral subtraction は profile がない場合に何もしない
  → `enabled_filters` に入れても比較対象がない時は素通りし、profile がある時だけ処理する構造にした

### 検証
- `mise exec -- uv run pytest -m unit tests/unit/test_stt_signal_gate.py tests/unit/test_phase3_stt.py tests/unit/test_edge_remote_stt_gate.py`
  - 15 passed
- `mise exec -- uv run pytest -m unit`
  - 316 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実録音比較では `SttAudioFrontend(enabled_filters=...)` の組み合わせを変えて、raw / signal_gate / short_merge / spectral_subtraction の STT 結果と latency を比較する

## 2026-05-25 セッション46

### やること（開始時に書く）
- audio frontend に speech bandpass filter を追加し、低域床揺れとナイキスト近辺の高域ノイズを軽く削る
- 100Hz high-pass / 2kHz low-pass 案を実録音で比較し、Whisper への影響を見る
- 常時ONにできる filter と実験用 filter を分ける

### やったこと
- `speech_bandpass()` を追加し、100Hz high-pass と 7.2kHz low-pass を `SttAudioFrontend` の filter として使えるようにした
- central / edge runtime の default frontend を `("speech_bandpass", "signal_gate")` にした
- `SttAudioFrontend` 単体の default は既存 unit test 互換のため `("signal_gate",)` のままにし、runtime から明示的に bandpass をONにする形にした
- 比較用に `bandpass_100_2000hz.wav` を作成し、2kHz low-pass の STT 影響を測った

### 詰まったこと・解決したこと
- 多くの既存 unit test は `np.ones()` の DC 信号を synthetic speech として使っており、100Hz high-pass が正しく削ると STT 前 gate が reject して落ちた
  → runtime では bandpass を常時ONにしつつ、`TomoroSession` を直接作る unit test では従来互換の frontend default を使う構造にした
- 2kHz low-pass は `捨てずに` が `すけずに` 寄りに崩れ、Whisper の子音認識を悪化させる可能性が見えた
  → 常用 low-pass は 2kHz ではなく 7.2kHz とする

### 検証
- `work/noise-filter-experiments/20260525T125851Z-read_aloud/all_filters_bandpass.wav`
  - `short_segment_merge` / `speech_bandpass` / `spectral_subtraction` / `signal_gate`
  - frontend: 約 9.2ms / 5秒音声、約 1.84ms/audio sec
  - Whisper: `うんそうだよ ともこ 短い声をすてずに 続きの`
- `work/noise-filter-experiments/20260525T125851Z-read_aloud/bandpass_100_2000hz.wav`
  - Whisper: `うんそうだよともこ短い声をすけずに続きの`
- `mise exec -- uv run pytest -m unit`
  - 318 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実ブラウザで `filters=speech_bandpass,signal_gate` の accept/reject ログを見て、通常発話を削りすぎていないか確認する

## 2026-05-25 セッション47

### やること（開始時に書く）
- RNNoise 系 denoise を実験用 filter として追加し、実録音に対する処理時間と Whisper 結果を確認する
- 常時ONにするかどうかを、少なくとも良い録音と低信号録音の2ケースで判断する

### やったこと
- `ffmpeg` の `arnndn` filter が利用可能であることを確認した
- `work/rnnoise-models/std.rnnn` に arnndn model を保存した
- `_tools/bench_rnnoise_filter.py` を追加し、WAV に RNNoise 系 denoise をかけて処理時間と metrics を JSON に保存できるようにした
- `SttAudioFrontend` に実験用 `rnnoise` filter を追加した
  - default OFF
  - model file がない場合は素通り
  - enabled の時だけ `ffmpeg arnndn` を呼ぶ
- `tests/unit/test_rnnoise_bench_tool.py` と `tests/unit/test_stt_signal_gate.py` の RNNoise 関連 test を追加した

### 詰まったこと・解決したこと
- Python の RNNoise binding は現環境に入っていなかった
  → 既に利用可能な `ffmpeg arnndn` を使い、追加 Python dependency なしで検証する形にした
- RNNoise は前処理としては動くが、5秒音声で約60-65ms、約12-13ms/audio sec と、現行 frontend より一桁重い
  → 常時ONではなく実験用 filter とする

### 検証
- 良い録音 `20260525T125851Z-read_aloud.wav`
  - RNNoise: 約65.1ms / 5秒音声、約13.0ms/audio sec
  - Whisper: `うんそうだよ ともこ 短い声をすてずに 続きの`
- 低信号録音 `20260525T122454Z-read_aloud.wav`
  - RNNoise: 約60.1ms / 5秒音声、約12.0ms/audio sec
  - Whisper: `おやすみなさい`
- `mise exec -- uv run pytest -m unit`
  - 321 passed, 17 deselected
- `mise exec -- uv run ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- RNNoise は低信号 hallucination を完全には止めないため、常時ON候補にせず、signal gate reject を優先する
- 実ノイズが強い録音素材を取ったら、`rnnoise` と `spectral_subtraction` を改めて比較する

## 2026-05-27 セッション12

### やること（開始時に書く）
- Apple Speech STT の CPU / ANE 使用を人間が `mactop` で観測できるよう、central realtime の active STT を `local_apple_speech_ja` に切り替える
- 既存 `/ws` / `TomoroSession` には触れず、config と契約テストだけを更新する
- unit test と lint を通し、観測用に `make server-debug` で起動できる状態にする

### やったこと
- `config/central_realtime.toml` の `stt_backend` を `local_apple_speech_ja` に変更した
- `tests/unit/test_phase0_config.py` の active STT 契約を Apple Speech に更新した
- MLX Whisper large turbo q4 は比較/rollback 用 backend として残した
- MEMORY.md に、Apple Speech active は CPU / ANE 観測用の一時判断であることを追記した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_stt_backends.py`
  - 18 passed
- `.venv/bin/python -m pytest`
  - 343 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- `.venv/bin/python _tools/bench_stt_backends.py --backends local_apple_speech_ja --runs 1 --output logs/stt-apple-active-smoke.json`
  - warm 189.6ms / measured 196.3ms
  - transcript `智子3 +3 =いくつですか`

### 次のセッションでやること
- `make server-debug` で実ブラウザ会話を動かし、人間側で `mactop` を見て Apple Speech STT 実行中の CPU / ANE / GPU 使用傾向を確認する

### 起動失敗修正
- `AppleSpeechSTT.warm_up()` が 1 秒の無音を実 transcribe しており、Apple Speech が正常に `No speech detected` を返した結果、FastAPI startup が失敗していた
- Apple Speech の warm-up は実 STT ではなく sidecar build / existence check だけに変更した
- `test_apple_speech_warm_up_only_ensures_sidecar` を追加し、warm-up で無音 audio を transcribe しないことを固定した

### 検証追記
- `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_startup_warmup.py tests/unit/test_phase0_config.py`
  - 22 passed
- `TOMOKO_CONFIG=config/central_realtime.toml .venv/bin/python - <<'PY' ... _warm_up_app()`
  - STT warm-up `local_apple_speech_ja` completed in 0.8ms
  - TTS / conversation / embedding warm-up まで完了
- `make server-debug`
  - port 8000 が既存プロセス使用中で起動不可だったため、上の `_warm_up_app()` 直接実行で startup path を確認した
- `.venv/bin/python -m pytest`
  - 344 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-27 セッション12

### やること（開始時に書く）
- go/cancel 判定用の local small LLM worker 方針を Phase として PLAN.md に追記する
- LM Studio の会話生成キューとは分離し、Tomoko 管理下の Makefile entry で起動する前提を明文化する

### やったこと
- `PLAN.md` に Phase 10.11 local turn-taking judge worker を追記した
- `make turn-taking-worker` / `make turn-taking-worker-once` を完了条件に入れ、会話 26B の LM Studio queue と制御 worker queue を分離する方針を明文化した
- rule-first judge、local small LLM worker、TomoroSession 接続、実ブラウザ評価の subphase に分けた

### 詰まったこと・解決したこと
- 計画追記のみなので実装・テスト実行はまだ行っていない

### 次のセッションでやること
- Phase 10.11 の契約に沿って `TurnTakingJudge` / local worker / Makefile entry / unit test を実装する

## 2026-05-27 セッション11

### やること（開始時に書く）
- Apple Speech STT 追加後に unit test が長くなっていないか確認する
- `tests/unit/test_stt_backends.py` と関連 factory test に、実 Swift build / 実 subprocess / 実 STT が混ざっていないか調べる
- 遅い原因が test 側にあれば mock 境界を直し、unit test を再実行する

### やったこと
- `tests/unit/test_stt_backends.py --durations=0` を確認し、Apple Speech unit は mock されており実 Swift build / 実 STT は走っていないことを確認した
- full unit の `--durations=20` を確認し、最遅 unit は 0.07s で、Apple Speech 周辺は遅延原因ではなかった
- 裸の `pytest` が perf / integration も対象にし得る設定だったため、`pyproject.toml` に `addopts = "-m unit"` を追加した
- `tests/unit/test_phase0_config.py` に pytest default が unit である契約を追加した

### 詰まったこと・解決したこと
- 長く見えた原因は unit test の中身ではなく、裸の `pytest` が `tests/perf/test_stt_latency.py` など実モデル系 perf を拾える設定だったこと
  - 解決: default は unit のみにし、perf / integration は従来どおり `pytest -m perf` / `pytest -m integration` で明示実行する

### 検証
- `.venv/bin/python -m pytest --collect-only -q`
  - 343/360 tests collected、17 deselected
- `.venv/bin/python -m pytest -m perf --collect-only -q tests/perf/test_stt_latency.py`
  - 5 perf tests collected
- `.venv/bin/python -m pytest -m integration --collect-only -q tests/integration/test_phase90_candidates_db.py`
  - 1 integration test collected
- `.venv/bin/python -m pytest --durations=20`
  - 343 passed, 17 deselected in 0.74s
- `make test-unit`
  - 343 passed, 17 deselected in 0.72s
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- perf STT を実行したい時だけ、`pytest -m perf --tb=short tests/perf/test_stt_latency.py -s` または `_tools/bench_stt_backends.py` を明示する

## 2026-05-27 セッション11

### やること（開始時に書く）
- 実会話ログで、LLM 推論中に `stale reply cancelled reason=resumed_user_speech_before_output` が出て返答が捨てられる原因を切り分ける
- VAD が `listening` に入っただけの低音量/空 STT 区間で、未出力 reply をキャンセルしないようにする

### やったこと
- 09:10:57 付近の実ログを確認し、LLM reply 開始直後に VAD が `listening` へ入っただけで未出力 reply がキャンセルされていることを確認した
- その直後の STT は `text=''` / `reason=empty` であり、実際には意味のある follow-up ではなかった
- `TomoroSession._transition("listening")` では未出力 reply をキャンセルしないようにした
- 空 transcript では既存 reply が生き残る regression test を追加し、意味のある follow-up は従来通り次の reply task 起動時に差し替える方針にした

### 詰まったこと・解決したこと
- `resumed_user_speech_before_output` の意図は、ユーザーが推論中に話し足した時に古い reply を捨てることだった
  - ただし VAD の listening は「発話候補」であって「確定発話」ではないため、Apple Speech の空認識と組み合わさると返答だけ消えて会話が終わったように見えた
  - 解決: キャンセル基準を listening 遷移から確定 transcript 側へ寄せ、空 STT ではキャンセルしない

### 検証
- `.venv/bin/python -m pytest tests/unit/test_streaming_tts_pipeline.py tests/unit/test_attention_mode.py tests/unit/test_barge_in.py -q`
  - 19 passed
- `.venv/bin/python -m pytest -q`
  - 346 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で、LLM 推論直後の低音量/空 STT 区間により Tomoko の返答が消えないことを確認する
- 実発話で推論中に追い発話した場合、古い reply がどの程度話し始めるかを観察し、必要なら「listening 中は出力を短時間 defer」する

## 2026-05-27 セッション10

### やること（開始時に書く）
- Apple 純正 Speech framework を Swift sidecar CLI として Python から呼べるようにする
- 同じ録音 WAV で Apple Speech と `local_whisper_mlx_large_turbo_q4` の transcript / latency を比較できるベンチを追加する
- 既存 `/ws` や `TomoroSession` には触れず、STT backend 境界と `_tools` の比較経路に閉じる

### やったこと
- `_tools/apple_speech_stt/AppleSpeechSTT.swift` と `Info.plist` を追加し、Apple Speech framework を使う Swift sidecar CLI を作った
- `server/edge/pipeline/stt_apple.py` と `apple_speech` backend factory を追加した
- `config/central_realtime.toml` に `local_apple_speech_ja` を比較 lane として追加した
- 既存 `_tools/bench_stt_backends.py` で Apple Speech と `local_whisper_mlx_large_turbo_q4` を同じ WAV で比較できるようにした

### 詰まったこと・解決したこと
- CLI から `SFSpeechRecognizer.requestAuthorization` を呼ぶと、TCC が usage description を認識せず `SIGABRT` した
  - 解決: sidecar を `.app` bundle + embedded Info.plist + ad-hoc codesign でビルドし、authorization request は明示実行しない
- semaphore で main thread を塞ぐと `recognitionTask` の callback が進まず timeout した
  - 解決: RunLoop を短い間隔で回しながら final callback を待つ

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py tests/unit/test_stt_bench_tool.py`
  - 23 passed
- synthetic `say` 音声 3 runs:
  - Apple Speech avg 183.3ms / text `智子3 +3 =いくつですか`
  - MLX Whisper large turbo q4 avg 240.6ms / text `ともこ3たす3はいくつですか`
- 実録音 `work/audio-recordings/20260525T125851Z-read_aloud.wav` 3 runs:
  - Apple Speech avg 242.7ms / text `うんそうだよ智子短いです声を捨てずに続き`
  - MLX Whisper large turbo q4 avg 248.7ms / text `うんそうだよ ともこ 短い声を捨てずに 続きの`
- JSON:
  - `logs/stt-apple-speech-vs-mlx-large-turbo-q4.json`
  - `logs/stt-apple-speech-vs-mlx-large-turbo-q4-read-aloud.json`

### 次のセッションでやること
- Apple Speech は active STT へ切り替えず比較 lane として残し、実会話録音が増えたら読み・表記ゆれをさらに比較する

## 2026-05-25 セッション48

### やること（開始時に書く）
- RNNoise は効果に対するコストが厳しいため、実ランタイムではOFFのままにする
- central realtime の会話モデルを `local_gemma4_e2b_mlx` に変更する
- TTS backend を Kokoro に変更する

### やったこと
- `config/central_realtime.toml` の `conversation_backend` を `local_gemma4_e2b_mlx` に変更した
- `config/central_realtime.toml` の `conversation_fallback` を `local_lfm25_12b_jp_mlx` に変更し、前メインモデルを fallback として残した
- `config/central_realtime.toml` と `config/edge_kitchen.toml` の `tts_backend` を `kokoro_mlx` に変更した
- `kokoro_mlx` backend に `sample_rate = 24000` を明示し、`KokoroMLXBackend.from_spec()` が config の `sample_rate` を反映するようにした
- 音声 stack soak tool の default TTS / conversation backend を `kokoro_mlx` / `local_gemma4_e2b_mlx` に更新した

### 詰まったこと・解決したこと
- 素の `pytest` は Python 3.14 環境で起動し、`psycopg` / `ollama` が見えず collection error になった
  → repo の `.venv/bin/python -m pytest` で検証した
- Kokoro MLX backend は実装上 24kHz default だったが、config には `sample_rate` がなく `from_spec()` でも読んでいなかった
  → config と factory の両方を揃え、設定で保証できる形にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_phase14_edge_split.py tests/unit/test_voice_stack_soak_tool.py tests/unit/test_stt_signal_gate.py tests/unit/test_kokoro_mlx_tts.py`
  - 34 passed

### 次のセッションでやること
- full unit / lint を回し、config 切り替えが既存の router / TTS 周辺に波及していないか確認する

### 検証追記
- `.venv/bin/python -m pytest -m unit`
  - 321 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-27 セッション5

### やること（開始時に書く）
- 最新 `logs/server-debug.log` / `logs/backend-trace.jsonl` を見て、26B A4B 採用後の実会話品質を確認する
- E2B / E4B より会話として楽しい領域に入ったか、内容とレイテンシーの両面で判断する
- メイン会話モデル探索を続けるか、自発的発話調整へ移るかを決める

### やったこと
- 03:55 台の実ブラウザ会話ログを確認した
- 会話 backend が `lmstudio_gemma4_26b_a4b` / `gemma-4-26b-a4b-it-mlx` で fallback していないことを確認した
- 「人生の生き方を考える上で大事にすること」への返答で、一般論ではなく相手の曖昧さを受け止め、次の内省質問へつなげられていることを確認した
- ユーザー体感として、E2B 時より明らかに楽しく、Romi よりも楽しいと判断された

### 詰まったこと・解決したこと
- first audio は 1.1〜2.2 秒程度で、即応性は犠牲になる
  → ただし意味のある会話の楽しさがレイテンシーを上回るため、メイン会話は 26B A4B で一旦FIXする
- 次に掘るべき対象はモデルサイズ探索ではなく、Tomoko からの自発的発話・話しかける間合い・候補選択へ移る

### 次のセッションでやること
- 自発的発話の runtime log と candidate / policy trace を確認し、Tomoko から話しかける段階の調整に入る

## 2026-05-27 セッション6

### やること（開始時に書く）
- 自発発話の初回実ログを踏まえ、PLAN.md に「話しかけ方の自然さ」を調整する Phase を追記する
- 候補文の橋渡し、会話開始後の文脈回復、候補品質評価、ログ確認手順を実装者が迷わない粒度に分解する

### やったこと
- PLAN.md に `2026-05-27 追記: Phase 10.10 自発発話の会話開始品質調整` を追加した
- 04:09 台の実ログで確認した、自発候補発話から follow-up conversation session へ入った流れを Phase の前提として記録した
- 課題を candidate 文の唐突さ、聞き返し時の話題保持、主語欠け文、境界 score の LLM judge 発話に分解した
- Phase 10.10.0〜10.10.4 として、ログ評価、generated_text 契約、会話文脈への載せ方、間合い調整、実ブラウザ smoke を追加した

### 検証
- `git diff --check -- PLAN.md LOG.md`
  - pass

### 次のセッションでやること
- Phase 10.10.0 から着手し、自発発話ログを同じ手順で評価できる inspection 手順を文書化する

## 2026-05-27 セッション7

### やること（開始時に書く）
- Phase 10.10 全体をまとめて進める
- 自発発話ログ評価手順、candidate generated_text 契約、follow-up 時の直前自発話題文脈、候補 policy tuning を実装する
- 迷った箇所は仮判断で前へ進め、実ブラウザ smoke で残る判断は起床後に確認できる形で LOG / MEMORY に残す

### やったこと
- `_docs/evaluation.md` に Phase 10.10 用の自発発話 inspection 手順を追記した
  - `logs/server-debug.log` から candidate fetched / policy / start reply / followup session / prompt / reply を見る
  - `utterance_candidates` / `conversation_sessions` の確認 SQL を固定した
  - `starts_conversation` / `not_abrupt` / `self_contained` / `recoverable` / `low_intrusion` の評価語彙を固定した
- `LLMUtteranceEvaluator` の prompt を、単なる興味文ではなく会話開始用 `generated_text` を返す契約に強めた
- candidate 生成後の正規化で、主語欠け断片、長すぎる文、最新情報の断定を落とすようにした
- world observation 由来 candidate に `topic_shift_bridge_required` tag を付け、必要なら `さっきの話とは別で、` を補うようにした
- `TomoroSession.start_precomputed_reply()` が直前の initiative / arrival 発話本文、source、candidate id を保持するようにした
- 人間の follow-up で通常会話に入る最初の LLM prompt に、直前の Tomoko 自発発話を assistant turn として入れるようにした
- 自発発話だけでは `conversation_session` を開始しない既存判断を regression test で固定した
- `CandidateSpeakPolicy` で `recent_heavy_conversation` 直後の bridge なし topic shift に小さな penalty を付けた
- 現行 `config/central_realtime.toml` の active STT が `local_whisper_mlx_large_turbo_q4` へ戻っていたため、config 契約テストを実設定に合わせた

### 詰まったこと・解決したこと
- `make thinker-once` は成功したが、既存 candidate の dedupe により新規 text-ready candidate は増えなかった
  - 結果: `candidate_generated=6 candidate_inserted=0 candidate_kept=0 pregenerated=0 arrival_behavior=wait_silent`
  - DB には active candidate が 11 件あり、既存の spoken world observation candidate も確認できた
- `make server-debug` は起動し、startup warm-up と root HTML 取得まで確認した
  - STT warm-up: `local_whisper_mlx_large_turbo_q4` 1218.0ms
  - TTS warm-up: `voicevox_tsumugi` 138.7ms
  - conversation warm-up: `lmstudio_gemma4_26b_a4b` 366.8ms
  - embedding warm-up: `local_bge_m3` 7811.0ms
  - `GET /` は 200 で HTML を返した
- 無人では実マイクから ambient idle -> initiative 発話 -> 人間 follow-up -> 2 turn 継続の自然さを評価できないため、Phase 10.10.4 の実ブラウザ会話評価は積み残した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase18_world_observation_source.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase4_thinking.py`
  - 58 passed
- `.venv/bin/python -m pytest -m unit`
  - 339 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- `make thinker-once`
  - pass, but inserted/kept/pregenerated are all 0 due to existing active candidates / dedupe
- `make server-debug`
  - startup complete, `GET /` 200, then stopped manually after smoke

### 次のセッションでやること
- `make server-debug` の実ブラウザで、ambient idle から 1 回以上 initiative を発話させる
- その場で人間が返答し、直後 3 turn の `ThinkFastMode llm_prompt` と reply を評価する
- 成功例と要改善例を最低 1 件ずつ LOG に残し、必要なら active 既存 candidate を dismiss / expire して新しい bridge 付き candidate を試す

## 2026-05-27 セッション10

### やること（開始時に書く）
- Apple Speech STT が `No speech detected` で例外を投げ、WebSocket / server task まで落ちる経路を修正する
- Apple Speech backend が現状は比較用の薄い実験レーンであり、本番品質の失敗分類までは未完成であることを確認する

### やったこと
- `AppleSpeechSTT` で sidecar の `No speech detected` を空 transcript として扱うようにした
- Apple Speech の未知の sidecar error は従来通り `RuntimeError` として残し、権限や実行失敗を隠さないようにした
- Apple Speech backend はまだ比較用の薄い実験レーンであり、失敗分類の詰めが必要だと MEMORY に追記した

### 詰まったこと・解決したこと
- VAD が拾った短い/弱い区間で Apple Speech が発話なし判定を返すと、STT の通常の空認識ではなく例外として websocket task を落としていた
  - 解決: `No speech detected` だけを非 fatal に分類し、以後の既存 transcript empty/drop 経路へ流す

### 検証
- `.venv/bin/python -m pytest tests/unit/test_stt_backends.py -q`
  - 17 passed
- `.venv/bin/python -m pytest -q`
  - 346 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で Apple Speech の空 transcript が WebSocket を落とさず drop されることを確認する
- engaged 滞在時間が短い問題は STT とは別に、attention timeout / VAD accepted segment / transcript empty の時系列で切り分ける

## 2026-05-27 セッション8

### やること（開始時に書く）
- ユーザー回答を反映する
  - active STT は `local_whisper_mlx_large_turbo_q4` が正
  - bridge 文は候補生成 prompt 側へ寄せ、自動補完は弱める
  - 断片 candidate reject は厳しめのまま
  - recent heavy conversation penalty は軽く入れたまま
  - initiative / arrival 両方を follow-up context に入れる実装は維持
- 既存候補で試すため、候補が来たが発話しなかった理由をブラウザ上で見える UI にする

### やったこと
- `LLMUtteranceEvaluator` の world observation bridge 自動補完を外し、bridge は prompt 側が生成する契約に戻した
- `CandidateCommandRunner` が `TomoroSession` の transition emission を WebSocket へ流すようにした
- ブラウザ UI に `Candidate` 表示欄を追加し、fetch / skip / reply / arrival / command failure の最新イベントを表示するようにした
- policy wait 時に `initiative_skipped` と policy score / threshold / reason がブラウザへ届く regression test を追加した
- 接続開始時の arrival emission が混ざっても、Phase1 の state / debug 契約を検証できるようにテストを更新した

### 詰まったこと・解決したこと
- 全体 unit で Phase1 の `sent_json` 完全一致テストが、接続開始時の `arrival_fetch_requested` emission 追加により失敗した
  - 解決: Phase1 テストは目的の state / debug event だけを抽出して検証し、candidate visibility event との共存を許容した
- Python ruff に `client/main.js` を直接渡すと JavaScript を Python として解析して失敗する
  - 解決: JS は `node --check client/main.js`、Python は `ruff check .` で検証した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase18_world_observation_source.py tests/unit/test_phase106_initiative_policy.py`
  - 28 passed
- `node --check client/main.js`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 340 passed, 17 deselected
- `git diff --check`
  - pass

### 次のセッションでやること
- `make server-debug` のブラウザで、Candidate 欄に `initiative_fetch_requested` / `initiative_skipped` / `initiative_reply_requested` が見えることを実マイク込みで確認する
- 既存 active candidate を使い、発話した例と policy / gate で止まった例を LOG に残す

## 2026-05-27 セッション9

### やること（開始時に書く）
- cooldown に入るのが早すぎる疑いについて、最新 `logs/server-debug.log` を確認する
- `TomoroSession` の state / attention_mode 遷移、VAD/STT/transcript/playback の時系列を見て原因候補を切り分ける

### やったこと
- `logs/server-debug.log` の 04:51 台を確認し、Tomoko の音声再生中に `engaged -> cooldown` が発火していることを確認した
  - 04:51:23.330 chunk 18 playback_started
  - 04:51:32.129 `engaged -> cooldown`
  - 04:51:34.417 chunk 18 playback_ended
  - 04:51:39.638 chunk 20 playback_ended
  - 04:51:40.128 `cooldown -> ambient`
- `TomoroSession.process_audio_chunk()` が VAD state `idle` の無音 chunk だけで attention idle を進めており、playback state を見ていないことを確認した
- playback state が `idle` の時だけ `_advance_attention_idle()` を進めるように修正した
- 再生中に attention idle が進まない regression test を追加した

### 詰まったこと・解決したこと
- cooldown が早い原因は timeout 値そのものより、Tomoko の長い再生時間も idle として数えていたことだった
  - 解決: `AudioTurnController.playback_state` が `client_playing` / `speaking` / `echo_grace` の間は attention timeout を止める

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_attention_mode.py tests/unit/test_barge_in.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_candidate_command_runner.py`
  - 35 passed
- `.venv/bin/python -m ruff check server/session.py tests/unit/test_attention_mode.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 341 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` の実ブラウザで、長い Tomoko 返答の再生中に `engaged -> cooldown` が出ないことを確認する
- 最終 chunk の playback_ended から、意図した 8 秒程度の無音後に cooldown / ambient へ進むかを確認する

## 2026-05-27 セッション12

### やること（開始時に書く）
- Phase 10.11 local turn-taking judge worker 全体を実装する
- turn-taking 判定 DTO / rule-first judge / worker 起動口 / TomoroSession 接続 / unit test を追加する
- 会話 26B queue と turn-taking judge queue を分離し、worker timeout/error では rule fallback で継続する

### やったこと
- `server/shared/models.py` に `TurnTakingInput` / `TurnTakingDecision` / audio metrics / enum DTO を追加した
- `RuleFirstTurnTakingJudge` を追加し、空 transcript / 低信号 / stop word / 訂正 / 相槌 / 実質 follow-up の基本分類を固定した
- `TurnTakingWorkerClient` を追加し、明確な rule 判定は worker を待たず、曖昧な `defer_output` だけ別 process worker へ投げるようにした
- `background-process/run_turn_taking_worker.py` を追加した
  - `make turn-taking-worker` で小型 MLX model worker を常駐起動する
  - `make turn-taking-worker-once` はモデルロードなしの rule sample を 1 回実行する
- `TomoroSession` に turn-taking judge を接続した
  - pending reply 中の確定 transcript だけを judge に通す
  - `ignore_as_noise` / `continue_current_reply` は observer として消費し、既存 reply を維持する
  - `defer_output` は短時間だけ reply output を遅らせる
  - `restart_with_new_input` は既存 reply を interrupted として止め、新 transcript の通常参加経路へ戻す
  - `stop_speaking` は stop-intent observation と stop ack へ接続する
- stop が並行して 2 回入った時に audio stop が二重送信されないよう、短い suppress window を入れた
- PLAN.md の Phase 10.11.0〜10.11.3 をチェック済みにした

### 詰まったこと・解決したこと
- 最初は playback 中 transcript も turn-taking judge に通したが、既存の `BargeInDetector` による playback echo 抑止と衝突しやすかった
  - 解決: Phase 10.11 の主対象である pending reply / 生成中 reply の確定 transcript に judge を適用し、playback echo は既存 barge-in 層に残した
- 未出力 reply 中の `さっきの続き` が小 follow-up として observer 消費され、既存 engaged follow-up test を壊した
  - 解決: 相槌以外の確定 follow-up は `restart_with_new_input` として、通常の参加・会話 session 経路へ戻すよう rule を調整した
- `make turn-taking-worker-once` は background-process 配下から実行されるため `server` package import に失敗した
  - 解決: 既存 background wrapper と同じように repo root を `sys.path` に足した

### 検証
- `make turn-taking-worker-once`
  - `{"decision": "continue_current_reply", "reason": "backchannel", "source": "rule", ...}` を出力
- `.venv/bin/python -m pytest -m unit tests/unit/test_turn_taking_judge.py tests/unit/test_turn_taking_worker_client.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_barge_in.py -q`
  - 19 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_session_concurrency.py tests/unit/test_turn_taking_judge.py tests/unit/test_turn_taking_worker_client.py -q`
  - 36 passed
- `.venv/bin/python -m pytest -m unit`
  - 353 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` と `make turn-taking-worker` を別 terminal で起動し、実マイクで Phase 10.11.4 の4ケースを確認する
- 実ログの `turn_taking_decision` / `reply_start` / `first_reply_text` / `first_audio_chunk` / `playback_started` を時系列で見て、rule / worker / timeout / STT のどこに tuning が必要か分ける

## 2026-05-27 セッション13

### やること（開始時に書く）
- Phase 10.11 実ブラウザ確認で worker judge が飛ばない理由をログで見えるようにする
- playback 中の「待って」「ストップ」などの interrupt 候補を turn-taking judge に通す
- 既存 playback echo 抑止と衝突しないよう、非 interrupt は従来通り barge-in / echo grace に残す

### やったこと
- `TomoroSession` に `turn_taking_skipped` ログを追加した
  - active reply も playback もない場合: `no_active_reply_or_playback`
  - playback 中だが interrupt 候補ではない場合: `playback_non_interrupt_candidate`
- playback 中でも `待って` / `ストップ` / `違う` 系の interrupt 候補は `TurnTakingJudge` に通すようにした
- `RuleFirstTurnTakingJudge` に `WAIT_WORDS` を追加し、`ちょっと待って` を `stop_speaking` として扱うようにした
- 非 interrupt の playback transcript は従来通り `BargeInDetector` / `playback_active_chunk` / `playback_ended_grace` に残した
- unit test で、playback 中の通常 follow-up は echo 抑止、playback 中の `ちょっと待って` は `turn_taking_decision=stop_speaking` へ入ることを固定した

### 詰まったこと・解決したこと
- `caplog` では既存 logging 設定の都合で `server.session` の INFO を捕まえられなかった
  - 実ログには出ていたため、テストでは挙動検証に寄せ、ログ文言は `server-debug.log` で確認する方針にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_turn_taking_judge.py tests/unit/test_barge_in.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_session_concurrency.py -q`
  - 22 passed
- `.venv/bin/python -m pytest -m unit`
  - 355 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 追加対応
- 実ログで STT が `ちょっと待って` ではなく `この映像ちょっとちょっと待とうか` と起こし、`playback_non_interrupt_candidate` で skip された
- その後、既存 stop-intent worker が `はい、とめますね` の hard stop ack を出した
- `RuleFirstTurnTakingJudge.WAIT_WORDS` に `待とう` / `まとう` 系を追加した
- `TomoroSession._is_turn_taking_interrupt_candidate()` で `should_record_stop_intent_candidate(text)` も見るようにし、stop-intent が拾う語は turn-taking 側で先に候補扱いするようにした
- regression test として `この映像ちょっとちょっと待とうか` が playback 中でも `turn_taking_decision=stop_speaking` へ入ることを追加した

### 追加検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_turn_taking_judge.py tests/unit/test_barge_in.py -q`
  - 18 passed
- `.venv/bin/python -m pytest -m unit`
  - 357 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- `make server-debug` の実ブラウザで、再生中に `ちょっと待って` / `ストップ` を言い、`turn_taking_decision` が出ることを確認する
- 通常の回り込みでは `turn_taking_skipped reason=playback_non_interrupt_candidate` が出て、echo 抑止へ流れることを確認する

## 2026-05-28 セッション3

### やること（開始時に書く）
- 現行 config を見て起動前準備を行う `make prepare` を追加する
- active TTS が VOICEVOX 系なら Engine の応答を確認し、落ちていれば VOICEVOX.app 起動を試す
- active STT が Apple Speech 系なら Swift sidecar app の build / codesign を起動前に済ませる

### やったこと
- `_tools/prepare_runtime.py` を追加した
- `make prepare` を追加し、`config/central_realtime.toml` の現行 active backend を見て起動前準備を行うようにした
- active TTS が `voicevox` / `voicevox_stream` の場合は `/version` を確認し、落ちていれば macOS の `open -a VOICEVOX` で VOICEVOX.app の起動を試すようにした
- active STT が `apple_speech` の場合は `AppleSpeechSTT.warm_up()` を呼び、Swift sidecar app の build / codesign を済ませるようにした
- README の初回セットアップとコマンド一覧に `make prepare` を追加した

### 詰まったこと・解決したこと
- VOICEVOX は外部 app / Engine なので、Tomoko 側では Engine が応答していなければ app 起動を試し、一定時間待っても `/version` が返らなければ prepare 失敗として扱う
- Apple Speech は startup で実 STT を走らせると `No speech detected` が通常系として出るため、prepare では sidecar build / existence check に留める

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_prepare_runtime.py -q`
  - 3 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_prepare_runtime.py tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py -q`
  - 24 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_prepare_runtime.py tests/unit/test_makefile_process_entries.py -q`
  - 8 passed
- `.venv/bin/python -m pytest -m unit`
  - 364 passed, 17 deselected
- `.venv/bin/python -m ruff check _tools/prepare_runtime.py tests/unit/test_prepare_runtime.py`
  - pass
- `.venv/bin/python -m ruff check .`
  - pass
- `make -n prepare`
  - `mise exec -- uv run python _tools/prepare_runtime.py --config config/central_realtime.toml` を確認

### 次のセッションでやること
- 実機で `make prepare` を実行し、VOICEVOX.app 起動待ちと Apple Speech sidecar build のログ表示が十分か確認する
- `.cache/tomoko/AppleSpeechSTT.app` を消した状態で `make prepare` を実行し、clone 直後相当の cold path で sidecar build / codesign / 起動前準備が通ることを確認する

### 追加対応
- README の STT default 説明を更新し、`local_apple_speech_ja` を単なる比較 lane ではなく Mac 実機で有力な primary lane として説明した
- Apple Speech を使う理由として、OS 側 Speech framework に STT を逃がして GPU / MLX 側を会話 LLM や TTS に残せる点を追記した
- Whisper MLX / WhisperKit は fallback / 比較候補として残す位置づけに整理した

## 2026-05-28 セッション4

### やること（開始時に書く）
- ambient / 会話中の STT 確定 transcript を、既存 `/ws` の JSON event として UI に流す
- DB 保存経路や conversation session lifecycle は変えず、UI 表示用の観測 event だけを追加する
- UI は高さ固定の transcript log div に entry を追記し、既存表示も画面内に収まりやすい高さ制約にする

### やったこと
- `TomoroSession.process_transcript()` から `transcript_final` event を送るようにした
  - observer / withdraw / called / invited の分類結果を event に含める
  - 会話参加発話では、`_ensure_conversation_session()` 後の `active_conversation_session_id` を event に含める
  - filtered final transcript は従来どおり drop し、UI にも出さない
- `client/index.html` に固定高の transcript log 領域を追加した
- `client/main.js` で `transcript_final` を最新順に追記し、最大 8 entry に制限した
- `client/styles.css` で panel / meters / read/debug/candidate/reply の高さを詰め、transcript log は `height: 132px` + overflow hidden にした
- `MEMORY.md` に、STT final transcript は既存 `/ws` の観測 event として UI に流す判断を追記した

### 詰まったこと・解決したこと
- Node REPL 環境に Playwright が入っておらず、headless screenshot での visual QA はできなかった
  - 解決: 一時的に `uvicorn` を起動して `/` の HTML 応答と transcript log DOM を確認し、unit / JS 構文 / ruff / diff check で検証した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase3_stt.py tests/unit/test_phase85_conversation_sessions.py -q`
  - 11 passed
- `node --check client/main.js`
  - pass
- `.venv/bin/python -m ruff check server/session.py tests/unit/test_phase3_stt.py tests/unit/test_phase85_conversation_sessions.py`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 364 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass
- `curl -s http://127.0.0.1:8017/ | rg -n "transcript-log|reply-container|candidate-result"`
  - transcript log / reply / candidate DOM が返ることを確認

### 次のセッションでやること
- 実ブラウザのマイク入力で、ambient observer と called/invited 発話が transcript log に積まれることを確認する
- 必要なら transcript log の高さ・件数・色分けを実画面で微調整する

## 2026-05-28 セッション15

### やること（開始時に書く）
- Phase 10.15.R として、現在の 10.15 実装差分を一度戻す
- Phase 10.16 として `_RUNTIME_STATE_FIELDS` / `__getattr__` / `__setattr__` を消し、state access を `runtime_state.xxx` または意味 method に寄せる
- Phase 10.15.Re として、runtime state proxy なしで `accept_signal` / dispatcher / flow を再実装する
- コミットは行わない

### やったこと
- Phase 10.15.R として、直前の 10.15 実装コードだけを戻し、PLAN / MEMORY / LOG の設計判断は残した
- Phase 10.16 として、`TomoroSession` から `_RUNTIME_STATE_FIELDS` / `__getattr__` / `__setattr__` を削除した
- `core.py` / reducer / reply orchestrator の state access を `runtime_state.xxx` へ明示化した
- `state` / `attention_mode` / `latest_segment` / `active_conversation_session_id` は read-only property として残し、テスト内の直接代入は `runtime_state` へ寄せた
- Phase 10.15.Re として、`SessionInputSignal` / `SessionOutputSignal`、gateway port 分類、`TomoroSession.accept_signal()`、`SessionSignalDispatcher`、`TranscriptFlow` を再実装した
- gateway / edge adapter / candidate command runner の semantic session input を `accept_signal()` 経由へ戻した
- PLAN.md の Phase 10.15.R / 10.16 / 10.15.Re のチェックを更新し、MEMORY.md に proxy 廃止後の判断を追記した

### 詰まったこと・解決したこと
- proxy 削除後、既存テストの一部が `session.attention_mode = ...` / `session.state = ...` の直接代入に依存していた
  - 解決: compatibility property は read-only にし、テストの setup は `session.runtime_state.xxx = ...` に明示化した
- `accept_signal()` は `SessionEvent` 以外では `TransitionResult` を返さないため、candidate runner など result を必要とする箇所では helper で `None` でないことを明示した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_state.py tests/unit/test_session_reducer.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py -q`
  - 32 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_signals.py tests/unit/test_phase14_edge_split.py tests/unit/test_session_state.py tests/unit/test_session_reducer.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py -q`
  - 48 passed
- `.venv/bin/python -m pytest -m unit`
  - 394 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `TranscriptFlow` 内部を必要に応じて `OperationPlan` の read / write step に分ける
- lifecycle / candidate flow のどれを次に dispatcher 配下へ移すか、実ブラウザ調整で読みづらい箇所から選ぶ

## 2026-05-28 セッション16

### やること（開始時に書く）
- `SessionEventRunner` を追加し、`core.py` から event queue / drain / process / reduce の runtime 実装を移す
- `core.py -> dispatcher -> event runner -> TransitionResult -> core.py output boundary` の流れに寄せる
- `dispatcher.py` が `SessionEvent` を core private method へ戻さず、runner へ渡すようにする
- コミットは行わない

### やったこと
- `server/session/event_runner.py` に `SessionEventRunner` を追加した
- `core.py` から `_post_event` / `_drain_events` / `_process_event` / `_reduce` / `_reduce_transcript_finalized` を削除した
- `SessionSignalDispatcher` が `SessionEvent` を `SessionEventRunner.post()` へ渡すようにした
- `PlaybackTelemetry` も dispatcher 内で `SessionEvent` に変換し、core private method へ戻らず runner へ渡すようにした
- 既存テストの `session._process_event` / `session._reduce` 直接参照を、runner または public `post_event()` 経由に更新した
- MEMORY.md に event runtime ownership の判断を追記した

### 詰まったこと・解決したこと
- 既存テストが `TomoroSession._process_event` / `_reduce` を直接 monkeypatch / 呼び出ししていた
  - 解決: event runtime の検証対象を `session._event_runner` と `session.post_event()` に移した
- 最初の移動後、`PlaybackTelemetry` が dispatcher から core の `_accept_playback_telemetry_signal()` に戻っていた
  - 解決: dispatcher が telemetry を `SessionEvent` に変換し、そのまま `SessionEventRunner` へ渡す形にした

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_event_runner.py tests/unit/test_session_signals.py tests/unit/test_session_reducer.py tests/unit/test_phase10_session_contract.py -q`
  - 23 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_event_runner.py tests/unit/test_phase105_session_runtime.py tests/unit/test_phase885_session_runtime.py -q`
  - 18 passed
- `.venv/bin/python -m pytest -m unit`
  - 396 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 人間が `core.py` / `dispatcher.py` / `event_runner.py` を読んだ違和感を確認する
- 次に切るなら output boundary または lifecycle/candidate flow のどちらが読みにくさへ効くか判断する

## 2026-05-28 セッション17

### やること（開始時に書く）
- 一枚岩時代の `server/session.py` を git から `old_session.py.txt` として復元し、比較用の標本にする
- 現行 `server/session` 配下の責務を、入力音声系 / 応答音声系 / 入力シグナル系 / 応答シグナル系で考え直す
- 今回は実装構造を戻さず、比較・可視化の材料を作る

### やったこと
- `b254d32^:server/session.py` から一枚岩時代の `server/session.py` を `old_session.py.txt` として復元した
- `old_session.py.txt` は 2342 行で、現行 `server/session/core.py` は 1343 行であることを確認した
- 一枚岩時代と現行 package split の責務比較を始める材料を作った

### 検証
- `git diff --check`
  - pass
- `git status --short`
  - `LOG.md` と `old_session.py.txt` の差分のみ

### 次のセッションでやること
- `old_session.py.txt` と現行 `server/session` 配下を、入力音声系 / 応答音声系 / 入力シグナル系 / 応答シグナル系の4象限でマーキングする
- 応答自発系 / candidate 系をどの象限へ置くか、または別レイヤにするかを判断する

## 2026-05-28 セッション18

### やること（開始時に書く）
- `input -> changer -> state -> watcher -> output -> new input` の閉じたループを `ARCHITECTURE.md` に追記する
- `changer -> state` は demand ではなく info/write であり、`state -> watcher` に output demand が現れる、という用語を固定する
- gateway 由来の入力と session 内の output 結果を同じ input loop に戻す設計意図を明記する

### やったこと
- `ARCHITECTURE.md` の冒頭に `Session closed-loop architecture` を追記した
- `input` / `changer` / `<info>` / `state` / `<demand>` / `watcher` / `output` / `new input` の責務を明文化した
- 現行の `SessionCommand` / `StateEmission` / `SessionOutputSignal` を、この loop 上のどの概念として読むかを追記した

### 検証
- `git diff --check`
  - pass

### 次のセッションでやること
- 現行 `server/session` 配下を closed-loop の `input` / `changer` / `state` / `watcher` / `output` に対応づける
- 次の実装では、まず output demand / watcher の境界から切れるか確認する

## 2026-05-28 セッション19

### やること（開始時に書く）
- PLAN.md に Phase 10.17 Session closed-loop convergence を長時間タスクとして追記する
- Phase 10.17.0〜10.17.2 を実装し、ARCHITECTURE.md の closed-loop 設計へ最初の境界を寄せる
- `SessionCommand` を demand として分類し、`TomoroSessionEffects` を session-local watcher として固定する
- コミットは行わない

### やったこと
- PLAN.md に Phase 10.17 と 10.17.0〜10.17.8 のサブフェーズを追記した
- 10.17.0 として、現行 `server/session` 配下を `input` / `changer` / `state` / `demand` / `watcher` / `output` / `new input` に対応づけた
- 10.17.1 として、`server/session/commands.py` に `SessionCommand` owner 分類を追加した
- 10.17.2 として、`TomoroSessionEffects` が `session_watcher` command だけを実行するようにした
- `record_playback_telemetry` の event-local 実行を `SessionEventRunner` から `TomoroSessionEffects` へ移した
- `tests/unit/test_session_commands.py` を追加し、command 分類と watcher 実行を固定した

### 詰まったこと・解決したこと
- `SessionCommand` には session 内部で実行するものと candidate runner が実行するものが混在していた
  - 解決: まず owner 分類だけを追加し、unknown command は将来の `external_worker` として扱うことにした
- `SessionEventRunner` は playback telemetry command だけを特別扱いしていた
  - 解決: event-local command 実行も watcher 境界へ寄せ、runner は queue / drain / reduce / result wrapping に寄せた
- 最初は runner から session watcher command 全体を実行してしまい、`apply_stop_intent_event()` の stop ack が二重実行された
  - 解決: runner から実行する watcher command は `record_playback_telemetry` のような event-local command に限定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_session_event_runner.py -q`
  - 5 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_session_event_runner.py tests/unit/test_phase105_session_runtime.py -q`
  - 18 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_session_event_runner.py tests/unit/test_session_reducer.py tests/unit/test_session_signals.py tests/unit/test_phase10_session_contract.py -q`
  - 26 passed
- `.venv/bin/python -m pytest -m unit`
  - 399 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- Phase 10.17.3 として、reply lifecycle / TTS finished / candidate result など境界が明確な output result を new input に戻す

## 2026-05-28 セッション20

### やること（開始時に書く）
- `session_watcher` command のうち、Effects が実行済みのもの / 未実装のものを test で明示する
- 未実装 command は silent no-op にせず、warning + no-op として扱う
- `_run_internal_commands()` から移す対象は一度に全部ではなく、command table に沿って 1 種類ずつ増やす

### やったこと
- `server/session/commands.py` に `IMPLEMENTED_SESSION_WATCHER_COMMANDS` と `PENDING_SESSION_WATCHER_COMMANDS` を追加した
- `tests/unit/test_session_commands.py` で実装済み / 未実装 command の一覧を固定した
- `TomoroSessionEffects.run_commands()` が未実装 `session_watcher` command を受けた場合は warning を出して no-op にするようにした

### 詰まったこと・解決したこと
- 未実装 command を silent no-op にすると demand が落ちた時にログから追えない
  - 解決: closed-loop 移行中は warning + no-op にして、実装済み command table をテストで更新しながら 1 種類ずつ移す

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_session_event_runner.py tests/unit/test_phase105_session_runtime.py -q`
  - 20 passed
- `.venv/bin/python -m pytest -m unit`
  - 401 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `PENDING_SESSION_WATCHER_COMMANDS` から 1 種類選び、既存挙動を保ったまま Effects 実装済みに移す

## 2026-05-28 セッション21

### やること（開始時に書く）
- Phase 10.17.3 は lifecycle 境界だけを対象にする
- LLM token delta / TTS audio chunk を全部 SessionEvent 化しない
- `reply_done` / cancel / TTS finished / candidate result のような coarse-grained result だけを new input 候補として整理する
- 既存 `/ws` contract、audio hot path、`reply_text` delta の体感 latency は変えない

### やったこと
- `server/session/lifecycle.py` を追加し、coarse lifecycle result を new input 候補として分類できるようにした
- `reply_done` / `reply_cancelled` / `tts_finished` / candidate result 系だけを lifecycle new input candidate とした
- `reply_text` / `audio_start` / `audio_end` / `audio_control` / `emotion` は hot path event として対象外にした
- 実 runtime の送信順や audio binary path は変更しなかった
- `tests/unit/test_session_lifecycle.py` を追加し、coarse lifecycle と hot path の分離を固定した

### 詰まったこと・解決したこと
- Phase 10.17.3 を実際の runtime event loop へ接続しすぎると、`reply_text` delta や audio chunk の latency に触れやすい
  - 解決: 今回は lifecycle 境界の分類と test に留め、hot path は一切 event 化しない方針を固定した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_lifecycle.py tests/unit/test_session_commands.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_phase10_candidate_command_runner.py -q`
  - 29 passed
- `.venv/bin/python -m pytest -m unit`
  - 413 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- Phase 10.17.4 として、TranscriptFlow の direct output を state update / demand emission に分けられる箇所から整理する

## 2026-05-28 セッション23

### やること（開始時に書く）
- Phase 10.17.3 の続きとして、追加した lifecycle trace が実際の event 経路を通っているか確認する
- `reply_done` / `reply_cancelled` / `tts_finished` / candidate 系 event が `SessionEventRunner` に入った時だけ trace されることを unit test で固定する
- hot path event は trace されないことを unit test で固定する
- new input queue へ戻す実装、OutputDemand / SessionOutputWatcher / DemandQueue などの新抽象追加、hot path 変更はしない

### やったこと
- `tests/unit/test_session_event_runner.py` の lifecycle trace test を、`reply_done` / `reply_cancelled` / `tts_finished` / `initiative_candidate_loaded` / `arrival_candidate_loaded` / `candidate_command_failed` の全候補へ広げた
- hot path 非 trace test を `reply_text` / `audio_start` / `audio_end` / `audio_control` / `emotion` の全候補へ広げた
- 実経路を grep で確認し、candidate result 系は `CandidateCommandRunner` から `accept_signal()` 経由で runner に入ることを確認した
- 一方、通常 `reply_done` は `ReplyOrchestrator` / `start_precomputed_reply` / stop ack から `_send_event()` 直送で、現時点では runner に入らないことを確認した
- PLAN.md に、分類器と trace はあるが通常 `reply_done` 系 output はまだ実経路では runner 未接続であることを記録した

### 詰まったこと・解決したこと
- `reply_done` を runner に通す配線へ変更すると `/ws` contract や reply/audio 順序へ踏み込む
  - 解決: 今回は配線変更せず、runner に入った場合の trace と、現実の未接続箇所の記録だけに留めた

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_lifecycle.py tests/unit/test_session_event_runner.py -q`
  - 25 passed
- `.venv/bin/python -m pytest -m unit`
  - 424 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 通常 `reply_done` を new input 候補として扱うなら、まず client output と lifecycle input の二重化方針を PLAN に切る

## 2026-05-28 セッション24

### やること（開始時に書く）
- Phase 10.17.3b として reply lifecycle event の送信箇所を列挙する
- どれを `SessionEventRunner` に戻すべきか、どれは gateway/client notification のままでよいかを分類する
- 配線変更はまだ最小にし、既存 `/ws` contract / audio hot path / reply hot path は変えない

### やったこと
- `server/session/lifecycle.py` に `REPLY_LIFECYCLE_SEND_POINTS` を追加し、reply lifecycle send point を棚卸しした
- normal reply / precomputed reply / stop ack の `reply_done` は現状 client notification として維持する分類にした
- `reply_cancelled` / `tts_finished` は現状未送信で、将来 runner input 候補として分類した
- `initiative_candidate_loaded` / `arrival_candidate_loaded` / `candidate_command_failed` は既に `SessionEventRunner` input であることを分類した
- `tests/unit/test_session_lifecycle.py` に current route と recommendation のテストを追加した

### 詰まったこと・解決したこと
- `reply_done` を runner に戻すには client notification と lifecycle input を二重化する方針が必要になる
  - 解決: 今回は配線を変えず、send point と recommendation の分類だけに留めた

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_lifecycle.py tests/unit/test_session_event_runner.py -q`
  - 27 passed
- `.venv/bin/python -m pytest -m unit`
  - 426 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `reply_done` の client notification と lifecycle input を二重化するか、別 event 名で lifecycle だけ戻すかを PLAN 上で切ってから実装する

## 2026-05-28 セッション22

### やること（開始時に書く）
- `lifecycle_result_from_event(event)` を event runner か debug trace で呼ぶ
- lifecycle result がある場合だけ trace/log に出す
- まだ input queue には戻さない

### やったこと
- `SessionEventRunner._process()` で `lifecycle_result_from_event(event)` を呼ぶようにした
- lifecycle result がある場合だけ `lifecycle_new_input_candidate` log を出すようにした
- payload は中身ではなく key 一覧だけをログに出し、candidate payload などを大きく出さないようにした
- hot path event は trace 対象外のままにし、input queue へ再投入する処理は追加しなかった
- `tests/unit/test_session_event_runner.py` に lifecycle trace と hot path 非 trace のテストを追加した

### 詰まったこと・解決したこと
- lifecycle result を queue に戻すと、まだ Phase 10.17.3 の範囲を超えて制御順序が変わる
  - 解決: 今回は観測ログだけに限定し、new input 候補として見えるが再投入はしない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_event_runner.py tests/unit/test_session_lifecycle.py tests/unit/test_phase10_candidate_command_runner.py -q`
  - 22 passed
- `.venv/bin/python -m pytest -m unit`
  - 415 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- Phase 10.17.4 として、TranscriptFlow の direct output を state update / demand emission に分けられる箇所から整理する

## 2026-05-28 セッション25

### やること（開始時に書く）
- Phase 10.17.2 continuation として、`session_watcher` command のうち `TomoroSessionEffects.run_commands()` がまだ実行していないものを一覧化する
- 実装移動は `send_audio_control_stop` または `cancel_reply_generation` の低リスクな 1 種類だけに絞る
- 新しい Demand / Watcher / OutputDemand 型は追加しない
- `reply_done` / lifecycle routing / hot path は触らない

### やったこと
- `PLAN.md` に Phase 10.17.2b として pending `session_watcher` command の一覧を追記した
- `cancel_reply_generation` は reply / TTS task の cancellation status と待ち合わせに触るため pending のまま残した
- `send_audio_control_stop` は既存 `_send_reserved_audio_stop()` を呼ぶだけなので、今回の低リスクな 1 種類として Effects 実行済みに移した
- `server/session/commands.py` の implemented / pending table を更新した
- `tests/unit/test_session_commands.py` に `send_audio_control_stop` が既存 `audio_control stop` event を出すことを追加した

### 詰まったこと・解決したこと
- `send_audio_control_stop` は client 向け audio control event を出すが、新しい event type や hot path の配線変更は不要だった
  - 解決: `TomoroSessionEffects` では既存 helper への委譲だけにし、payload 形式は変えない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py -q`
  - 6 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_session_event_runner.py tests/unit/test_phase885_session_runtime.py -q`
  - 22 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 427 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- `cancel_reply_generation` を Effects に移す場合は、reply task / TTS worker / `reply_cancel_status` の待ち合わせを先に unit test で固定してから 1 種類だけ移す

## 2026-05-28 セッション26

### やること（開始時に書く）
- Phase 10.17.2 continuation として、未実行 `session_watcher` command から `write_ambient_observer` を優先確認する
- 実装前に、現在の実行箇所、DB / ambient log write が hot path を止めているか、失敗時の扱いが変わらないかを確認する
- 実装移動は 1 command だけにし、既存挙動を変えず `TomoroSessionEffects` への委譲に留める
- result input 化、新しい Demand / Watcher / OutputDemand 型追加、`cancel_reply_generation` / `reply_done` / lifecycle routing / hot path 変更はしない

### やったこと
- `write_ambient_observer` は `SessionEventRunner` の `transcript_finalized` reduce で playback echo / continue speaking の observer command として発生していることを確認した
- ambient log write は `TranscriptFlow` / turn-taking observer の STT 確定後 path で await されており、audio chunk hot path ではないことを確認した
- 既存 direct write は例外を握りつぶしていないため、Effects 側も catch しない方針にした
- `write_ambient_observer` だけを `IMPLEMENTED_SESSION_WATCHER_COMMANDS` へ移し、`TomoroSessionEffects` から既存 writer / transcript final notification に委譲した
- `run_event_local_commands()` でも `write_ambient_observer` を実行し、result input 化や routing 変更は行わなかった

### 詰まったこと・解決したこと
- `write_ambient_observer` は command としては存在していたが、Effects 未実装のため event runner 経路では warning no-op になっていた
  - 解決: 新しい型や routing を足さず、既存の observer write と同じ処理だけを Effects に追加した

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_phase885_session_runtime.py -q`
  - 11 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_commands.py tests/unit/test_session_event_runner.py tests/unit/test_phase885_session_runtime.py -q`
  - 24 passed
- `.venv/bin/python -m pytest -m unit -q`
  - 429 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 残る pending command は `cancel_reply_generation` / `save_tomoko_turn` / `start_reply_generation`。次も 1 command だけ、失敗時の既存挙動を先に固定してから移す

## 2026-05-29 セッション2

### やること（開始時に書く）
- Phase 10.20.0 として、`experiment/restore-session-monolith-960be36` の一枚 `server/session.py` baseline から cautious split restart 方針を docs-only で固定する
- 最初に切り出しても安全そうな候補を、state container / input_router 相当 / pure helper の中から 1 つだけ選ぶ
- 今回は runtime code を原則変更せず、必要なら characterization test だけを検討する
- 未来の package split / dispatcher / effects / event_runner / maps package 方式は再実装しない
- hot path、ReplyOrchestrator 相当の LLM/TTS ordering、TTS flush / audio chunk / playback timing、`reply_text` / `reply_done` routing、cancel / TTS finished new input 化、OutputDemand / Watcher、DB write SessionCommand 化、ambient_log_write 非同期化は触らない

### やったこと
- AGENTS.md 指示どおり MEMORY.md / LOG.md / PLAN.md / README.md / ARCHITECTURE.md / `_reference/` を確認した
- `server/session.py` が 2342 行の一枚構成で、`server/session/state.py` は存在しないことを確認した
- `TomoroSession.__init__` に runtime state field が残っており、`get_now_state()` と transition helpers が state snapshot / mutation の中心であることを確認した
- 最初に切り出す候補は `state container` 1 つに絞った
- `input_router` 相当の入口整理は、audio hot path / reply lifecycle / candidate result / client lifecycle にまたがり、dispatcher / event_runner 的な再分割に戻りやすいため今回は保留した
- `pure helper / value object` は、closed-loop の主要責務境界を固定する効果が薄く、carryover / context 周辺へ広がりやすいため今回は保留した
- PLAN.md に Phase 10.20.0 の split restart 方針を追記した
- MEMORY.md に、split 再開時は closed-loop 用語に合わせて 1 責務ずつ進める判断を追記した
- runtime code、test code、audio hot path、reply / TTS ordering、DB write ordering、lifecycle routing は変更していない

### 検証
- `git diff --check`
  - pass

### 次のセッションでやること
- state container を実際に切る場合は、まず characterization test で `get_now_state()` と state ownership を固定する
- 実装に進む場合も `server/session/state.py` への pure extraction だけに限定し、`TomoroSession` final owner と runtime behavior を維持する

## 2026-05-29 セッション3

### やること（開始時に書く）
- Phase 10.20.1 として、monolithic `server/session.py` の `TomoroSession.__init__` 内 runtime state fields を棚卸しする
- state container に移せる field と、まだ core に残すべき field を分類する
- 次に実装する場合の対象を 1 グループだけに絞る
- 今回は `state.py` 作成、field 移動、property/proxy 追加、import path 変更、runtime behavior change を行わない
- audio hot path、reply task / TTS queue、candidate gate、DB write ordering、OutputDemand / Watcher / dispatcher / effects / maps は触らない

### やったこと
- AGENTS.md 指示どおり MEMORY.md / LOG.md / PLAN.md / README.md / ARCHITECTURE.md / `_reference/` を確認した
- `TomoroSession.__init__` の field を、dependency / injected collaborator、hot path adjacent state、pure runtime state、task / queue lifecycle state、latency probe state、candidate request state、conversation session state、memory carryover state、precomputed reply context state、turn-taking transient state に分類した
- state container に入れる次の実装候補は `latency probe state` 1 グループだけに絞った
- `state` / `attention_mode` / `audio_turns` は VAD hot path / attention lifecycle / playback telemetry / candidate final gate に近いため、初回抽出では core に残す判断にした
- reply task / TTS queue、candidate request、conversation session、memory carryover、precomputed reply context、turn-taking transient state は、それぞれ ordering / gate / DB write / memory quality / stop-restart 体感に関わるため core に残す判断にした
- PLAN.md に Phase 10.20.1 の field classification と次候補を追記した
- MEMORY.md に、初回候補は `latency probe state` に限定する判断を追記した
- runtime code、test code、import path、audio hot path、reply task / TTS queue、candidate gate、DB write ordering は変更していない

### 検証
- `git diff --check`
  - pass

### 次のセッションでやること
- 実装へ進む場合は、まず latency probe の characterization test で reset / mark / elapsed semantics を固定する
- その後も `state.py` 作成と field 移動は `latency probe state` だけに限定し、authoritative state や hot path adjacent state は動かさない

## 2026-05-29 セッション4

### やること（開始時に書く）
- Phase 10.20.2 として、latency probe state の reset / mark / elapsed / logging-adjacent output start / defer wait semantics を characterization test で固定する
- 今回は `state.py` 作成、field 移動、property/proxy 追加、import path 変更、runtime behavior change を行わない
- `_reply_task` / `_tts_worker_task` / `_tts_queue`、audio hot path、ReplyOrchestrator 相当の LLM-TTS ordering、DB write ordering、OutputDemand / Watcher / dispatcher / effects / maps は触らない

### やったこと
- `tests/unit/test_session_latency_probe_characterization.py` を追加し、latency probe state の現状挙動を characterization test で固定した
- `_reset_latency_probe()` が 5 つの latency timestamp と `_reply_output_started` を reset し、`_reply_output_defer_until` は reset しない現状仕様を固定した
- elapsed helper は `None` なら `0.0`、mark 済みなら `time.perf_counter()` 差分 ms を返すことを固定した
- `reply_text` output path が `_latency_reply_start_at` / `_latency_first_reply_text_at` / `_reply_output_started` を mark することを固定した
- TTS chunk / audio output path が `_latency_tts_start_at` / `_latency_first_audio_chunk_at` / `_reply_output_started` を mark することを固定した
- `_defer_reply_output()` が遅い deadline を保持し、`_maybe_wait_reply_output_defer()` が最大 250ms 待って 1 回で clear することを固定した
- PLAN.md に Phase 10.20.2 の characterization 方針と次に抽出する場合の対象 field を追記した
- MEMORY.md に、latency probe は characterization を先に固定し、次も 1 グループだけに限定する判断を追記した
- runtime code、`state.py`、field move、property/proxy、import path、audio hot path、reply task / TTS queue、DB write ordering は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_latency_probe_characterization.py -q`
  - 7 passed
- `.venv/bin/python -m pytest -m unit`
  - 384 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- 実装へ進む場合も、抽出対象は latency probe state だけに限定する
- `state.py` 作成や field move に進む前に、今回固定した reset / mark / elapsed / defer semantics を維持する
- `_reply_task` / `_tts_worker_task` / `_tts_queue`、audio hot path、reply / TTS ordering、DB write ordering、candidate gate、conversation session lifecycle は引き続き core に残す

## 2026-05-29 セッション5

### やること（開始時に書く）
- Phase 10.20.3 として、Phase 10.20.2 で固定した latency probe state だけを `LatencyProbeState` に抽出する
- `server/session.py` と衝突する `server/session/` package は作らず、小さい module に限定する
- 既存 `_reset_latency_probe()` / elapsed helper 名は残し、内部で `LatencyProbeState` に委譲する
- `_reply_output_defer_until` は reset で消さない挙動を維持する
- runtime のログ文言、latency 計算、reply output timing、reply task / TTS queue ownership、audio hot path、DB write ordering は変更しない

### やったこと
- `server/session_latency.py` を追加し、`LatencyProbeState` に latency probe 専用 state を抽出した
- `LatencyProbeState` に reset / mark / elapsed / defer merge / defer wait delay 消費を移した
- `TomoroSession._reset_latency_probe()` と `_elapsed_since_*_ms()` は残し、`LatencyProbeState` への委譲にした
- `reply_start` / `first_reply_text` / `tts_start` / `first_audio_chunk` の mark 位置と latency log 文言は維持した
- `_reply_output_defer_until` 相当の `reply_output_defer_until` は、Phase 10.20.2 の characterization どおり reset で消さない挙動を維持した
- characterization test を `LatencyProbeState` 抽出後の形へ更新し、専用 state object の mark/reset 挙動も固定した
- PLAN.md / MEMORY.md に Phase 10.20.3 の専用抽出方針を追記した
- `server/session/` package、汎用 `state.py`、dispatcher / effects / event_runner / maps、OutputDemand / Watcher は作っていない
- authoritative state、`audio_turns`、reply task / TTS queue、LLM/TTS ordering、`reply_done` / cancel / TTS finished routing、DB write ordering、audio hot path は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_latency_probe_characterization.py -q`
  - 8 passed
- `.venv/bin/python -m pytest -m unit`
  - 385 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- latency probe 以外の state はまだ抽出しない
- 次に進む場合も 1 phase 1 responsibility とし、authoritative state / reply lifecycle / TTS queue / audio hot path / DB write ordering は dedicated phase なしに触らない

## 2026-05-29 セッション6

### やること（開始時に書く）
- Phase 10.20.4 として、Phase 10.20.3 の `LatencyProbeState` 抽出を runtime verification 済みの安全地点として記録する
- 次に抽出してよい候補を 1 つだけ選ぶ
- 今回は docs-only とし、runtime behavior、field move、import path restructuring、generic state container、`server/session/` package 作成は行わない
- hot path、reply orchestration、reply task / TTS queue、DB write ordering、candidate gate、conversation session lifecycle、dispatcher / effects / event_runner / maps、OutputDemand / Watcher は触らない

### Phase 10.20.3 runtime verification
- 人間側の実ブラウザ確認により、`LatencyProbeState` 抽出後も通常会話が通ったことを確認した
- wake word から conversation session start へ進み、`reply_text`、TTS audio、playback telemetry、follow-up、memory recall が動作していることを確認した
- latency log は `reply_start` / `first_reply_text` / `tts_start` / `first_audio_chunk` として出ており、Phase 10.20.3 の抽出後も計測点が維持されている
- TTS audio と playback telemetry が流れているため、audio output path と client playback feedback は壊れていない
- runtime error / Traceback / 未実装 warning は見当たらない
- 空 transcript / `too_short` / `low_audio_short_text` drop は transcript filter の正常系として扱う

### 次候補の選定
- 次に抽出してよい候補は `retrieved context carryover state` 1 つに絞る
- 対象候補は `_RetrievedContextCarryoverEntry`、`_retrieved_context_carryover`、`_retrieved_context_carryover_seq` と、その read / remember / evict / clear helper 群である
- 理由: authoritative state ではなく、audio hot path、reply task / TTS queue、LLM-TTS ordering、DB write ordering、candidate gate、conversation session lifecycle に直接触れない
- 既存挙動は carryover merge、dedup key、entry count / text budget eviction、session close clear、既存 log 文言を characterization test で囲いやすい
- ただし memory quality に関わるため、実装する場合は Phase 10.20.5 以降で先に characterization test を置き、今回は実装しない

### 検証
- `git diff --check`
  - pass

### 次のセッションでやること
- `retrieved context carryover state` を実装候補にする場合も、まず characterization test で merge / dedup / eviction / clear semantics を固定する
- implementation は small object への pure extraction に限定し、DB read / ContextSnapshotBuilder / reply orchestration / session lifecycle は動かさない

## 2026-05-29 セッション7

### やること（開始時に書く）
- Phase 10.20.5 として、`retrieved context carryover` だけを小さい dedicated module に抽出する
- 既存の key 生成、merge 順序、dedup、entry count / text budget eviction、clear の挙動を変えない
- `TomoroSession` 側の既存 method 名は残し、中で carryover object に委譲する
- memory retrieval policy、ContextSnapshotBuilder、prompt、DB query、reply orchestration、audio hot path は変更しない
- commit は行わず、人間の実ブラウザ memory recall / carryover 確認待ちにする

### やったこと
- `server/session_carryover.py` を追加し、`RetrievedContextCarryoverState` に carryover 専用 state/helper を抽出した
- `_RetrievedContextCarryoverEntry` 相当の entry、key 生成、merge / remember / evict / clear を dedicated module に移した
- `TomoroSession._merge_carried_long_term_memory()` / `_carried_long_term_memory()` / `_remember_retrieved_context()` / `_evict_retrieved_context_carryover()` / `_evict_one_carryover()` / `_clear_retrieved_context_carryover()` の method 名は残し、内部で carryover object に委譲した
- `carryover_used` / `carryover_added` / `carryover_evicted` / `carryover_cleared` の log 文言は `TomoroSession` 側に残した
- `tests/unit/test_session_carryover.py` を追加し、source_id 優先 key、normalized text key、fresh-first merge / dedup、entry count eviction、text budget eviction、clear count を固定した
- memory retrieval policy、ContextSnapshotBuilder、prompt format、DB query、context quota / weight、reply orchestration、audio hot path、DB write ordering、candidate gate、conversation session lifecycle は変更していない
- OutputDemand / Watcher / dispatcher / effects / event_runner / maps、汎用 `state.py` は作っていない
- commit は行っていない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_carryover.py -q`
  - 6 passed
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase8_memory.py tests/unit/test_phase88_context_snapshot.py -q`
  - 25 passed
- `.venv/bin/python -m ruff check .`
  - pass
- `.venv/bin/python -m pytest -m unit`
  - 391 passed, 17 deselected
- `git diff --check`
  - pass

### 人間の実ブラウザ確認待ち
- 「智子、〇〇のこと覚えてる？」
- 「もっと詳しく」
- 同一会話内の follow-up
- memory recall / carryover の log
- 返答が極端に悪化していないこと

### 人間の実ブラウザ確認結果
- 2026-05-29 00:44〜00:47 の `make server-debug` 実行で、`RetrievedContextCarryoverState` 抽出後も通常会話が通った
- wake word から conversation session start へ進み、同一会話内 follow-up、`reply_text`、TTS audio、playback telemetry が問題なさそうなことを確認した
- latency log は `reply_start` / `first_reply_text` / `tts_start` / `first_audio_chunk` として出ており、Phase 10.20.5 後も計測点は維持されている
- 「話は変わるけど著作権の事って覚えてる」で deep memory recall が走り、`memory_hits` / `session_summaries` / `restored_turn_snippets` が採用された
- carryover は `carryover_added added=9 total=6 evicted=3`、後続 turn で `carryover_used count=6 fresh_count=0 merged_count=6`、UI stop で `carryover_cleared reason=ui_stop count=6` が出ており、remember / merge / clear が動いている
- runtime error / Traceback / 未実装 warning は見当たらない
- 空 transcript drop と `low_audio_ascii_text` drop は transcript filter の正常系として扱う
- 「仕事が忙しかった」へのクイズは、具体的な仕事内容が会話内で与えられていなかったため答えられない挙動であり、carryover 抽出の regression とは扱わない

## 2026-05-29 セッション9

### やること（開始時に書く）
- Phase 10.20.7 として、TomoroSession 周辺に残っている small value object / key generation / JSON payload helper 候補を read-only で棚卸しする
- すでに抽出済みの `server/session_payloads.py` と `server/session_carryover.py` の範囲を確認する
- `server/session.py` に残る small helper 候補を危険度つきで整理し、次に抽出してよい候補を 1 個だけ PLAN.md に追記する
- runtime code、test code、audio hot path、reply orchestration、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、dispatcher / effects / event_runner / maps package は変更しない

### やったこと
- AGENTS.md 指示と今回指定に従い、AGENTS.md / MEMORY.md / LOG.md / PLAN.md / ARCHITECTURE.md / README.md / `_reference/` を確認した
- `server/session_payloads.py` の抽出済み helper が pure payload / JSON coercion / playback telemetry coercion に限定されていることを確認した
- `server/session_carryover.py` の抽出済み helper が retrieved context carryover 専用 state / key generation / merge / remember / evict / clear に限定されていることを確認した
- `server/session.py` に残る `_session_summary_hit_to_memory()`、`_retrieved_context_key()`、`_candidate_policy_payload()`、`_candidate_reply_gate_payload()`、`_new_candidate_request_id()`、`_is_stale_candidate_result()`、`_start_reason_from_participation_mode()`、`_withdraw_decision()`、`_accepts_keyword()`、`_elapsed_ms()`、`_pending_reply_state()`、`_recent_turns_with_precomputed_topic()` を候補として分類した
- PLAN.md に Phase 10.20.7 の候補表、危険度、選ぶ / 選ばない理由、characterization test 候補を append-only で追記した
- 次に実装してよい候補は `_session_summary_hit_to_memory()` 1 個だけに絞った
- runtime code、test code、import path、audio hot path、reply orchestration、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate は変更していない

### 検証
- `git diff --check`
  - pass

## 2026-05-29 セッション10 最終追記

### やったこと
- Phase 10.20.7a として `_session_summary_hit_to_memory()` だけを `server/session_memory_helpers.py` の `session_summary_hit_to_memory()` へ抽出した
- `tests/unit/test_session_memory_helpers.py` で `SessionSummaryHit -> MemoryHit` の speaker / text prefix / timestamp fallback / similarity / emotion / source_id を固定した
- `server/session.py` は `session_summary_hit_to_memory` import と `_reply_to()` 内の呼び出し置換、private helper 削除に限定した
- runtime behavior、audio hot path、reply routing、LLM/TTS ordering、DB write ordering、conversation session lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、OutputDemand / Watcher、`server/session/` package split は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出前）
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_memory_helpers.py -q`
  - 2 passed（抽出後）
- `.venv/bin/python -m pytest -m unit`
  - 397 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

## 2026-05-29 セッション11

### やること（開始時に書く）
- Phase 10.20.8 として、`server/session.py` に残る key generation 系 helper / inline expression を read-only で棚卸しする
- runtime behavior / ordering / lifecycle / stale 判定に影響しない候補を 1 個だけ選び、characterization test 追加後に narrow module へ抽出する
- `server/session.py` 側は import と呼び出し置換に近い最小差分に限定する
- PLAN.md / LOG.md / MEMORY.md へ append-only で判断と結果を記録する
- audio hot path、playback telemetry ordering、reply routing、LLM/TTS ordering、DB ordering、conversation lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、stale result discard policy、OutputDemand / Watcher、dispatcher / effects / event_runner / maps、`server/session/` package split、汎用 `state.py` は変更しない

### やったこと
- `server/session.py` の key / id / uuid / candidate / session 関連を read-only で棚卸しした
- PLAN.md に Phase 10.20.8 の候補表と危険度分類を append-only で追記した
- `_new_candidate_request_id()` 全体は stale result discard の active id 更新を含むため抽出せず、内部の `f"{kind}-{self._candidate_request_sequence}"` だけを pure formatter 候補として選んだ
- 抽出前に `tests/unit/test_phase105_session_runtime.py` へ characterization test を追加し、既存 session path の `initiative-1` / `initiative-2` / `arrival-1` 形式を固定した
- `server/session_key_helpers.py` を追加し、`candidate_request_id(kind, sequence)` 1 個だけを抽出した
- `server/session.py` は `candidate_request_id` import と `_new_candidate_request_id()` 内の呼び出し置換だけに限定した
- `tests/unit/test_session_key_helpers.py` を追加し、pure helper の `initiative-1` / `arrival-2` 形式を固定した
- runtime behavior、audio hot path、playback telemetry ordering、reply routing、LLM/TTS ordering、DB ordering、conversation lifecycle、memory retrieval policy、ContextSnapshotBuilder、prompt format、candidate gate、stale result discard policy、OutputDemand / Watcher、`server/session/` package split は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase105_session_runtime.py -q`
  - 14 passed（抽出前 characterization）
- `.venv/bin/python -m pytest -m unit tests/unit/test_session_key_helpers.py tests/unit/test_phase105_session_runtime.py -q`
  - 15 passed
- `.venv/bin/python -m pytest -m unit`
  - 399 passed, 17 deselected
- `.venv/bin/python -m ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- TomoroSession 周辺の次 phase が指定されるまで、candidate stale 判定、conversation lifecycle、memory retrieval policy、prompt format、audio / playback ordering には踏み込まない

## 2026-05-29 セッション12

### やること（開始時に書く）
- Phase 10.20.9 として、`server/session.py` に残っている small helper / value object / formatter / coercion / mapping 的な候補を read-only で棚卸しする
- Phase 10.20.6 / 10.20.7a / 10.20.8 で抽出済みの `server/session_payloads.py` / `server/session_memory_helpers.py` / `server/session_key_helpers.py` の範囲と重複しないよう確認する
- 次に実装してよい候補を 0 個または 1 個だけ PLAN.md に記録し、今日は実装しない
- runtime code、test code、import、`server/session.py` の整形、`server/session/` package split、dispatcher / effects / event_runner / maps、OutputDemand / Watcher、汎用 helpers / utils / state は変更しない
- PLAN.md / LOG.md / MEMORY.md に append-only で判断を記録し、docs-only として `git diff --check` だけを実行する

### やったこと
- AGENTS.md / MEMORY.md / LOG.md / PLAN.md / ARCHITECTURE.md / README.md / `_reference/` を確認した
- `server/session_payloads.py` / `server/session_memory_helpers.py` / `server/session_key_helpers.py` / `server/session_carryover.py` の抽出済み範囲を確認した
- `rg "def _" server/session.py`、payload / metadata / label / key / id / formatter / coercion 系検索、inline dict / f-string 検索で `server/session.py` の残候補を read-only で棚卸しした
- PLAN.md に Phase 10.20.9 の候補表、危険度、抽出可否、next-extractable-candidate を append-only で追記した
- MEMORY.md に、今回の checkpoint では次の helper extraction 候補を選ばない判断を append-only で追記した
- low-risk 候補は `_elapsed_ms()` / `_retrieved_context_key()` の wrapper cleanup に留まり、新しい extraction としては選ばない判断にした
- `_candidate_policy_payload()` / `_accepts_keyword()` / `_start_reason_from_participation_mode()` は pure に近いが、それぞれ candidate policy、DB writer compatibility、conversation lifecycle に近いため次回候補にしない判断にした
- runtime code、test code、import、`server/session.py` の整形、helper 抽出は行っていない

### 結論
- next-extractable-candidate は 0 個
- 次に進む場合も、別 Phase で候補を 1 つに絞り、characterization test から始める
- candidate gate、stale result discard、reply lifecycle、turn-taking、playback timing、memory retrieval policy、ContextSnapshotBuilder、prompt format、DB write ordering には踏み込まない

### 検証
- `git diff --check`
  - pass
## 2026-05-29 セッション14

### やること（開始時に書く）
- Phase 10.20.10 として、現行 UI と STT 結果ログの 2 ペイン表示をクライアントだけで実装する
- `TomoroSession`、`server/session.py`、WebSocket payload contract、DB write、参加判断、会話セッション lifecycle、TTS / playback ordering は変更しない
- 既存 `/ws` の `transcript_final` event を UI 側で右ペインに表示するだけに限定する
- PLAN.md に append-only で Phase を追記し、HTML / CSS / JS の表示差分を小さく入れる

### やったこと
- PLAN.md に Phase 10.20.10 を append-only で追記した
- `client/index.html` を、左の現行 UI と右の STT log section の 2 ペイン構成に変更した
- `client/styles.css` で desktop は 2 column、狭い viewport は 1 column にし、STT log を scrollable にした
- `client/main.js` は既存 `transcript_final` handling を維持し、表示件数を 80 件に増やし、受信時刻を meta に足した
- MEMORY.md に、今回の 2 ペイン STT log UI は client-only に限定する判断を追記した
- `TomoroSession`、`server/session.py`、WebSocket payload shape、参加判断、会話セッション lifecycle、DB write ordering、TTS / playback ordering は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase3_stt.py -q`
  - 6 passed
- `git diff --check`
  - pass
- static server `localhost:8765/client/index.html` を in-app browser で確認
  - desktop で左 440px / 右 560px の 2 ペイン表示
  - `127.0.0.1:8765` は browser 側で `ERR_BLOCKED_BY_CLIENT` になったため `localhost:8765` で確認した

## 2026-05-29 セッション15

### やること（開始時に書く）
- Phase 10.20.11 として、右ペインに Tomoko の `reply_text` も会話ログとして表示する
- 実際の `TTSInput.text` は現行 WebSocket payload に含まれないため、ブラウザに届いている `reply_text` delta を集約して表示する
- `TomoroSession`、`server/session.py`、WebSocket payload contract、reply orchestration、TTS ordering、audio hot path は変更しない
- PLAN.md に append-only で Phase を追記し、HTML / CSS / JS の client-only 差分に限定する

### やったこと
- PLAN.md に Phase 10.20.11 を append-only で追記した
- 右ペインの見出しを `Log` / `transcript_final / reply_text` に変更した
- `client/main.js` で `reply_text` delta を Tomoko log entry として右ペインへ集約表示するようにした
- streaming delta は 1 entry に追記し、`reply_done` または次の `participation` で entry を閉じる
- `client/styles.css` に `data-mode="tomoko"` の border color を追加した
- MEMORY.md に、Tomoko 返答ログは client-only の `reply_text` 集約に限定する判断を追記した
- `TomoroSession`、`server/session.py`、WebSocket payload shape、reply orchestration、TTS ordering、audio hot path は変更していない

### 検証
- `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py tests/unit/test_phase5_tts.py -q`
  - 16 passed
- `git diff --check`
  - pass
- `git diff --name-only -- server/session.py server/shared/inference/tts/voicevox.py client/main.js client/index.html client/styles.css`
  - `client/index.html` / `client/main.js` / `client/styles.css` のみ
- static server `localhost:8765/client/index.html` を in-app browser で確認
  - desktop で右ペインの見出しが `Log` / `transcript_final / reply_text` になっていることを確認した

## 2026-05-29 セッション20

### やること（開始時に書く）
- short memory extraction を heuristic-only から、任意の LLM structured output lane + heuristic fallback に拡張する
- memory extraction 用 backend は `InferenceRouter` 経由で選択し、会話 hot path は待たせない
- LM Studio の別モデル並列実験に向けて、extraction backend / decision / elapsed / fallback / prompt note count のログを追加する
- DB 永続化、ContextSnapshotBuilder の副作用、audio hot path、reply routing、DB ordering、conversation lifecycle、TTS / playback timing は変更しない

### やったこと
- `InferenceSection` / `InferenceRouter` に `memory_extraction_backend` / `memory_extraction_fallback` と `memory_extraction` role を追加した
- `config/central_realtime.toml` で short memory extraction 用 backend を `lmstudio_gemma4_e2b` に設定した
- `ShortMemoryProposalResult` に `decision` / `reason` / `raw_text` / `source` を追加した
- `server/session_short_memory_llm.py` を追加し、LM Studio structured output で store / skip と proposal を返す extraction lane を実装した
- 明らかなノイズ発話は heuristic prefilter で LLM に投げず skip するようにした
- LLM extraction 失敗時は heuristic fallback に戻すようにした
- LM Studio backend の queue key を URL + model にし、会話 26B と memory E2B を別 lane として観測できるようにした
- short memory extraction のログと UI event に backend / source / decision / reason / proposals / elapsed_ms を載せた
- client monitor の short memory status に backend / source / decision / elapsed を表示するようにした
- `_docs/latency.md` に今回の regression と未測定の live metric を追記した

### 変更していないもの
- DB 永続化 / migration
- PostgreSQL への short memory note 保存
- ContextSnapshotBuilder の副作用
- audio hot path
- reply routing
- DB ordering
- conversation lifecycle
- TTS / audio chunk / playback timing
- `server/session/` package split
- OutputDemand / Watcher

## 2026-05-29 セッション23

### やること（開始時に書く）
- 口頭タスク更新シナリオを作り、音声なし TomoroSession simulation で short memory の反応変化を見る
- シナリオ: 初期タスク 3 件、2 件完了、1 件追加、最後に残タスク確認
- short memory 有効 / 無効を比較し、残タスク回答が改善するか確認する

### やったこと
- 初回 simulation では、初期タスクリストは memory 化されたが、`ログ確認は終わった` と `ブラウザ確認を追加して` が prefilter で落ち、5 turn 目で初期リストも expire した
- short memory cue に `タスク` / `終わった` / `完了` / `追加` を追加した
- short memory TTL default を 4 turn から 5 turn に変更した
- `SHORT WORKING MEMORY` prompt に、task list / completed / added notes から残タスクを推論するよう追記した
- 再 simulation では、short memory に初期リスト、完了2件、追加1件がすべて入った

### 実測メモ
- short memory 無効:
  - 最終回答: `タスクのリストはまだ何も登録されていないみたい。`
  - expected remaining の `テスト実行` / `ブラウザ確認` は 0/2
- short memory 有効:
  - 最終回答: `残っているのは、ブラウザ確認だけだよ。`
  - `ブラウザ確認` は出たが、`テスト実行` を落とした
  - 途中 turn では `残るはテスト実行だけかな？` と言えていたため、複数 working_context note から ledger を安定再構成するには prompt hint だけでは弱い

### 分かったこと
- short memory は、baseline と比べて「タスク一覧がある」ことと「追加タスクがある」ことは会話に効かせられる
- ただし、完了/追加/残りを正確に保つ task ledger としては不十分
- 残タスク精度を上げるには、LLM に自然文 note を複数渡すだけでなく、task-specific structured note か deterministic reducer が必要そう
- long-term memory / persona snapshot 昇格
- embedding retrieval / dedupe / tombstone / task scheduling

### 検証
- targeted test: `.venv/bin/python -m pytest -m unit tests/unit/test_short_memory.py tests/unit/test_router.py tests/unit/test_lm_studio_backend.py tests/unit/test_phase0_config.py -q`
  - 31 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 421 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- JS / diff: `node --check client/main.js && git diff --check`
  - pass
- browser check:
  - `http://localhost:8767/client/index.html` で Monitor panel / Short memory status が表示されることを確認

### 次のセッションでやること
- LM Studio で `gemma-4-26b-a4b-it-mlx` と `gemma-4-e2b-it-mlx` を同時 load し、`make server-debug` で memory_extraction の elapsed_ms と conversation first_reply_text / first_audio への影響を実測する
- LLM extraction prompt が STT 誤認識をどこまで正規化できるか、人間の会話シナリオで確認する
- prompt に載る short memory が会話ログと重複しすぎる場合は、store する note の抽象度と skip 条件を調整する

## 2026-05-29 セッション24

### やること（開始時に書く）
- short memory extraction backend を `gemma-4-31b-it-mlx` に切り替える
- 口頭タスク更新シナリオで確認した品質改善を実ブラウザ体感テストへ進められるようにする

### やったこと
- `config/central_realtime.toml` に `lmstudio_gemma4_31b` backend を追加した
- `memory_extraction_backend` を `lmstudio_gemma4_e4b` から `lmstudio_gemma4_31b` に変更した
- E4B backend は比較・fallback 候補として設定に残した
- config unit test を 31B backend 前提に更新した

### 実測メモ
- 口頭タスク更新シナリオでは、E4B extraction は最終回答で `テスト実行` を落とした
- 31B extraction は 3 回連続で `テスト実行` と `ブラウザ確認` の両方を残タスクとして回答できた
- 31B extraction は warm 後でも 1.8〜3.2 秒程度かかるため、短い連続発話では次ターンに間に合わない可能性がある

### 変更していないもの
- DB 永続化 / migration
- PostgreSQL への short memory note 保存
- ContextSnapshotBuilder の副作用
- audio hot path
- reply routing
- DB ordering
- conversation lifecycle
- TTS / audio chunk / playback timing
- `server/session/` package split
- OutputDemand / Watcher

### 検証
- targeted test: `.venv/bin/python -m pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_router.py tests/unit/test_short_memory.py -q`
  - 31 passed
- full unit: `.venv/bin/python -m pytest -m unit`
  - 428 passed, 17 deselected
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- git diff --check: `git diff --check`
  - pass

## 2026-05-29 セッション21

### やること（開始時に書く）
- 実サーバー確認ログから、会話 prompt に short memory がどう積まれているかを確認する
- `remember_items` schema 化後も raw 発話が prompt を汚していないか確認する
- 問題が局所的なら、prompt formatter と LLM extraction fallback だけを修正する

### やったこと
- `logs/server-debug.log` で `123って言う数字を覚えてください` のターンを確認した
- `SHORT WORKING MEMORY` が次ターン以降の conversation system prompt に入っていることを確認した
- 一方で、memory extraction LLM が JSON parse 失敗し、heuristic fallback が raw 発話を `verbatim` note として保存していることを確認した
- 会話 prompt 側が `server/gateway/thinking/short_memory_prompt.py` の formatter を使っており、`server/session_short_memory.py` 側の `Remember verbatim:` 表示が反映されていないことを確認した
- gateway 側 formatter も dedupe と `verbatim` 表示に対応させた
- LLM extraction 失敗時は heuristic fallback で raw 発話を保存せず、`decision=skip` にするよう変更した

### 変更していないもの
- DB 永続化 / migration
- PostgreSQL への short memory note 保存
- ContextSnapshotBuilder の副作用
- audio hot path
- reply routing
- DB ordering
- conversation lifecycle
- TTS / audio chunk / playback timing
- `server/session/` package split
- OutputDemand / Watcher

### 検証
- targeted test: `.venv/bin/python -m pytest -m unit tests/unit/test_short_memory.py tests/unit/test_phase4_thinking.py::test_think_fast_logs_llm_prompt_payload -q`
  - 16 passed

## 2026-05-29 セッション22

### やること（開始時に書く）
- short memory extraction backend を `lmstudio_gemma4_e4b` に上げる
- structured output は維持しつつ、schema は `remember_items[].text` / `mode` だけに簡略化する
- `confidence` / `expires_after_turns` は LLM に出させず、サーバー側で補完する
- extraction prompt から Tomoko reply を外し、reply 由来の誤 store を避ける
- TomoroSession を使った音声なし複数ターン simulation で、short memory の効果を定量確認する

### やったこと
- `config/central_realtime.toml` の `memory_extraction_backend` を `lmstudio_gemma4_e4b` に変更した
- `server/session_short_memory_llm.py` の structured output schema を `text` / `mode` のみにした
- LLM 由来 note の `confidence` は 0.85、TTL は buffer default にサーバー側で補完するようにした
- extraction user prompt から Tomoko reply を外し、latest user transcript だけを渡すようにした
- recall / answer request / hearing check は deterministic guard で LLM に渡さず skip するようにした
- `SHORT WORKING MEMORY` prompt に、`Remember verbatim` note はユーザーが聞いたら正確に再現するよう明示した
- TomoroSession + 実 LM Studio backend で、short memory 有効 / 無効の音声なし 2-turn simulation を実行した

### 実測メモ
- E4B extraction live probe:
  - `ABCを覚えてください`: store `ABC`, elapsed 731.9ms
  - `123って言う数字を覚えてください`: store `123`, elapsed 658.3ms
  - `DB永続化はまだしないで短期メモリだけで試したい`: store working_context, elapsed 770.8ms
  - recall question / hearing check: deterministic guard で skip, elapsed 0.1ms
- TomoroSession simulation:
  - `123` recall: with short memory score 1 / without score 0
  - working context recall: with short memory score 2 / without score 0
  - `ABC` recall: with short memory score 0 / without score 0。short memory note は prompt に入ったが、26B reply が空文字になった

### 変更していないもの
- DB 永続化 / migration
- PostgreSQL への short memory note 保存
- ContextSnapshotBuilder の副作用
- audio hot path
- reply routing
- DB ordering
- conversation lifecycle
- TTS / audio chunk / playback timing
- `server/session/` package split
- OutputDemand / Watcher
## 2026-05-30 セッション13

### やること（開始時に書く）
- Apple Speech STT active 時の実ログを確認し、「ともこ」だけが落ちる/捨てられるように見えるパターンを推測する
- `logs/server-debug.log` / `logs/backend-trace.jsonl` / debug recording 周辺から、VAD 分割、STT 空認識、transcript filter、participation 判定のどこで落ちているかを切り分ける
- まずは分析のみとし、Apple Speech sidecar や participation logic の変更は行わない

### やったこと
- `logs/server-debug.log` の 2026-05-27 / 2026-05-28 / 2026-05-29 周辺を確認した
- `智子聞こえますか` / `智子聞こえる` のように Apple Speech が `智子` を出した場合は、`WakeWordJudge` が `wake_word_detected` として拾えていることを確認した
- 一方で、近接する聞こえる確認の試行で `聞こえますか` / `聞こえてますか` / `聞こえてる` だけが transcript になり、wake word が落ちたように見える例を複数確認した
- `朝子聞こえてますか` / `大聞こえてますか` / `どう子聞こえてますか` のように wake word 語頭が崩れる例も確認した
- `server/edge/participation/wake_word.py` と `server/edge/pipeline/stt_filter.py` を確認し、`智子` / `朋子` は既に許容されるが、`朝子` / `どう子` / `大` は wake word として扱われないことを確認した

### 詰まったこと・解決したこと
- `logs/server.log` は直近 unit test / fake backend のログが多く、実会話判断には使いにくかったため、実ブラウザに近い `logs/server-debug.log` を主に見た
- Apple Speech が `智子` を出すケースでは後段が反応しているので、主因は「後段の智子未対応」ではなく「STT が wake word を欠落/誤認識した後に filter / participation で落ちる」ものと推定した

### 次のセッションでやること
- Apple Speech sidecar に contextual strings / custom language model を追加できるか確認する
- 実ログ由来の alias（例: `朝子` / `どう子`）を wake word 候補に入れる場合の false positive を unit test で固定する
- ambient で `聞こえますか` / `聞こえてますか` だけが来た場合を弱い呼びかけとして扱うか、録音サンプル比較後に判断する

## 2026-05-30 セッション14

### やること（開始時に書く）
- Apple Speech STT sidecar に `SFSpeechRecognitionRequest.contextualStrings` を設定する
- Python wrapper から Tomoko wake word 用の短い語句だけを sidecar へ渡す
- まずは STT 認識そのものの改善を狙い、ParticipationJudge の alias 追加や ambient 弱呼びかけ判定は行わない

### やったこと
- `_tools/apple_speech_stt/AppleSpeechSTT.swift` に `--contextual-string` の複数指定を読む処理を追加した
- `SFSpeechURLRecognitionRequest.contextualStrings` に指定語句を渡すようにした
- `server/edge/pipeline/stt_apple.py` から `ともこ` / `トモコ` / `Tomoko` / `智子` / `朋子` / `tomoko` を sidecar へ渡すようにした
- `tests/unit/test_stt_backends.py` で Apple Speech sidecar 呼び出し引数に contextual strings が含まれることを固定した

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py::test_apple_speech_transcribes_via_sidecar -q`
  - contextual strings 未実装で失敗することを確認
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py::test_apple_speech_transcribes_via_sidecar tests/unit/test_stt_backends.py::test_apple_speech_no_speech_is_empty_transcript tests/unit/test_stt_backends.py::test_apple_speech_unknown_sidecar_error_still_raises -q`
  - 3 passed
- Swift parse: `swiftc -parse _tools/apple_speech_stt/AppleSpeechSTT.swift`
  - pass
- STT/config unit: `.venv/bin/python -m pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py -q`
  - 21 passed
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- targeted diff check: `git diff --check -- server/edge/pipeline/stt_apple.py _tools/apple_speech_stt/AppleSpeechSTT.swift tests/unit/test_stt_backends.py PLAN.md LOG.md MEMORY.md`
  - pass

### 未解決・今回触っていないこと
- global `git diff --check` は、今回触っていない `prompts/persona_overlay.md` の EOF 空行で失敗した
- full unit は、今回触っていない `prompts/persona_overlay.md` が空に近い状態のため `test_persona_overlay_describes_inspired_style_without_original_lines` 1 件で失敗した
- `prompts/persona_overlay.example.md` も未追跡で存在するが、今回の作業では触っていない

### 次のセッションでやること
- 実ブラウザで `ともこ聞こえますか` 系を試し、`logs/server-debug.log` で `智子` / `ともこ` の残り方が改善するか確認する
- 改善が弱い場合だけ、実ログ由来 alias や ambient 弱呼びかけ判定を別 Phase として検討する

## 2026-05-30 セッション15

### やること（開始時に書く）
- `prompts/persona_overlay.md` が空または header だけでも unit test が落ちないようにする
- overlay の内容検査は、実際に本文が入っている場合だけ行う
- runtime code、prompt 本体、audio / STT / TTS 経路は変更しない

### やったこと
- `test_persona_overlay_describes_inspired_style_without_original_lines` を、`## PERSONA OVERLAY` header は確認しつつ、本文が空ならスタイル文言検査を skip する形に緩めた
- overlay 本文がある場合は、従来どおり `小悪魔的` / `後輩` / `原作台詞` と `一色いろは` 不在の検査を残した

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase4_thinking.py::test_persona_overlay_describes_inspired_style_without_original_lines tests/unit/test_phase4_thinking.py::test_think_fast_includes_persona_overlay_when_sibling_file_exists tests/unit/test_phase4_thinking.py::test_think_fast_omits_persona_overlay_when_sibling_file_is_missing -q`
  - 3 passed
- ruff: `.venv/bin/python -m ruff check tests/unit/test_phase4_thinking.py`
  - pass
- targeted diff check: `git diff --check -- tests/unit/test_phase4_thinking.py LOG.md`
  - pass
- full unit: `.venv/bin/python -m pytest -m unit`
  - 447 passed, 17 deselected

### 未解決・今回触っていないこと
- global `git diff --check` は、今回触っていない `prompts/persona_overlay.md` の EOF 空行で引き続き失敗する

## 2026-05-30 セッション16

### やること（開始時に書く）
- `logs/server-debug.log` を起動単位で分割し、イベント種別と濃度で読める静的 HTML レポート生成ツールを追加する
- Tomoko runtime / `/ws` / `TomoroSession` / audio hot path は変更しない
- parser と HTML 生成の unit test を先に追加する

### やったこと
- `_tools/analyze_server_debug_log.py` を追加し、`server-debug.log` を server process 起動単位で分割して静的 HTML に埋め込むようにした
- event category は `startup` / `reload` / `transcript` / `reply` / `playback` / `initiative` / `turn_taking` / `stt` / `tts` / `backend` などに分類する
- HTML 側に run selector、density slider、category checkbox、search box、summary cards、timeline を持たせた
- `make log-report` で `logs/server-debug-report.html` を生成できるようにした
- 実 `logs/server-debug.log` から `logs/server-debug-report.html` を生成し、519 runs / 25858 lines を読み込めることを確認した

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_server_debug_log_report.py -q`
  - 4 passed
- related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_server_debug_log_report.py tests/unit/test_makefile_process_entries.py -q`
  - 8 passed
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- full unit: `.venv/bin/python -m pytest -m unit`
  - 451 passed, 17 deselected
- targeted diff check: `git diff --check -- _tools/analyze_server_debug_log.py tests/unit/test_server_debug_log_report.py Makefile LOG.md`
  - pass

### 未解決・今回触っていないこと
- `prompts/persona_overlay.md` は作業開始時点から dirty のまま触っていない
- `logs/server-debug-report.html` は生成物として `logs/` 配下に出る

### 追加でやったこと
- スマホ表示で最新 reload run を選ぶと `reply` filter が 0 件になり読みにくかったため、初期選択を最新の会話系 event run に変更した
- run list に `runs with visible lines` toggle を追加し、現在の filter / density / search に一致する run だけを表示できるようにした
- run card に transcript / reply / initiative / turn_taking / error / warning の件数を表示した
- filter 変更時、現在の run に一致行が無ければ最新の一致 run へ自動移動するようにした
- `logs/server-debug-report.html` を再生成し、配信中の `http://192.168.11.66:8766/server-debug-report.html` で更新版が返ることを確認した

### 追加検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_server_debug_log_report.py -q`
  - 4 passed
- ruff focused: `.venv/bin/python -m ruff check _tools/analyze_server_debug_log.py tests/unit/test_server_debug_log_report.py`
  - pass
- HTTP check: `curl -I http://127.0.0.1:8766/server-debug-report.html`
  - 200 OK

## 2026-05-30 セッション23 追加検証

### 検証
- full unit: `.venv/bin/python -m pytest -m unit`
  - 467 passed, 17 deselected
- global ruff: `.venv/bin/python -m ruff check .`
  - pass
- targeted diff check: `git diff --check -- server/shared/candidate.py server/thinker/main.py tests/unit/test_phase90_candidates.py tests/unit/test_phase94_thinker_loop.py tests/integration/test_phase90_candidates_db.py PLAN.md MEMORY.md LOG.md`
  - pass

### 次のセッションでやること
- 必要なら commit する

## 2026-05-30 セッション23

### やること（開始時に書く）
- `arrival_candidates` が無限に増え続けないよう、thinker の arrival precompute interval で 7 日より古い期限切れ行を DELETE する
- cleanup は `TomoroSession` / `/ws` / online hot path に入れず、background thinker 側に閉じる
- unit test を先に追加し、InMemory / Postgres store と thinker 呼び出しを固定する

### やったこと
- `CandidateStore.delete_expired_arrival_candidates(older_than=...)` を追加した
- InMemory store は `valid_until < older_than` の arrival candidate を list から削除するようにした
- PostgreSQL store は `DELETE FROM arrival_candidates WHERE valid_until < %s` を実行するようにした
- `ThinkerProcess.run_arrival_precompute_once()` が precompute 前に `observed_at - 7 days` を cutoff として cleanup するようにした
- cleanup 失敗時は `error_count` に加算し、arrival precompute 自体は継続するようにした
- `ArrivalPrecomputeResult.deleted_expired_arrival_count` を追加し、ログにも削除件数を出すようにした

### 詰まったこと・解決したこと
- integration test で既存の `now=2099` を使ったため、最初の実行で実 DB の既存 `arrival_candidates` も削除対象に入った
  - `arrival_candidates` は一時候補なので source of truth ではないが、テストとして shared DB に強すぎた
  - integration test は 2000 年の隔離データだけを対象にする cutoff へ修正した
- 修正後の実 DB では `arrival_candidates` は 0 件になった

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_phase90_candidates.py::test_candidate_store_deletes_old_expired_arrival_candidates tests/unit/test_phase94_thinker_loop.py::test_arrival_precompute_deletes_expired_arrivals_older_than_seven_days -q`
  - cleanup API / result field 未実装で 2 failed
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase94_thinker_loop.py -q`
  - 9 passed
- integration: `.venv/bin/python -m pytest -m integration tests/integration/test_phase90_candidates_db.py -q`
  - 1 passed
- ruff focused: `.venv/bin/python -m ruff check server/shared/candidate.py server/thinker/main.py tests/unit/test_phase90_candidates.py tests/unit/test_phase94_thinker_loop.py tests/integration/test_phase90_candidates_db.py`
  - pass

### 次のセッションでやること
- 必要なら full unit / global ruff / commit を行う

## 2026-05-30 セッション22

### やること（開始時に書く）
- `arrival_candidates` が何を保存しているテーブルなのか、schema / DTO / thinker / session 消費経路 / 実 DB sample から説明する

### やったこと
- `MEMORY.md` / `LOG.md` / `PLAN.md` / `_reference/` / `README.md` / `ARCHITECTURE.md` を確認した
- `docker/postgres/init/006_candidates.sql`、`server/shared/candidate.py`、`server/thinker/arrival.py`、`server/session.py`、`server/gateway/candidate_commands.py` を確認した
- read-only SQL で `arrival_candidates` の件数と直近 5 件を確認した

### 分かったこと
- `arrival_candidates` は会話ログではなく、ブラウザ接続・入室時の初手ふるまいを 3 分 TTL で事前計算する一時候補テーブル
- 実 DB では 697 件、fresh unused は 1 件、used は 37 件で、直近は `behavior=wait_silent` が並んでいた

### 次のセッションでやること
- 必要なら `arrival_candidates` の掃除方針や monitor 表示名を別 Phase として検討する

## 2026-05-30 セッション17

### やること（開始時に書く）
- `TomoroSession` / runtime core に触らず、外部 read-only monitor dashboard を追加する
- `logs/server-debug.log` / `logs/backend-trace.jsonl` / 可能なら DB から snapshot を作る
- `make monitor` でローカル HTTP dashboard を起動できるようにする
- parser / snapshot / Makefile entry の unit test を先に追加する

### やったこと
- `_tools/monitor_snapshot.py` を追加し、`server-debug.log` / `backend-trace.jsonl` / PostgreSQL を read-only に読む snapshot を作れるようにした
- context build の `depth` / elapsed / timeout / source counts を抽出し、「今 deep 入った」系の観測に使えるようにした
- timeline event として transcript / participation / context / conversation_prompt / reply / initiative / turn_taking / memory / backend / playback を抽出するようにした
- `_tools/monitor_dashboard.py` を追加し、`/api/snapshot` を 2.5 秒ごとに読むローカル dashboard を実装した
- `make monitor` を追加し、`MONITOR_HOST` / `MONITOR_PORT` / `BACKEND_TRACE_LOG_FILE` を設定できるようにした
- `make monitor` で起動し、`http://127.0.0.1:8770/api/snapshot` が JSON snapshot を返すことを確認してから停止した

### 変更していないもの
- `TomoroSession`
- `server/edge/main.py`
- `/ws` protocol
- audio / STT / LLM / TTS hot path
- DB write path

### 検証
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_monitor_snapshot.py tests/unit/test_makefile_process_entries.py -q`
  - 9 passed
- ruff: `.venv/bin/python -m ruff check .`
  - pass
- full unit: `.venv/bin/python -m pytest -m unit`
  - 456 passed, 17 deselected
- targeted diff check: `git diff --check -- LOG.md Makefile _tools/monitor_snapshot.py _tools/monitor_dashboard.py tests/unit/test_monitor_snapshot.py tests/unit/test_makefile_process_entries.py`
  - pass
- runtime smoke: `MONITOR_PORT=8770 make monitor` + `curl -s http://127.0.0.1:8770/api/snapshot`
  - snapshot JSON returned

### 未解決・今回触っていないこと
- `prompts/persona_overlay.md` は作業開始時点から dirty のまま触っていない

### 追加でやったこと 2
- 会話推論 LLM に渡した prompt だけを見られる `conversation_prompt` event category を追加した
- `ThinkFastMode llm_prompt` / `server.gateway.thinking.fast ... llm_prompt` だけを `conversation_prompt` に分類し、short memory extraction などの別 prompt とは分けた
- summary card に `Prompt` 件数を追加した
- 実ログ再生成で `conversation_prompt 150` 件が分類されることを確認した

### 追加検証 2
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_server_debug_log_report.py -q`
  - 4 passed
- ruff focused: `.venv/bin/python -m ruff check _tools/analyze_server_debug_log.py tests/unit/test_server_debug_log_report.py`
  - pass
- HTTP check: `curl -I http://127.0.0.1:8766/server-debug-report.html`
  - 200 OK

## 2026-05-30 セッション24

### やること（開始時に書く）
- MaAI 本番側の `p_bc_react` 閾値を 0.68 から 0.45 に下げる
- MaAI adapter の suggestion 発火 default と TomoroSession の release gate を同じ 0.45 に揃える
- 境界値の unit test を先に追加し、focused / full unit と smoke で確認する

### やったこと
- `server/gateway/maai_backchannel.py` の MaAI react suggestion default を 0.45 に下げた
- `server/session.py` の TomoroSession release gate を 0.45 に下げた
- `tests/unit/test_maai_backchannel_adapter.py` で config default / env default を固定した
- `tests/unit/test_maai_backchannel_tap.py` で 0.45 release と 0.44 below_threshold skip を固定した
- `PLAN.md` / `MEMORY.md` に 0.68 方針を否定して 0.45 にする判断を追記した

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py -q`
  - 3 failed, 16 passed
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py -q`
  - 19 passed
- focused ruff: `.venv/bin/python -m ruff check server/session.py server/gateway/maai_backchannel.py tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py`
  - pass
- material smoke: `MAAI_MATERIAL_START_SEC=60 MAAI_MATERIAL_DURATION_SEC=30 make smoke-maai-material`
  - `logs/maai-material-smoke.json` に `threshold=0.45`、react `score=0.453804...`、`reason=user_not_speaking` を確認
- dialogue smoke: `make smoke-maai-dialogue`
  - `logs/maai-dialogue-smoke.json` に `backchannel_released`、`score=0.725980...`、`threshold=0.45`、`text=なるほど` を確認
- full unit: `.venv/bin/python -m pytest -m unit`
  - 502 passed, 17 deselected
- global ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check -- server/session.py server/gateway/maai_backchannel.py tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py PLAN.md LOG.md MEMORY.md`
  - pass

### 次のセッションでやること
- 必要なら実ブラウザ会話で 0.45 の体感頻度を確認する

## 2026-05-31 セッション1

### やること（開始時に書く）
- MaAI 相槌を有効化した実サーバーで通常会話が壊れた原因をログから切り分ける
- ambient の wake word / participation 判定前に相槌が鳴る経路を塞ぐ
- unit test を先に追加し、MaAI focused unit / smoke unit / full unit まで確認する

### 分かったこと
- `TOMOKO_MAAI_BACKCHANNEL_ENABLED=1` 後の実ログで `MaaiBackchannelTap started` は出ていた
- `attention_mode=ambient` / `state=listening` の段階で `なるほど` などの backchannel TTS が先に鳴った
- その直後の `智子聞こえる` などの transcript が Tomoko playback 中として扱われ、通常 participation path を邪魔していた
- 既存 gate は user speaking / Tomoko idle / segment once / cooldown だけで、ambient の参加判定前を止められていなかった

### やったこと
- `server/session.py` の MaAI backchannel release gate に `attention_mode=ambient` の skip を追加した
- skip reason は `attention_not_engaged` とした
- `tests/unit/test_maai_backchannel_tap.py` に ambient listening では release しない test を追加した
- 既存 release 系 unit は `engaged` へ遷移してから release を期待するように直した
- `_tools/smoke_maai_dialogue.py` / `_tools/smoke_maai_material.py` の session release harness は、会話中の相槌検証として `engaged` で実行するようにした

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py -q`
  - ambient listening で `backchannel_released` になり 1 failed
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py -q`
  - 11 passed
- MaAI related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_smoke_maai_dialogue.py tests/unit/test_smoke_maai_material.py -q`
  - 27 passed
- focused ruff: `.venv/bin/python -m ruff check server/session.py tests/unit/test_maai_backchannel_tap.py _tools/smoke_maai_dialogue.py _tools/smoke_maai_material.py`
  - pass
- full unit: `.venv/bin/python -m pytest -m unit`
  - 503 passed, 17 deselected
- global ruff: `.venv/bin/python -m ruff check .`
  - pass
- diff check: `git diff --check -- server/session.py tests/unit/test_maai_backchannel_tap.py _tools/smoke_maai_dialogue.py _tools/smoke_maai_material.py PLAN.md MEMORY.md LOG.md`
  - pass

### 次のセッションでやること
- `TOMOKO_MAAI_BACKCHANNEL_ENABLED=1 make server-debug` で実ブラウザ再確認し、ambient wake word 前に相槌が出ないことを見る

## 2026-05-31 セッション2

### やること（開始時に書く）
- 実 server-debug.log の user transcript / Tomoko reply / MaAI 相槌タイミングを見て、通常会話が勝手に始まる原因を切り分ける
- engaged follow-up 中の短い未完 fragment を通常 LLM reply に昇格させない gate を追加する
- unit test を先に追加し、focused / full unit / ruff / diff check まで確認する

### 分かったこと
- `logs/server-debug.log` では `相槌の` が `attention_mode=engaged` で `participation mode=invited reason=attention_engaged_followup` になっていた
- その結果、通常 LLM が起動し `相槌のタイミングについて、もっと詳しく教えてくれる？` を話し始めていた
- MaAI 相槌 `なるほど` / `うん` 自体は gesture audio だが、その前後の短い STT 断片が通常 turn に昇格して会話を壊していた
- `けど` まで未完 fragment 扱いすると `さっきの続きなんだけど` も observer になってしまうため、助詞終端だけに絞る必要があった

### やったこと
- `WakeWordJudge` の engaged / cooldown follow-up 判定に短い未完 fragment guard を追加した
- 12 文字以下で `の` / `で` / `を` / `が` / `に` / `と` / `は` / `も` で終わる follow-up は `low_confidence_followup` として observer に落とす
- `tests/unit/test_participation.py` に `相槌の` / `相槌のタイミングで` の赤テストを追加した
- `tests/unit/test_attention_mode.py` に engaged 中の未完 fragment が conversation log / reply_text を発生させないテストを追加した

### 検証
- red test: `.venv/bin/python -m pytest -m unit tests/unit/test_participation.py::test_engaged_followup_filters_short_unfinished_fragment tests/unit/test_attention_mode.py::test_engaged_short_unfinished_fragment_does_not_start_reply -q`
  - 2 failed
- focused unit: `.venv/bin/python -m pytest -m unit tests/unit/test_participation.py tests/unit/test_attention_mode.py tests/unit/test_phase3_stt.py -q`
  - 23 passed
- MaAI / participation related unit: `.venv/bin/python -m pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_smoke_maai_dialogue.py tests/unit/test_smoke_maai_material.py tests/unit/test_participation.py tests/unit/test_attention_mode.py -q`
  - 44 passed
- focused ruff: `.venv/bin/python -m ruff check server/edge/participation/wake_word.py tests/unit/test_participation.py tests/unit/test_attention_mode.py`
  - pass
- full unit: `.venv/bin/python -m pytest -m unit`
  - 505 passed, 17 deselected
- global ruff: `.venv/bin/python -m ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で `相槌の...` のような一瞬の言いかけが通常 reply を開始しないことを確認する

## 2026-05-31 セッション3

### やること（開始時に書く）
- 最新 server-debug.log で相槌後も user 発話が遮られる原因を時系列で確認する
- user transcript / MaAI backchannel / playback state / barge-in / participation の流れから、データ経路の破綻点を特定する
- 必要ならテストを先に追加して最小修正する

### 分かったこと
- 最新 `logs/server-debug.log` では、MaAI 相槌 `うん` が `turn_id=None` の playback telemetry として届いていた
- `AudioTurnController` は `turn_id=None` でも通常 playback chunk として扱っていたため、相槌直後の user transcript が `playback_ended_grace` / `playback_active_chunk` の echo として observer に落ちていた
- 例: `プログラミングプログラミングさぁAIで` が `turn_taking_skipped reason=playback_non_interrupt_candidate` → `barge-in kind=echo reason=playback_ended_grace` になった
- さらに長い発話でも `...そういうのって` のような継続助詞終端が `attention_engaged_followup` になり、ユーザーの続きより先に通常 reply が始まっていた
- つまり問題は MaAI score そのものではなく、gesture audio と通常発話 playback が同じ echo 判定レーンを汚していたことと、未完 follow-up の判定が短文だけに限定されすぎていたこと

### やったこと
- `AudioTurnController.handle_playback_telemetry()` で `turn_id=None` の telemetry は通常 playback / echo grace state に反映しないようにした
- `tests/unit/test_audio_turn_controller.py` に `turn_id=None` telemetry が `idle` のままになる test を追加した
- `tests/unit/test_phase885_session_runtime.py` に `TomoroSession.post_event()` 経由でも `turn_id=None` playback が runtime state を汚さない test を追加した
- `WakeWordJudge` の low confidence follow-up に、長い発話でも `って` / `とか` / `みたいな` / `という` / `というか` で終わる未完 continuation tail を追加した
- `tests/unit/test_participation.py` に実ログ由来の `...そういうのって` transcript が observer に落ちる test を追加した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_audio_turn_controller.py -q`
  - `test_audio_turn_controller_ignores_playback_telemetry_without_turn_id` が 1 failed
- red test: `.venv/bin/pytest -m unit tests/unit/test_participation.py -q`
  - `test_engaged_followup_filters_long_unfinished_continuation_tail` が 1 failed
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_participation.py tests/unit/test_audio_turn_controller.py tests/unit/test_phase885_session_runtime.py tests/unit/test_maai_backchannel_tap.py -q`
  - 30 passed
- focused ruff: `.venv/bin/ruff check server/gateway/audio_turn.py server/edge/participation/wake_word.py tests/unit/test_audio_turn_controller.py tests/unit/test_phase885_session_runtime.py tests/unit/test_participation.py`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 508 passed, 17 deselected

### 次のセッションでやること
- 実ブラウザ会話で MaAI 相槌直後の user transcript が `barge-in kind=echo` に落ちないことを確認する
- まだ遮る場合は VAD silence threshold / STT segment boundary の実測ログを見て、未完 tail guard ではなく発話終了判定側を調整する

## 2026-05-31 セッション4

### やること（開始時に書く）
- 最新 server-debug.log で「話し終える前に通常応答が始まる」原因を、VAD speech_end / transcript / participation / reply_start の時系列から確認する
- 造りの問題か設定の問題かを切り分ける
- 相槌頻度を増やすため、MaAI release gate の cooldown / once-per-segment / threshold のどこを緩めるべきかを実ログとテストで判断する

### 分かったこと
- 最新 `logs/server-debug.log` では、MaAI 相槌直後の `turn_id=None` echo 混入は前回修正対象だが、まだ通常 reply が早く始まるケースがあった
- `彼女の社会的な...その辺がさぁよくさぁ` が `attention_engaged_followup` になり、約 1.8s 後に reply_text が出ていた
- `あのね会話を破壊するんだよね友達とその関係が` も同様に `attention_engaged_followup` になり、ユーザーの続きより先に reply_start していた
- これは VAD silence threshold だけの設定問題ではなく、speech_end 後の transcript を「完成発話」か「未完で聞き続ける発話」かに分ける participation gate の問題だった
- 相槌が少ない原因は、TomoroSession 側の `already_released_in_speech_segment` gate が長い user speech segment 内の 2 回目以降を止めていたこと

### やったこと
- `WakeWordJudge` の長い未完 continuation tail に `さぁ` と長文の `が` 終端を追加した
- 実ログ由来の `...よくさぁ` / `...関係が` が `low_confidence_followup` になる unit test を追加した
- MaAI backchannel の同一 speech segment 1 回制限を外した
- MaAI backchannel の TomoroSession global cooldown を 2000ms から 1500ms に短縮した
- 同一 speech segment でも cooldown 後なら 2 回目の相槌が release される unit test に更新した
- `_docs/latency.md` に unit-only control-path regression として記録した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_participation.py::test_engaged_followup_filters_long_unfinished_continuation_tail -q`
  - 実ログ由来の `...よくさぁ` / `...関係が` が invited になり 1 failed
- red test: `.venv/bin/pytest -m unit tests/unit/test_maai_backchannel_tap.py::test_maai_backchannel_can_repeat_in_same_user_speech_after_cooldown -q`
  - 2 回目が `backchannel_skipped` になり 1 failed
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_participation.py tests/unit/test_attention_mode.py tests/unit/test_maai_backchannel_tap.py tests/unit/test_maai_backchannel_adapter.py tests/unit/test_phase885_session_runtime.py tests/unit/test_audio_turn_controller.py -q`
  - 47 passed
- focused ruff: `.venv/bin/ruff check server/session.py server/edge/participation/wake_word.py tests/unit/test_participation.py tests/unit/test_maai_backchannel_tap.py`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 508 passed, 17 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で `...さぁ` / `...が` で通常 reply が開始されず、Tomoko が聞き続けることを確認する
- 相槌頻度がまだ少ない場合は MaAI adapter 側 cooldown 900ms / react threshold 0.45 の実ログ分布を見る

## 2026-05-31 セッション5

### やること（開始時に書く）
- 最新 server-debug.log で、MaAI 相槌が鳴った直後に user turn が終端扱いされ通常 reply が始まる経路を確認する
- 相槌 TTS / playback が user mic VAD に回り込んで speech_end を誘発していないかを切り分ける
- gesture audio が通常 reply start の trigger にならないように、テストを先に追加して最小修正する

### 分かったこと
- 最新 `logs/server-debug.log` では、00:56:08 に MaAI 相槌 `なるほど` が出た直後、00:56:09 の user transcript が `playback_state=speaking` として扱われていた
- 前回の `turn_id=None` playback telemetry 除外だけでは足りず、サーバー内部で `_release_backchannel_audio()` が `audio_turns.begin_turn()` と `reserve_audio_chunk()` を通していた
- その結果、相槌が gesture audio ではなく Tomoko の通常発話として `is_tomoko_speaking()` を立て、turn-taking / barge-in / participation に影響していた

### やったこと
- MaAI backchannel release では `audio_turns.begin_turn()` を呼ばないようにした
- `_flush_tts_text(..., track_audio_turn=False)` を追加し、backchannel TTS chunk は `AudioTurnController.reserve_audio_chunk()` を通さず送るようにした
- `_send_audio_chunk(..., mark_reply_output=False)` を追加し、backchannel audio send が reply output latency state を進めないようにした
- `tests/unit/test_maai_backchannel_tap.py` で backchannel release 後も `audio_turns.is_tomoko_speaking()` が False で、`audio_start` / `audio_end` を出さないことを固定した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_maai_backchannel_tap.py::test_maai_react_suggestion_releases_llm_less_backchannel_audio -q`
  - `session.audio_turns.is_tomoko_speaking()` が True になり 1 failed
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_maai_backchannel_tap.py tests/unit/test_audio_turn_controller.py tests/unit/test_phase885_session_runtime.py tests/unit/test_barge_in.py tests/unit/test_session_latency_probe_characterization.py tests/unit/test_reply_speech_normalizer.py -q`
  - 44 passed
- focused ruff: `.venv/bin/ruff check server/session.py tests/unit/test_maai_backchannel_tap.py`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 508 passed, 17 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で MaAI 相槌直後の transcript が `playback_state=speaking` にならないことを確認する
- まだ通常 reply が早い場合は、相槌音声のマイク回り込みで VAD segment が切れているかを raw audio / VAD score ログで見る

## 2026-05-31 セッション6

### やること（開始時に書く）
- MaAI 相槌を TomoroSession mutation から外し、server-owned gesture audio lane を新設する
- 既存の防御修正は残しつつ、TomoroSession 内の backchannel release command / cooldown / threshold / 無害化パッチを削除する
- MaAI callback が TomoroSession.get_now_state() の snapshot だけを読んで gesture audio を出すことを unit test で固定する

### 分かったこと
- 前回の `_flush_tts_text(track_audio_turn=False)` / `_send_audio_chunk(mark_reply_output=False)` は症状を抑えたが、TomoroSession 内に gesture audio 例外を残していた
- MaAI 相槌は通常 reply / stop ack / pregenerated candidate と同じ audio turn 経路に入れるべきではなく、gateway 側の別 lane で既存 `/ws` audio send だけを使う方が境界が明確になる
- TomoroSession には `get_now_state()` があるため、release gate は mutation なしで `attention_mode` / `vad_state` / `playback_state` を読める

### やったこと
- `server/gateway/gesture_audio.py` に `GestureAudioEmitter` を追加した
- MaAI callback を `session.apply_backchannel_suggestion()` から `GestureAudioEmitter.release_backchannel()` に差し替えた
- TomoroSession から `backchannel_suggested` reduce、`apply_backchannel_suggestion()`、`release_backchannel_audio` command を削除した
- 通常 reply 用の `_flush_tts_text()` / `_send_audio_chunk()` を gesture 例外なしの形に戻した
- `smoke-maai-dialogue` / material smoke の release harness を TomoroSession ではなく gesture lane に差し替えた
- `tests/unit/test_gesture_audio.py` を追加し、release / skip / cooldown が session mutation なしで動くことを固定した
- `MEMORY.md` と `_docs/latency.md` に今回の境界判断と検証結果を追記した

### 検証
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_gesture_audio.py tests/unit/test_maai_backchannel_tap.py tests/unit/test_smoke_maai_dialogue.py tests/unit/test_smoke_maai_material.py tests/unit/test_maai_backchannel_adapter.py -q`
  - 23 passed
- full unit: `.venv/bin/pytest -m unit -q`
  - 504 passed, 17 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で MaAI 相槌が鳴っても `playback_state=speaking` /通常 reply start が誘発されないことを確認する
- まだ遮る場合は、相槌音声そのもののマイク回り込みで user VAD segment が切れていないか raw audio / VAD score ログを見る

## 2026-05-31 セッション7

### やること（開始時に書く）
- 最新 `logs/server-debug.log` で `今何時` が反応しない原因を transcript / filter / participation / prompt の順に確認する
- 会話の終わりを誤判定して応答した箇所が VAD speech_end 由来か、speech_end 後の stale reply cancel 漏れかを切り分ける
- 必要なら clock query の filter 例外と resumed listening 時の unstarted reply cancel を unit test で固定する

### 分かったこと
- `今何時` は STT では `transcript text='今何時'` と認識されていた
- 反応しなかった直接原因は `TranscriptFilter` が `action=drop reason=low_audio_short_text` にしたことだった
- 長めに `俺今何時とかっていうのは反応できひんのかな` と言った場合は accept され、prompt に current local time が入り、Tomoko は `深夜の1時32分` と答えていた
- 会話を遮った箇所では、VAD が長い user speech を一度 speech_end にし、その直後に `state changed to listening` へ戻っていた
- つまり VAD は普通に segment boundary を出しているが、再開した user speech を見て未出力 reply を stale cancel できていなかった

### やったこと
- `TranscriptFilter` に clock query 例外を追加し、`今何時` / `いま何時` / `何時ぐらい` / `時刻` などは低音量短文でも accept するようにした
- `TomoroSession._transition("listening")` で reply generation active かつ reply output 未開始なら `resumed_user_speech_before_output` として cancel するようにした
- `tests/unit/test_stt_filter.py` に低音量 `今何時` の accept test を追加した
- `tests/unit/test_streaming_tts_pipeline.py` の unstarted reply 方針を、空 transcript 待ちではなく listening 再開時 cancel に更新した
- `MEMORY.md` と `_docs/latency.md` に今回の原因と境界判断を追記した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_stt_filter.py::test_filter_accepts_low_audio_clock_query tests/unit/test_streaming_tts_pipeline.py::test_new_listening_cancels_unstarted_reply_before_output -q`
  - 2 failed
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_stt_filter.py tests/unit/test_streaming_tts_pipeline.py tests/unit/test_attention_mode.py tests/unit/test_participation.py tests/unit/test_phase885_session_runtime.py tests/unit/test_audio_turn_controller.py tests/unit/test_gesture_audio.py -q`
  - 42 passed
- full unit: `.venv/bin/pytest -m unit -q`
  - 505 passed, 17 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass

### 次のセッションでやること
- 実ブラウザ会話で短い `今何時` が filter drop されず、即答できることを確認する
- 長い user speech の途中で一度 VAD speech_end が出ても、話し続けた場合に未出力 reply が cancel されることを server log で確認する

## 2026-05-31 セッション8

### やること（開始時に書く）
- Phase 10.20.21 として output lane / floor ownership の境界を明示する
- `OutputLane = reply_turn | initiative_turn | gesture_audio | stop_ack | interrupting_turn` を追加し、現状の各 lane の期待動作を characterization test で固定する
- `AudioTurnController` は turn audio 専用、MaAI 相槌は `gesture_audio` として対象外、candidate gate は initiative lane の floor policy として読める形にする
- conversation log の保存対象 lane / 非保存 lane を helper と test で明示する
- closed-loop の「入力を受ける / 床を取る / 出力する / 出力結果が次の入力判定へ戻る」観点で、通常 turn に戻る lane と gesture として外に留まる lane を分ける

### やったこと
- `OutputLane` を追加し、`reply_turn` / `initiative_turn` / `gesture_audio` / `stop_ack` / `interrupting_turn` を固定した
- `AudioTurnController.begin_turn(lane=...)` を追加し、`gesture_audio` は turn audio ではないため拒否するようにした
- candidate / arrival の command payload に `output_lane="initiative_turn"` を明示した
- candidate final gate payload に `output_lane` と `floor_policy="ambient_idle"` を出すようにした
- `conversation_log_writes_output_lane()` を追加し、`reply_turn` / `initiative_turn` / `interrupting_turn` は保存対象、`gesture_audio` / `stop_ack` は非保存対象として test で固定した
- `GestureAudioEmitter` の既存 `lane="gesture_audio"` emission と `audio_start` / `audio_end` を出さない挙動を closed-loop 境界として維持した

### 詰まったこと・解決したこと
- 最初の赤テストでは `OutputLane` と `conversation_log_writes_output_lane` が未定義で collection error になった
  - 型 alias と helper を追加して解消した
- ruff で `server/gateway/audio_turn.py` の import order が落ちた
  - `ruff check --fix` で整形した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_phase10_session_contract.py tests/unit/test_gesture_audio.py -q`
  - `OutputLane` / `conversation_log_writes_output_lane` 未実装で 2 errors
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_phase10_session_contract.py tests/unit/test_gesture_audio.py -q`
  - 24 passed
- related unit: `.venv/bin/pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_phase10_session_contract.py tests/unit/test_gesture_audio.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase5_tts.py tests/unit/test_phase105_session_runtime.py tests/unit/test_streaming_tts_pipeline.py -q`
  - 53 passed
- focused ruff: `.venv/bin/ruff check server/shared/models.py server/gateway/audio_turn.py server/gateway/gesture_audio.py server/gateway/candidate_commands.py server/session.py tests/unit/test_audio_turn_controller.py tests/unit/test_phase10_session_contract.py tests/unit/test_gesture_audio.py`
  - pass
- full unit: `.venv/bin/pytest -m unit -q`
  - 508 passed, 17 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass

### 次のセッションでやること
- Tomoko が人間発話中に床を取る `interrupting_turn` を実装する場合は、今回の lane / floor policy を前提に別 Phase で characterization test から始める

## 2026-05-31 セッション9

### やること（開始時に書く）
- `tests/integration/test_phase180_world_observations_db.py` が共有DBの既存 candidate に依存して落ちる問題を直す
- `PostgresWorldObservationStore` への connection 注入は影響が大きいため避ける
- `try/finally` cleanup と fixture id の直接 DB 確認で integration test を安定化する

### やったこと
- `phase18-checksum` を fixture checksum として定数化した
- test 本体を `try/finally` で囲み、assertion 途中失敗でも fixture document を checksum で削除するようにした
- `fetch_candidate_interpretations(limit=10)` の global topN assertion をやめ、作成した `interpretation_id` が `world_observation_trace` に出ることを直接確認するようにした
- pending item 確認も global limit に依存せず、作成した `item_id` を直接 query するようにした

### 詰まったこと・解決したこと
- 外側 transaction rollback は store が各 method で別 connection を開くため、そのままでは isolation にならない
  - 今回は connection 注入を避け、cleanup と fixture id 直接確認に限定した

### 検証
- focused ruff + integration: `.venv/bin/ruff check --fix tests/integration/test_phase180_world_observations_db.py && .venv/bin/pytest tests/integration/test_phase180_world_observations_db.py -m integration -q`
  - 1 passed
- full integration: `.venv/bin/pytest -m integration -q`
  - 9 passed, 516 deselected

### 次のセッションでやること
- 他の integration test でも shared DB の global topN / limit に fixture が入る前提を見つけたら、fixture id 直接確認へ寄せる

## 2026-05-31 セッション10

### やること（開始時に書く）
- README.md の編集制限が一時解除されたため、現行 runtime / backend / worker / test / lane 境界に合わせて全面メンテする
- 古い Phase 10.10 / 10.11 中心の説明を、MaAI gesture lane、OutputLane、closed-loop、現行 smoke / integration 状態を含む入口文書へ更新する

### やったこと
- README.md を全面更新し、現行の runtime shape、OutputLane、TomoroSession boundary、default backend、setup、worker、smoke/perf、DB/log、test 方針を整理した
- 古い Phase 10.10 / 10.11 中心の「直近の注力点」を、MaAI `gesture_audio`、未完 transcript gate、output lane / floor ownership、shared DB integration fixture 方針へ更新した
- Makefile と config/central_realtime.toml に合わせて command 名と default backend を確認した

### 検証
- `make check`
  - ruff pass
  - 508 passed, 17 deselected

### 次のセッションでやること
- README に書いた current posture が変わったら、Phase 完了時に README も追従させる

## 2026-05-31 セッション11

### やること（開始時に書く）
- GPU が心許ないため、Apple Silicon の GPU pressure を Tomoko の観測面へ取り込む
- mactop v2 の `--headless --count` JSON を optional provider として使い、直接 IOReport 実装を移植しない
- `logs/system-metrics.jsonl` と `make monitor` dashboard で常時確認できる形にする

### やったこと
- `_tools/system_metrics.py` を追加し、mactop headless JSON を normalized `SystemMetricsSample` に変換できるようにした
- `make system-monitor` を追加し、`logs/system-metrics.jsonl` へ GPU active / GPU power / GPU freq / ANE power / memory / thermal を常時 JSONL 追記できるようにした
- mactop 未インストール、timeout、非0終了、parse failure は `available=false` sample として記録するようにした
- `_tools/monitor_snapshot.py` / `_tools/monitor_dashboard.py` を更新し、`make monitor` の dashboard card に最新 GPU pressure を表示するようにした
- README.md / MEMORY.md / PLAN.md に GPU pressure monitor の境界判断とコマンドを追記した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_system_metrics.py tests/unit/test_monitor_snapshot.py tests/unit/test_makefile_process_entries.py -q`
  - `_tools.system_metrics` 未実装で collection error
- focused unit + ruff: `.venv/bin/pytest -m unit tests/unit/test_system_metrics.py tests/unit/test_monitor_snapshot.py tests/unit/test_makefile_process_entries.py -q && .venv/bin/ruff check _tools/system_metrics.py _tools/monitor_snapshot.py _tools/monitor_dashboard.py tests/unit/test_system_metrics.py tests/unit/test_monitor_snapshot.py tests/unit/test_makefile_process_entries.py`
  - 15 passed / ruff pass
- 実 one-shot: `.venv/bin/python _tools/system_metrics.py --count 1 --interval-sec 0.1`
  - mactop から `gpu=67.87829432219607%`, `gpu_power=0.51702W`, `gpu_freq=338.0MHz`, `system_name=Apple M4 Max` を取得
- full unit: `.venv/bin/pytest -m unit -q`
  - 512 passed, 17 deselected
- global ruff: `.venv/bin/ruff check .`
  - pass

### 次のセッションでやること
- 実会話中に `make system-monitor` と `make monitor` を並走させ、LLM/TTS/STT の backend trace と GPU pressure の時系列相関を見る

## 2026-05-31 セッション12

### やること（開始時に書く）
- 手元の `make system-monitor` が `timeout_after_sec:5.0` を連続出力する原因を確認する
- mactop headless の初回 sample 所要時間に合わせて timeout を調整する
- timeout 行が最新でも dashboard が直近 available sample を表示できるようにする

### 分かったこと
- `mactop --headless --count 1 --interval 2000` は実測で約 6.9 秒かかっていた
- 初期実装の timeout 5 秒は `SYSTEM_METRICS_INTERVAL_SEC=2` の default には短すぎた

### やったこと
- `default_mactop_timeout_sec(interval_sec)` を追加し、timeout を `max(10s, interval_sec + 8s)` にした
- `latest_system_metrics_sample()` は最新行が unavailable でも、直近の available sample を優先して返すようにした
- unit test で timeout scaling と recent available sample 優先を固定した

### 検証
- focused unit + ruff: `.venv/bin/pytest -m unit tests/unit/test_system_metrics.py tests/unit/test_monitor_snapshot.py -q && .venv/bin/ruff check _tools/system_metrics.py tests/unit/test_system_metrics.py`
  - 10 passed / ruff pass
- 実 one-shot: `.venv/bin/python _tools/system_metrics.py --output /tmp/tomoko-system-metrics-fix.jsonl --count 1 --interval-sec 2`
  - `gpu=40.210557059020715%`, `gpu_power=0.473387W`, `gpu_freq=338.0MHz`
- full unit + ruff: `.venv/bin/pytest -m unit -q && .venv/bin/ruff check .`
  - 515 passed, 17 deselected / ruff pass

## 2026-05-31 セッション15

### やること（開始時に書く）
- Tomoko の隣に Perplexity 専用 research operator の別 git project を作る
- chatgpt-el の CDP 方針を参考にしつつ、GPL code copy ではなく自前実装の足場にする
- Tomoko とは MCP 風 protocol / process boundary で接続し、Tomoko DB 永続化は Tomoko 側責務として文書化する
- AGENTS.md / ARCHITECTURE.md / README.md / PLAN.md など初期文書と最小 Python scaffold を作り、初回 git commit まで行う

### やったこと
- `/Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator` を新規 git project として作成した
- `AGENTS.md` / `README.md` / `ARCHITECTURE.md` / `PLAN.md` / `MEMORY.md` / `LOG.md` を追加した
- `ResearchRequest` / `ResearchResult` / `Citation`、CDP target selection、Perplexity prompt/result shaping、CLI placeholder、MCP placeholder を追加した
- Tomoko DB 永続化は Tomoko 側責務、operator は Chrome/Perplexity 操作と structured result 返却だけ、という境界を文書化した
- `chatgpt-el` は GPLv3-or-later source のため code copy せず、CDP workflow の参考に留める判断を `MEMORY.md` に追記した

### 検証
- `PYTHONPATH=src /Users/seijiro/.local/share/mise/installs/python/3.14/bin/pytest -q`
  - 7 passed
- `PYTHONPATH=src python3 -m compileall src tests`
  - pass
- `uv run pytest` / `uv run ruff check .`
  - この shell では `uv` が見つからず未実行
- `python3 -m ruff check .`
  - この shell では `ruff` が見つからず未実行

### 次のセッションでやること
- `tomoko-research-operator` Phase 1 として、実 Chrome CDP target 接続と Perplexity tab reuse/open の smoke を作る

## 2026-05-31 セッション16

### やること（開始時に書く）
- Research MCP command boundary 初段として、PLAN.md に Phase を追記する
- Tomoko 側に rule-based research intent detector、MCP client/parser、ResearchCommandRunner を追加する
- TomoroSession は `research_requested` / `research_result_ready` の final owner として command / emission だけを扱う
- Chrome / Perplexity automation、DB 永続化、「教えて」で本文を読む処理は次 Phase に残す

### やったこと
- PLAN.md に `Research MCP command boundary initial integration` Phase を追記した
- `server/gateway/research.py` を追加し、`ResearchIntentDetector` / `ResearchMcpClient` / `ResearchCommandRunner` / DTO を実装した
- MCP response は `structuredContent` を読み、citation URL dedupe と failure status 分離を Tomoko 側で行うようにした
- TomoroSession に `research_requested` / `research_result_ready` reducer を追加し、`submit_research_request` command と result-ready emission を固定した
- `tests/unit/test_research_gateway.py` と `tests/unit/test_research_session_contract.py` を追加した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
  - `server.gateway.research` 未実装で 2 collection errors
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
  - 11 passed
- related unit: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase10_candidate_command_runner.py -q`
  - 31 passed
- focused ruff: `.venv/bin/ruff check server/gateway/research.py server/session.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py`
  - pass
- full unit / global ruff / diff check:
  - `.venv/bin/pytest -m unit -q`
    - 530 passed, 17 deselected
  - `.venv/bin/ruff check .`
    - pass
  - `git diff --check`
    - pass

### 次のセッションでやること
- Research result の Tomoko DB 永続化と、「教えて」で pending result を読む follow-up rule を別 Phase として実装する

## 2026-06-01 セッション5

### やること（開始時に書く）
- live log で engaged から cooldown までが約 8〜10 秒で短いことを確認したため、既定の engaged timeout を 2.5 倍にする
- 既定値の挙動を unit test で固定し、cooldown timeout 自体は変更しない

### やったこと
- `TomoroSession` の既定 `engaged_timeout_ms` を 8 秒から 20 秒に変更した
- 既定値では 20 秒未満の無音で engaged を維持し、20 秒相当で cooldown に入る unit test を追加した
- `cooldown_timeout_ms` は従来どおり 8 秒のまま維持した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_attention_mode.py::test_default_engaged_timeout_waits_twenty_seconds_before_cooldown -q`
  - 既存 8 秒既定値のため failed
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_attention_mode.py -q`
  - 9 passed
- focused ruff: `.venv/bin/ruff check server/session.py tests/unit/test_attention_mode.py`
  - pass
- `git diff --check`
  - pass
- live server log:
  - `WatchFiles detected changes in 'server/session.py'. Reloading...`
  - `Application startup complete.`

### 次のセッションでやること
- live voice で playback end から cooldown までが約 20 秒に伸びたか確認する

## 2026-06-01 セッション6

### やること（開始時に書く）
- `今何時` への返答後、engaged 中の `もうかなり夜遅いやん` / `もうかなり遅いやん` に応答しなかった原因を live log から切り分ける
- engaged follow-up の低音量判定が普通の短文を observer に落としていないか unit test で固定する

### 分かったこと
- `03:29:55` と `03:30:03` の transcript は STT / transcript filter では accept されていた
- ただし `audio_level_db=-30.1/-31.9` かつ 20 文字以下だったため、`WakeWordJudge` の `low_confidence_followup` が observer にしていた
- そのため UI では `engaged / observer` と表示され、通常 reply pipeline が起動しなかった

### やったこと
- `WakeWordJudge` の低音量 blanket follow-up drop を 20 文字以下から 6 文字以下へ狭めた
- `もうかなり夜遅いやん` は低音量でも engaged follow-up として `invited` になる unit test を追加した
- observer になった時も participation reason を server log に出すようにした

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_participation.py::test_engaged_followup_keeps_quiet_complete_sentence -q`
  - 既存 20 文字境界のため failed
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_participation.py tests/unit/test_attention_mode.py -q`
  - 20 passed
- focused ruff: `.venv/bin/ruff check server/edge/participation/wake_word.py server/session.py tests/unit/test_participation.py tests/unit/test_attention_mode.py`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- live voice で `もうかなり夜遅いやん` 系の自然な短文が invited になり、通常 reply が起動するか確認する

## 2026-06-01 セッション2

### やること（開始時に書く）
- live `logs/server-debug.log` で、MCP 完了後に Tomoko から「調べ終わった」発話が来ない原因を切り分ける
- `research_result_ready` の UI emission と音声発話経路の接続を確認する

### やったこと
- live log では `Research MCP subprocess completed ... speakable=True`、result ingest、`Research command runner finished` まで成功していたことを確認した
- その後に `reply_text` / `tts_start` が出ておらず、`research_result_ready.notice_text` が UI emission のまま音声 command に接続されていないことを確認した
- speakable な `research_result_ready` が `start_research_notice_reply` command を返すようにした
- `ResearchCommandRunner` が background handler 経路でも `start_precomputed_reply(reason="research_result_notice")` を実行するようにした
- research smoke の期待値を、完了通知 `調べ終わったよ。聞く？` と follow-up 本文の 2 段発話に更新した

### 検証
- focused research unit:
  - `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
    - 48 passed
- focused ruff:
  - `.venv/bin/ruff check server/session.py server/gateway/research.py server/edge/main.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/integration/test_research_mcp_smoke.py`
    - pass
- full unit:
  - `.venv/bin/pytest -m unit -q`
    - 561 passed, 19 deselected, 2 failed
    - 2 件は `tests/unit/test_phase88_context_snapshot.py` の calendar fixture 日付ズレ

### 次のセッションでやること
- full unit に残っている calendar fixture の日付依存失敗を別件として直す
- live browser で実際に `調べ終わったよ。聞く？` が音声再生されることを確認する

### 追記
- live `02:41:10` の再試行では MCP subprocess 自体は起動したが、`status=failed speakable=False` で 0.9 秒程度で戻っていた
- 同じ `cwd=/Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator` から手動で `uv run tomoko-research-mcp` に同じ query を投げると `status=completed` で返ったため、path / cwd の恒久失敗ではなく、その回の operator result が failed だった
- Tomoko 側が failed / needs_human result では通知 command を出さず無音になる仕様だったため、speakable=false でも `調べきれなかったみたい。` を `start_research_notice_reply` で発話するようにした
- Research MCP completed / command runner finished log に `error_reason` を出すようにし、次回 failed になった時の理由をログで追えるようにした

### 追加検証
- `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 48 passed
- `.venv/bin/ruff check server/session.py server/gateway/research.py tests/unit/test_research_session_contract.py tests/unit/test_research_gateway.py`
  - pass

### さらに追記
- live log では `調べ終わったよ。聞く？` 後の `ともこ教えて` が partial では出たが、final transcript は `ともこ` になっており、ここは STT 側で `教えて` が落ちていた
- その後の `主称について教えて` / `手書について教えて` / `ともこ 日本の首相について教えて` は final transcript として通った
- ただし `is_research_answer_request()` が direct answer cue の `教えて` を含むと query overlap を見ずに pending result を返していたため、`日本の首相について教えて` に対して直前 pending の `手書` result を返していた
- topic 付き `教えて` は pending query と overlap する時だけ research answer として扱うようにした
- 単独の `教えて` / `ともこ教えて` は pending result があれば引き続き通す

### 追加検証2
- `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 49 passed
- `.venv/bin/ruff check server/gateway/research.py tests/unit/test_research_gateway.py`
  - pass

## 2026-05-31 セッション26

### やること（開始時に書く）
- central realtime の active STT backend を、精度優先の `local_whisper_mlx_large_turbo_q4` に切り替える
- config と current backend 表、config unit test の期待値を揃える
- 設定変更後に STT/config 周辺 unit と ruff を通す

### やったこと
- `config/central_realtime.toml` の `stt_backend` を `local_whisper_mlx_large_turbo_q4` に切り替えた
- README の default backend 表を更新し、Apple Speech は STT 比較候補として残した
- `tests/unit/test_phase0_config.py` で active STT が MLX Whisper large turbo q4 であることを固定した
- 精度優先で MLX Whisper large turbo q4 に戻す判断を PLAN / MEMORY に追記した

### 検証
- focused unit / ruff: `.venv/bin/pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_prepare_runtime.py -q && .venv/bin/ruff check tests/unit/test_phase0_config.py _tools/prepare_runtime.py server/shared/config.py`
  - 8 passed / ruff pass
- full unit / global ruff: `.venv/bin/pytest -m unit -q && .venv/bin/ruff check .`
  - 561 passed, 19 deselected / ruff pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- 実 server 再起動後、startup warm-up と live transcript で `local_whisper_mlx_large_turbo_q4` が使われていることを `logs/backend-trace.jsonl` で確認する

## 2026-06-01 セッション1

### やること（開始時に書く）
- 最新 `logs/server-debug.log` で Research MCP が起動しないように見える原因を切り分ける
- live transcript path で `research request detected` / `Research MCP subprocess starting` / failure reason を確認する
- default MCP command が隣 repo `../tomoko-research-operator` を指すように修正し、unit test で固定する

### 分かったこと
- 最新 live log では MCP は未起動ではなく、`Research MCP subprocess starting` まで進んでいた
- 直後に `No such file or directory (os error 2)` で失敗していた
- command は `uv --directory /Users/seijiro/Sync/sync_work/by-llms/tomoko/tomoko-research-operator run tomoko-research-mcp` になっていた
- 実際の operator repo は `/Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator` なので、repo 内ではなく sibling repo を指す必要がある

### やったこと
- `ResearchMcpClient` に `cwd` を追加し、subprocess を指定 working directory で起動できるようにした
- default MCP client は sibling operator repo を cwd にし、command は `uv run tomoko-research-mcp` にした
- unit test で default MCP client の cwd が `../tomoko-research-operator` であることを固定した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py::test_default_research_mcp_client_points_to_sibling_operator -q`
  - 既存実装では operator parent が `tomoko` になり 1 failed
- focused research unit / ruff: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_makefile_process_entries.py -q && .venv/bin/ruff check server/edge/main.py server/gateway/research.py tests/unit/test_research_gateway.py`
  - 46 passed / ruff pass
- full unit / global ruff: `.venv/bin/pytest -m unit -q && .venv/bin/ruff check .`
  - 2 failed。`tests/unit/test_phase88_context_snapshot.py` の calendar fixture が 2026-06-01 現在日付に対して過去日扱いになり、今回の Research MCP 変更とは別件

### 次のセッションでやること
- server を再起動して、live request で `Research MCP subprocess starting ... cwd=/Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator` と completion / operator 側 failure を確認する

## 2026-05-31 セッション21

### やること（開始時に書く）
- InMemory research result store を PostgreSQL backed `research_results` table へ置き換える
- `summary_text` と `summary_embedding` を保存し、deep context が DB から research summary を取得できるようにする
- runtime の default store factory も PostgreSQL に寄せる
- DB insert / semantic search / ContextSnapshotBuilder fetch を integration test で固定する

### やったこと
- `docker/postgres/init/015_research_results.sql` を追加し、`research_results` table / HNSW index / fetched_at index を定義した
- `PostgresResearchResultStore` を追加し、summary embedding の upsert と pgvector cosine search を実装した
- `ResearchCommandRunner` が保存時に embedding model 名も渡すようにした
- default runtime / gateway text session が PostgreSQL backed research result store を `TomoroSession` に渡すようにした
- integration test で DB insert、semantic search、`ContextSnapshotBuilder(depth="deep")` の research summary fetch を確認した

### 検証
- `.venv/bin/pytest -m integration tests/integration/test_research_results_db.py -q`
  - 1 passed
- `.venv/bin/pytest -m unit tests/unit/test_research_session_contract.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_prepare_runtime.py tests/unit/test_startup_warmup.py -q`
  - 38 passed
- `.venv/bin/pytest -m unit -q`
  - 550 passed, 19 deselected
- `.venv/bin/ruff check .`
  - pass
- `git diff --check`
  - pass

### 次のセッションでやること
- Research request 検出を実際の transcript path に接続する
- 実 runtime で `ResearchCommandRunner` を起動する command drain の配置を決める

## 2026-05-31 セッション22

### やること（開始時に書く）
- 最新 `logs/server-debug.log` で「調査が走らない」原因を確認する
- 実 transcript path で research intent を検出し、通常 reply に流さないようにする
- central browser runtime で `submit_research_request` command を `ResearchCommandRunner` に渡す

### 見たログ
- `2026-05-31 20:19:39` に `智子小浜大統領について調べて` は STT / filter / participation まで通っていた
- その直後に `research_requested` ではなく `ThinkFastMode llm_prompt` が出ており、通常 reply に流れていた
- `research_result_ready` / `submit_research_request` / MCP subprocess のログは出ていなかった

### やったこと
- `process_transcript()` の filter 後・turn-taking 前に research request 検出を追加した
- research request は user turn として記録し、`research_requested` event / `submit_research_request` command に落として通常 reply を開始しないようにした
- central browser runtime で `ResearchCommandRunner` を background command drain として接続した
- default MCP command は `TOMOKO_RESEARCH_MCP_COMMAND` があればそれを使い、未指定なら隣の `tomoko-research-operator` を `uv --directory ... run tomoko-research-mcp` で呼ぶようにした
- `智子` 表記の wake name も research query から strip するようにした

### 検証
- `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
  - 29 passed
- `.venv/bin/pytest -m unit -q`
  - 553 passed, 19 deselected
- `.venv/bin/ruff check .`
  - pass
- `make smoke-research-mcp`
  - `ok=true`, `event_types` に `research_request_accepted` / `research_result_ready` あり
- `git diff --check`
  - pass

### 次のセッションでやること
- 実 browser runtime で `智子、OpenAIについて調べて` を試し、`research_request_accepted` と operator 実行をログ確認する
- 必要なら「調べ始めたよ」系の軽い通知 emission / speech を設計する

## 2026-05-31 セッション23

### やること（開始時に書く）
- Research request を受けた時に、無言ではなく Tomoko が通常応答として「調べているので待って」と言えるようにする
- 固定文ではなく、LLM prompt に一時 directive を入れて Tomoko の自然な短文にする
- ただし調査対象について推測で答え始めないように縛る

### やったこと
- `ThinkingInput.response_directive` を追加した
- `ThinkFastMode` が `RESPONSE DIRECTIVE` を system prompt に混ぜられるようにした
- research request 検出後、`submit_research_request` を background drain に渡したうえで、通常 reply pipeline を起動するようにした
- research wait reply 用 directive には「今は調査結果を答えず、調べ始めたことと少し待ってほしいことだけ」を明記した
- unit test で prompt directive と research request 時の LLM wait reply 起動を固定した

### 検証
- `.venv/bin/pytest -m unit tests/unit/test_research_session_contract.py tests/unit/test_research_gateway.py tests/unit/test_phase4_thinking.py::test_think_fast_includes_response_directive -q`
  - 31 passed
- `.venv/bin/pytest -m unit -q`
  - 555 passed, 19 deselected
- `.venv/bin/ruff check .`
  - pass
- `make smoke-research-mcp`
  - `ok=true`

### 次のセッションでやること
- 実 browser runtime で research request 時の待機応答と、MCP 完了後の `調べ終わったよ。聞く？` の体感を確認する
- 完了通知が無音 emission だけなら、result-ready notice の発話化を別 Phase で検討する

## 2026-05-31 セッション24

### やること（開始時に書く）
- `TomoroSession.process_transcript("智子オバマ大統領について調べて")` から、LLM wait reply / MCP / 「教えて」follow-up まで一気通貫で見える smoke を作る
- fake MCP / fake conversation LLM を使い、外部 UI ではなく TomoroSession lifecycle を固定する

### やったこと
- `_tools/smoke_research_tomoro_session_flow.py` を追加した
- `make smoke-research-session` を追加した
- unit test で smoke JSON summary を固定した
- smoke は `智子オバマ大統領について調べて` を transcript として入れ、`教えて` follow-up まで実行する
- JSON summary に wait reply、result-ready notice、answer reply、prompt directive 有無、deep context summary を出すようにした

### 検証
- `.venv/bin/pytest -m unit tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 11 passed
- `.venv/bin/ruff check _tools/smoke_research_tomoro_session_flow.py tests/unit/test_smoke_research_tomoro_session_flow.py tests/unit/test_makefile_process_entries.py`
  - pass
- `make smoke-research-session`
  - `ok=true`
  - `wait_reply_text="調べてみるね。少し待って。"`
  - `answer_requested=true`
  - `answer_reply_text="オバマ大統領について について調べたよ。バラク・オバマはアメリカ合衆国の第44代大統領です。"`
  - `wait_prompt_has_response_directive=true`

### 次のセッションでやること
- 実 operator command を `--command` に渡して同じ TomoroSession transcript smoke を任意確認する
- result-ready notice を実際に発話するか、UI emission だけにするか体感で判断する

## 2026-05-31 セッション18

### やること（開始時に書く）
- Research result が `調べ終わったよ。聞く？` として届いた後、pending result を TomoroSession 内に保持する
- transcript finalize の早い段階で「教えて」「聞かせて」系 follow-up を検出し、通常 LLM 返答ではなく `research_answer_requested` に落とす
- pending result が speakable な時だけ `short_answer` を emission / speech command として返し、同じ result を二重に読まないようにする

### やったこと
- `is_research_answer_request()` を追加し、「教えて」「聞かせて」「結果」「内容」「読んで」「はい、お願い」系を rule-based follow-up として検出するようにした
- TomoroSession が speakable な `research_result_ready` を pending として保持し、`research_answer_requested` で一度だけ消費するようにした
- `process_transcript()` の filter 後・turn-taking / participation 前で research answer follow-up を横取りし、`research_answer_requested` emission と `start_research_answer_reply` command に落とすようにした
- `start_research_answer_reply` は `start_precomputed_reply(..., output_lane="reply_turn")` で `reply_text` / TTS / `reply_done` へ流すようにした
- `_tools/smoke_research_mcp_flow.py` は result ready 後に `教えて` follow-up まで simulation し、`reply_text_deltas` を出すようにした

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
  - `is_research_answer_request` 未実装で collection error
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
  - 21 passed
- smoke/unit/integration:
  - `.venv/bin/pytest -m unit tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
    - 22 passed
  - `.venv/bin/pytest -m integration tests/integration/test_research_mcp_smoke.py -q`
    - 1 passed
  - `make smoke-research-mcp`
    - `answer_requested=true`, `reply_text_deltas=["今日のOpenAI関連ニュースを短く についての smoke 応答です。"]`
- 実 operator smoke:
  - `.venv/bin/python _tools/smoke_research_mcp_flow.py --command 'uv --directory /Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator run tomoko-research-mcp' --timeout-sec 180 --output logs/research-mcp-real-smoke.json`
    - `answer_requested=true`, `reply_done_count=1`, `citation_count=3`, `provider_trace_id=perplexity-20260531T103729Z`
- related unit / focused ruff:
  - `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_makefile_process_entries.py -q`
    - 31 passed
  - `.venv/bin/ruff check server/gateway/research.py server/session.py _tools/smoke_research_mcp_flow.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/integration/test_research_mcp_smoke.py`
    - pass
- full unit / global ruff:
  - `.venv/bin/pytest -m unit -q`
    - 542 passed, 18 deselected
  - `.venv/bin/ruff check .`
    - pass

### 次のセッションでやること
- Research request 検出を実際の transcript path に接続し、発話から `research_requested` を自動生成する
- Research result の Tomoko DB 永続化を別 Phase として実装する

## 2026-05-31 セッション19

### やること（開始時に書く）
- Research answer follow-up を一度だけで消費する方針を見直す
- pending result は短命な latest research cache として残し、「OpenAIについて知ってることある？」のような query overlap follow-up でも返せるようにする
- 無関係な「知ってることある？」では pending result を誤用しないようにする
- 次の DB 永続化 Phase では、research result 保存時に embedding を作り、安価に再検索できる索引にする判断を記録する

### やったこと
- `research_answer_requested` で pending research result を消費しないようにした
- `is_research_answer_request(text, query=...)` を拡張し、`知ってる` / `わかる` / `分かる` 系は query overlap がある時だけ research answer follow-up として扱うようにした
- `OpenAIについて知ってることある？` は `今日のOpenAI関連ニュースを短く` の pending result を読むが、`Anthropicについて知ってることある？` は読まないことを unit test で固定した
- DB 永続化 Phase では保存時 embedding を作り、安価な再検索索引にする判断を MEMORY.md / PLAN.md に追記した

### 検証
- red test: `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
  - `OpenAIについて知ってることある？` が query overlap として扱われず 2 failed
- focused unit / ruff:
  - `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
    - 25 passed
  - `.venv/bin/ruff check server/gateway/research.py server/session.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py`
    - pass
- smoke / integration:
  - `.venv/bin/pytest -m unit tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py -q`
    - 25 passed
  - `.venv/bin/pytest -m integration tests/integration/test_research_mcp_smoke.py -q`
    - 1 passed
  - `make smoke-research-mcp`
    - `answer_requested=true`, `reply_done_count=1`
- full unit / global ruff / diff check:
  - `.venv/bin/pytest -m unit -q`
    - 546 passed, 18 deselected
  - `.venv/bin/ruff check .`
    - pass
  - `git diff --check`
    - pass

### 次のセッションでやること
- Research request 検出を実際の transcript path に接続する
- Research result DB 永続化では embedding を同時生成し、query overlap より広い semantic retrieval に拡張する

## 2026-05-31 セッション20

### やること（開始時に書く）
- Research result 取り込み時に LLM summary を作り、その summary を embedding して保存する
- ContextSnapshotBuilder の `depth=deep` で research result を optional source として fetch する
- prompt へ混ぜる research context は raw answer ではなく summary に限定する
- fake MCP / fake LLM summary / fake embedding store の smoke 的 test で、取り込みから deep prompt 露出まで固める

### やったこと
- `ResearchResultSummarizer` を追加し、MCP result 取り込み時に LLM backend で deep context 用 summary を作れるようにした
- `ResearchCommandRunner` に optional `result_store` / `embedding_backend` / `summarizer` を追加し、speakable result の summary を embedding して保存するようにした
- `ResearchContextHit` と `InMemoryResearchResultStore` を追加した
- `ContextBuildPolicy.deep/reflective` に research result source を追加し、`ContextSnapshotBuilder` が embedding 検索で research summaries を読むようにした
- `ThinkFastMode` の prompt に `RESEARCH CONTEXT` を追加し、raw answer ではなく summary だけを混ぜるようにした
- `make smoke-research-mcp` で fake MCP -> fake LLM summary -> fake embedding store -> deep context fetch まで JSON に出すようにした

### 検証
- focused unit:
  - `.venv/bin/pytest -m unit tests/unit/test_research_session_contract.py tests/unit/test_phase88_context_snapshot.py::test_deep_snapshot_reads_research_result_summaries tests/unit/test_phase88_context_snapshot.py::test_fast_snapshot_does_not_read_research_results tests/unit/test_phase4_thinking.py::test_think_fast_includes_research_summary_context_from_snapshot tests/unit/test_smoke_research_mcp_flow.py -q`
    - 12 passed
  - `.venv/bin/pytest -m unit tests/unit/test_research_session_contract.py tests/unit/test_phase88_context_snapshot.py tests/unit/test_phase4_thinking.py tests/unit/test_smoke_research_mcp_flow.py -q`
    - 49 passed
- smoke:
  - `make smoke-research-mcp`
    - `ingested_research_count=1`, `deep_research_summaries=["今日のOpenAI関連ニュースを短く の外部調査結果をdeep context用に要約したメモです。"]`
- 実 operator smoke:
  - `.venv/bin/python _tools/smoke_research_mcp_flow.py --command 'uv --directory /Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator run tomoko-research-mcp' --timeout-sec 180 --output logs/research-mcp-real-smoke.json`
    - `ingested_research_count=1`, `deep_research_summaries` あり, `provider_trace_id=perplexity-20260531T105544Z`
- integration smoke:
  - `.venv/bin/pytest -m integration tests/integration/test_research_mcp_smoke.py -q`
    - 1 passed
- full unit / global ruff:
  - `.venv/bin/pytest -m unit -q`
    - 550 passed, 18 deselected
  - `.venv/bin/ruff check .`
    - pass

### 次のセッションでやること
- InMemory store を PostgreSQL backed `research_results` table に置き換える
- Research request 検出を実際の transcript path に接続する

## 2026-05-31 セッション17

### やること（開始時に書く）
- Research MCP の Tomoko 側経路を unit fake client だけでなく、subprocess JSON-RPC まで含む integration / smoke で確認する
- `ResearchIntentDetector` -> `TomoroSession` command -> `ResearchMcpClient` subprocess -> `research_result_ready` emission を一気通貫で固定する
- 実 Perplexity / Chrome の揺れとは分け、deterministic fake MCP subprocess を標準 smoke にする

### やったこと
- `_tools/smoke_research_mcp_flow.py` を追加し、speech-like text から research request を作り、TomoroSession command と MCP subprocess を通して result-ready emission まで JSON summary に出せるようにした
- fake MCP subprocess を runtime に生成し、標準 smoke / integration では外部 UI に依存せず JSON-RPC `tools/call` 経路を確認するようにした
- `make smoke-research-mcp` を追加した
- 同じ smoke script の `--command` で隣の `tomoko-research-operator` 実コマンドも呼べることを確認した

### 検証
- focused unit: `.venv/bin/pytest -m unit tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_makefile_process_entries.py -q`
  - 10 passed
- integration smoke: `.venv/bin/pytest -m integration tests/integration/test_research_mcp_smoke.py -q`
  - 1 passed
- 実 fake smoke: `make smoke-research-mcp`
  - `ok=true`, `event_types=["research_request_accepted","research_result_ready"]`, `status=completed`
- 実 operator smoke: `.venv/bin/python _tools/smoke_research_mcp_flow.py --command 'uv --directory /Users/seijiro/Sync/sync_work/by-llms/tomoko-research-operator run tomoko-research-mcp' --timeout-sec 180 --output logs/research-mcp-real-smoke.json`
  - `ok=true`, `status=completed`, `citation_count=2`, `provider_trace_id=perplexity-20260531T102903Z`
- related unit / focused ruff:
  - `.venv/bin/pytest -m unit tests/unit/test_research_gateway.py tests/unit/test_research_session_contract.py tests/unit/test_smoke_research_mcp_flow.py tests/unit/test_makefile_process_entries.py -q`
    - 21 passed
  - `.venv/bin/ruff check _tools/smoke_research_mcp_flow.py tests/unit/test_smoke_research_mcp_flow.py tests/integration/test_research_mcp_smoke.py tests/unit/test_makefile_process_entries.py`
    - pass
- full unit / global ruff / diff check:
  - `.venv/bin/pytest -m unit -q`
    - 532 passed, 18 deselected
  - `.venv/bin/ruff check .`
    - pass
  - `git diff --check`
    - pass

### 次のセッションでやること
- Research result の Tomoko DB 永続化と、「教えて」で pending result を読む follow-up rule を別 Phase として実装する
## 2026-06-02 セッション1

### やること（開始時に書く）
- `make server-debug` の起動失敗ログを確認し、server / STT / TTS / 外部依存のどこで落ちているか切り分ける

### やったこと
- 貼り付けログを確認し、STT warm-up は `local_apple_speech_ja` で成功していることを確認した
- TTS warm-up が `voicevox_tsumugi` で始まり、VOICEVOX Engine の `/audio_query` 接続時に `httpx.ConnectError: All connection attempts failed` で落ちていることを確認した
- `config/central_realtime.toml` / README / `server/shared/inference/tts/voicevox.py` を確認し、VOICEVOX Engine の接続先が `http://127.0.0.1:50021` であることを確認した
- `curl http://127.0.0.1:50021/version` と `lsof -iTCP:50021` で、現在 50021 に listening process がないことを確認した

### 詰まったこと・解決したこと
- Tomoko server 自体の import / config parse ではなく、起動時 warm-up の外部 TTS 依存が未起動なため lifespan startup が失敗していた

### 次のセッションでやること
- VOICEVOX.app を起動するか `make prepare` を実行して Engine が 50021 で応答する状態にしてから `make server-debug` を再実行する

## 2026-06-02 セッション4

### やること（開始時に書く）
- timer / alarm foundation Phase を PLAN.md に追加する
- client-local timer / alarm 再生ではなく、別プロセス worker + DB row + TomoroSession gate + 既存 TTS/audio lane の境界で切る
- 初段は単発 timer / alarm のみに限定し、ポモドーロや連鎖 timer は未対応として残す

## 2026-06-02 セッション5

### やること（開始時に書く）
- task ledger voice create/complete を TomoroSession + real PostgreSQL + real memory_extraction 31B backend で E2E っぽく確認する
- create は実 DB 書き込み、曖昧 complete は real 31B structured extraction 経由で completed へ更新されることを確認する
- smoke 用 row は確認後に cleanup する

### やったこと
- `TomoroSession` を直接起動し、`TaskLedgerCommandRunner(session=..., store=PostgresTaskLedgerStore, backend_provider=lmstudio_gemma4_31b)` を接続した
- `ともこ、E2E作成確認をタスクにして` を `process_transcript()` に通し、`task_ledger_entries` に active row が作られることを確認した
- 比較用に `PDF資料レビュー` と `ログ監査` を実 DB に active row として投入した
- `ともこ、さっきのPDFレビュー終わった` を `process_transcript()` に通し、deterministic match ではなく `lmstudio_gemma4_31b` の structured extraction 経由で `PDF資料レビュー` が completed になることを確認した
- smoke 後に test row を全削除し、cleanup 後の remaining rows が空であることを確認した

### 検証
- real backend: `lmstudio_gemma4_31b` / `gemma-4-31b-it-mlx`
- create elapsed: 50.95ms
- ambiguous complete with real 31B elapsed: 8480.82ms
- complete result: `task_ledger_update_recorded`, `status=completed`, `operation=complete`, `task_id=e2e-task-ledger-pdf-review`, `reason=structured_match`
- DB rows before cleanup:
  - `e2e-task-ledger-pdf-review`: completed, `completed_at IS NOT NULL`
  - `e2e-task-ledger-log-audit`: active
  - `task-8046a54c06f777c6`: active, source voice
- cleanup check: `remaining_rows=[]`

### 次のセッションでやること
- 必要なら同じ smoke を `_tools/` 化して、手動 live regression として再実行しやすくする

## 2026-06-02 セッション8

### やること（開始時に書く）
- 長めの Tomoko 発話で前半の音程が低く聞こえる現象を切り分ける
- 入力 audio path は 16kHz のまま維持し、VOICEVOX の出力 sample_rate だけを 16kHz に寄せる
- config contract test を更新し、設定変更後に unit test を通す

### やったこと
- `config/central_realtime.toml` と `config/edge_kitchen.toml` の `voicevox_tsumugi` / `voicevox_tsumugi_stream` を `sample_rate = 16000` に変更した
- `tests/unit/test_phase0_config.py` の config contract を VOICEVOX 16kHz に更新した
- PLAN.md / MEMORY.md に、入力 hot path を動かさず VOICEVOX 出力だけを 16kHz に寄せる切り分け方針を追記した

### 詰まったこと・解決したこと
- active client は `AudioContext({ sampleRate: 16000 })` なので、AudioContext を 24kHz に上げると mic / VAD / STT まで揺れる
  - 今回は VOICEVOX の `outputSamplingRate` だけを 16kHz に寄せ、再生 resampling の影響を切り分ける形にした

### 検証
- focused config/VOICEVOX unit: `uv run pytest -m unit tests/unit/test_phase0_config.py tests/unit/test_voicevox_tts.py -q`
  - 10 passed
- focused ruff: `uv run ruff check config tests/unit/test_phase0_config.py tests/unit/test_voicevox_tts.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 613 passed, 23 deselected

### 次のセッションでやること
- live browser で長めの Tomoko 発話を再生し、前半の低音程感が 16kHz VOICEVOX 出力で変わるか確認する
- 変わらない場合は VOICEVOX の長文 prosody / accent phrase 生成側を第一候補として見る

## 2026-06-05 セッション3 追記

### やったこと
- `make information-collect-world` の timeout 原因を切り分けた
- Tomoko 側の MCP subprocess timeout は 240 秒だったが、operator 側の Perplexity provider timeout が 90 秒既定のままだったため、`TOMOKO_WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC=240` を Make から渡すようにした
- `informations/prompts/daily_world_observation.md` を、Perplexity document / file / canvas を作らずチャット回答欄へ本文だけを返す指示へ寄せた
- Tomoko 側の保存ガードを追加し、provider が「作成しました」系の短い document summary を返した時は work Markdown に保存しないようにした
- CDP `innerText` では Markdown の `#` / `##` が落ち、`事実：` / `source_hint：` のように全角コロンになることがあるため、保存ガードを rendered text に合わせた label / topic heading 判定へ変更した
- provider が返した loose frontmatter や `以下が本文です。` の preamble は保存時に剥がし、Tomoko 側の deterministic frontmatter を付け直すようにした

### 詰まったこと・解決したこと
- 最初の失敗 `timed out waiting for Perplexity response to settle; chars=5` は、外側 Make timeout ではなく operator 内部の provider timeout が短いことが原因だった
- その後の失敗は Perplexity が短文しか返していないのではなく、CDP `innerText` の rendered text と保存ガードの Markdown source 前提がズレていたことが原因だった

### 検証
- operator focused: `uv run pytest tests/test_mcp_server.py tests/test_models.py -q`
  - 18 passed
- operator focused ruff: `uv run ruff check src/tomoko_research_operator/mcp_server.py src/tomoko_research_operator/perplexity.py tests/test_mcp_server.py`
  - pass
- Tomoko focused unit: `uv run pytest -m unit tests/unit/test_world_observation_operator_client.py tests/unit/test_makefile_process_entries.py -q`
  - 18 passed
- Tomoko focused ruff: `uv run ruff check server/world_observations/operator_client.py _tools/collect_world_observation.py tests/unit/test_world_observation_operator_client.py tests/unit/test_makefile_process_entries.py`
  - pass
- live Make: `make information-collect-world`
  - `world_observation_collected informations/work/2026-06-05-world-observation.md`
- strict validator: `uv run python _tools/validate_world_observation_md.py --strict informations/work/2026-06-05-world-observation.md`
  - `valid=true`, `issues=[]`
- ingest dry-run: `make information-ingest-dry-run`
  - `processed=1 archived=0 failed=0 skipped=1`
  - `would_ingest informations/work/2026-06-05-world-observation.md`
- generated work Markdown size: 12,671 chars

### 次のセッションでやること
- 必要なら `make information-ingest-once` / `make information-interpret-once` で DB 取り込みまで進める

## 2026-06-05 セッション4

### やること（開始時に書く）
- `make information-ingest-once` が `failed=1` になる原因を切り分ける
- failed sidecar の error を確認し、normalizer / DB / raw Markdown validation のどこで落ちたか特定する

### やったこと
- `informations/failed/2026-06-05/*.error.json` を確認し、失敗原因が raw Markdown validation ではなく LLM normalizer の context length 超過であることを確認した
- `WorldObservationNormalizer` の deterministic fallback parser を拡張し、Markdown source の `## topic` / `### title` だけでなく、CDP rendered text の `topic` 単独行 / `観測1：title` 形式も拾うようにした
- `source_hint：` の全角コロン label も fallback source extraction で拾うようにした
- failed に移動済みだった `2026-06-05-world-observation.md` を `informations/work/` に戻し、`make information-ingest-once` を再実行した

### 詰まったこと・解決したこと
- `make information-collect-world` は正常で、`make information-ingest-once` の `failed=1` は LLM normalizer が 29,913 chars の raw body をそのまま受けて context length を超えたことが原因だった
- fallback parser が rendered text 形式を知らず items を作れなかったため failed 扱いになっていた
- 修正後は LLM normalizer が失敗しても代表項目 8 件を deterministic fallback で保存できる

### 検証
- focused unit: `uv run pytest -m unit tests/unit/test_world_observation_normalizer.py tests/unit/test_world_observation_ingest.py tests/unit/test_world_observation_operator_client.py tests/unit/test_makefile_process_entries.py -q`
  - 26 passed
- focused ruff: `uv run ruff check server/world_observations/normalizer.py server/world_observations/operator_client.py _tools/collect_world_observation.py tests/unit/test_world_observation_normalizer.py tests/unit/test_world_observation_operator_client.py tests/unit/test_makefile_process_entries.py`
  - pass
- strict validator: `uv run python _tools/validate_world_observation_md.py --strict informations/failed/2026-06-05/2026-06-05-world-observation.md`
  - `valid=true`, `issues=[]`
- ingest once retry: `make information-ingest-once`
  - `processed=1 archived=1 failed=0 skipped=0`
  - archived to `informations/archived/2026-06-05/2026-06-05-world-observation.md`
- DB check
  - 2026-06-05 completed document: 1
  - 2026-06-05 completed document items: 8
  - 修正前の failed document row: 1 row remains as failure history
- interpret once: `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
  - 2026-06-05 completed document は 8 item 中 3 item interpreted（limit 10 が過去未処理 item も拾ったため）

### 次のセッションでやること
- 必要なら `WORLD_OBSERVATION_INTERPRET_LIMIT` を上げて 2026-06-05 の残り 5 item も interpret する
- normalizer prompt へ渡す raw body の clipping / chunking は別 Phase として検討する

## 2026-06-05 セッション5

### やること（開始時に書く）
- `make information-collect-world` の world observation timeout を 600 秒へ伸ばす
- 生成中の Perplexity page text に含まれる `blocked` 文字列で `needs_human` へ早期終了しないようにする

### やったこと
- `WORLD_OBSERVATION_MCP_TIMEOUT_SEC` / `WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC` の既定を 240 秒から 600 秒へ変更した
- operator 側 `_world_observation_provider_config()` の env 未指定 default も 600 秒へ変更した
- Perplexity snapshot が `isGenerating=true` の間は `classify_needs_human()` の結果を採用せず、生成中の本文に含まれる `blocked` などで `needs_human` へ早期終了しないようにした
- Makefile / operator の timeout default と生成中 block 判定保留を unit test で固定した

### 検証
- Tomoko focused unit: `uv run pytest -m unit tests/unit/test_makefile_process_entries.py -q`
  - 11 passed
- Tomoko focused ruff: `uv run ruff check tests/unit/test_makefile_process_entries.py`
  - pass
- operator focused unit: `uv run pytest tests/test_mcp_server.py tests/test_models.py -q`
  - 20 passed
- operator focused ruff: `uv run ruff check src/tomoko_research_operator/mcp_server.py src/tomoko_research_operator/perplexity.py tests/test_mcp_server.py tests/test_models.py`
  - pass
- dry-run command: `make -n information-collect-world`
  - `TOMOKO_WORLD_OBSERVATION_MCP_TIMEOUT_SEC=600`
  - `TOMOKO_WORLD_OBSERVATION_PROVIDER_TIMEOUT_SEC=600`
- diff check
  - Tomoko / operator ともに `git diff --check` pass

### 次のセッションでやること
- live `make information-collect-world` を 600 秒 timeout で再実行し、Perplexity の長文生成が完走するか確認する

## 2026-06-05 セッション6

### やること（開始時に書く）
- 600 秒 timeout 後も `make information-ingest-once` が `failed=1` になる原因を切り分ける
- 最新 failed sidecar と実 Markdown を確認し、fallback parser の未対応形式を特定する

### やったこと
- 最新の `informations/failed/2026-06-05/2026-06-05-world-observation-2.md.error.json` を確認し、今回も raw Markdown validation ではなく LLM normalizer の context length 超過で失敗していることを確認した
- 実 Markdown は `news` / `economy` などの topic 単独行の下に、`1. ガザ情勢...` のような番号付き見出しを持つ rendered text 形式だった
- `WorldObservationNormalizer` の deterministic fallback parser を拡張し、`1. title` / `1) title` / `1：title` 形式も observation title として拾うようにした
- failed に移動済みだった `2026-06-05-world-observation-2.md` を `informations/work/2026-06-05-world-observation.md` に戻し、ingest / interpret を再実行した

### 詰まったこと・解決したこと
- 前回対応した `観測1：title` 形式だけでは、Perplexity が返す `1. title` 形式を拾えなかった
- LLM normalizer の context length 超過は引き続き許容し、代表項目抽出は deterministic fallback で受ける方針を維持した

### 検証
- strict validator: `uv run python _tools/validate_world_observation_md.py --strict informations/work/2026-06-05-world-observation.md`
  - `valid=true`, `issues=[]`
- ingest retry: `make information-ingest-once`
  - `processed=1 archived=1 failed=0 skipped=0`
  - archived to `informations/archived/2026-06-05/2026-06-05-world-observation-1.md`
- DB check
  - latest 2026-06-05 completed document: 8 items
- interpret once: `make information-interpret-once`
  - `world_observation_interpret interpreted=10 error_count=0`
- focused unit: `uv run pytest -m unit tests/unit/test_world_observation_normalizer.py tests/unit/test_world_observation_ingest.py tests/unit/test_world_observation_operator_client.py tests/unit/test_makefile_process_entries.py -q`
  - 27 passed
- focused ruff: `uv run ruff check server/world_observations/normalizer.py tests/unit/test_world_observation_normalizer.py`
  - pass
- diff check: `git diff --check`
  - pass

### 次のセッションでやること
- Perplexity rendered text の見出し形式がさらに揺れる場合は、fallback parser を prompt format 依存から label block extraction へ寄せる

## 2026-06-07 セッション1

### やること（開始時に書く）
- LM Studio から dflash へ変更した後の 26B / 31B 推論速度を調べる
- `_docs/latency.md` と過去の LOG / MEMORY 実測を確認し、向上か低下かを判断する

### やったこと
- `config/central_realtime.toml` の 31B が `localhost:8081`、26B が `localhost:8082` を向いていることを確認した
- dflash の `/v1/models` が `mlx-community/gemma-4-31b-it-4bit` / `mlx-community/gemma-4-26b-a4b-it-4bit` を返すことを確認した
- config の model name と dflash canonical model ID の両方で OpenAI-compatible streaming probe を実行した
- dflash は `delta.reasoning_content` を先に返し、Tomoko の現行 parser が読む `delta.content` は後から出ることを確認した

### 詰まったこと・解決したこと
- first any token だけを見ると 26B は約 0.36 秒、31B は約 0.99 秒に見えるが、これは主に `reasoning_content` であり Tomoko の発話本文ではない
- Tomoko のユーザー可視 latency は first content で見る必要がある

### 検証
- 26B config name `gemma-4-26b-a4b-it-mlx` / `localhost:8082`
  - 3 runs avg first any 362.8ms
  - 3 runs avg first content 3645.9ms
  - 3 runs avg total 3679.7ms
- 31B config name `gemma-4-31b-it-mlx` / `localhost:8081`
  - 3 runs avg first any 990.3ms
  - 3 runs avg first content 8616.5ms
  - 3 runs avg total 8661.5ms
- dflash canonical IDs
  - 26B `mlx-community/gemma-4-26b-a4b-it-4bit`: avg first content 3604.0ms / total 3635.1ms
  - 31B `mlx-community/gemma-4-31b-it-4bit`: avg first content 8652.9ms / total 8714.7ms

### 次のセッションでやること
- dflash の thinking / reasoning 出力を無効化できる API option があるか確認する
- 可能なら Tomoko backend に dflash 専用 option または `reasoning_content` handling を入れるか判断する

## 2026-06-07 セッション2

### やること（開始時に書く）
- dflash で Gemma 4 を think なしで呼ぶ方法を確認する
- LM Studio 時代の no-think に相当する呼び方を Tomoko の config / backend に反映する

### やったこと
- `dflash serve --help` と dflash package code を確認し、CLI の `--chat-template-args` / request body の `chat_template_kwargs` が `enable_thinking` を制御することを確認した
- dflash live request で `chat_template_kwargs = {"enable_thinking": false}` を試し、`reasoning_content` が出ず `content` が即時に返ることを確認した
- `think=false` / `thinking=false` は dflash では効かないことを確認した
- `BackendSpec.chat_template_kwargs` を追加し、`LMStudioBackend` が OpenAI-compatible payload に forward するようにした
- `config/central_realtime.toml` の 26B / 31B dflash backend に `chat_template_kwargs = { enable_thinking = false }` を追加した
- config contract / LMStudioBackend unit test を更新した

### 詰まったこと・解決したこと
- Gemma 4 chat template は `enable_thinking=false` でも system message があると thought channel prefix を持つため、Tomoko の system prompt 付き request では request body 側で明示する必要があった
- dflash の default model IDs は canonical repo ID を返すが、config の `gemma-4-26b-a4b-it-mlx` / `gemma-4-31b-it-mlx` でも no-think request は通った

### 検証
- dflash live probe
  - with system + no extra: reasoning chunks only, content none
  - with system + `chat_template_kwargs.enable_thinking=false`: first content 437.0ms / total 548.9ms, content `了解。`
- Tomoko `InferenceRouter` / `LMStudioBackend` live probe
  - conversation 26B: first 438.0ms / total 504.8ms, text `了解。`
  - memory_extraction 31B: first 1372.2ms / total 2085.3ms, text `了解。`
- user-restarted dflash no-think direct probe
  - 26B with request kwargs: 3-run avg first content 276.6ms / total 369.1ms, reasoning chunks 0
  - 31B with request kwargs: 3-run avg first content 306.7ms / total 984.0ms, reasoning chunks 0
  - server-side no-think only also produced reasoning chunks 0
- user-restarted Tomoko `InferenceRouter` live probe
  - conversation 26B: first 541.4ms / total 643.1ms, text `了解。`
  - memory_extraction 31B: first 1064.8ms / total 1723.5ms, text `了解。`
- focused unit: `uv run pytest -m unit tests/unit/test_lm_studio_backend.py tests/unit/test_phase0_config.py -q`
  - 13 passed
- focused ruff: `uv run ruff check server/shared/config.py server/shared/inference/router.py server/shared/inference/backends/lm_studio.py tests/unit/test_lm_studio_backend.py tests/unit/test_phase0_config.py`
  - pass

### 次のセッションでやること
- `make server-debug` の実会話で `role=conversation` first_delta / TTS first_chunk までの体感を確認する

## 2026-06-07 セッション3

### やること（開始時に書く）
- LM Studio と dflash の 26B / 31B を、短文 prompt と Tomoko 風の長文 prompt で実測比較する
- 長文 prompt で dflash の prefix/cache 効果が顕著に出るか確認する

### やったこと
- LM Studio `http://192.168.11.66:1234` の `/v1/models` で `gemma-4-26b-a4b-it-mlx` / `gemma-4-31b-it-mlx` が利用可能なことを確認した
- dflash は前回同様 `localhost:8082` を 26B、`localhost:8081` を 31B として model ID を明示して叩いた
- 短文 `了解。` prompt、Tomoko 風 10k prompt、LM Studio も返せる中長文 Tomoko 風 prompt の 3 種類を direct OpenAI-compatible streaming で測った
- dflash request には `chat_template_kwargs = {"enable_thinking": false}` を入れ、全 run で reasoning chunks が 0 であることを確認した

### 詰まったこと・解決したこと
- Tomoko 風 10k prompt では LM Studio 26B / 31B が HTTP 200 を返すが `delta.content` を 1 件も返さなかった
- 3k 程度の中長文 prompt では LM Studio も正常に返したため、10k prompt は LM Studio 側の context / prompt 処理限界または空完了として扱った
- dflash は cold first run が重いが、run 2-3 では prefix cache が効き、長文の first content が 0.25-0.48 秒台まで落ちた

### 検証
- artifacts
  - `logs/llm-lmstudio-dflash-short-long-20260607.json`
  - `logs/llm-lmstudio-dflash-medium-20260607.json`
- warm short prompt, run 2-3 average
  - LM Studio 26B: first 72.4ms / total 73.0ms
  - dflash 26B: first 223.4ms / total 350.4ms
  - LM Studio 31B: first 820.5ms / total 821.8ms
  - dflash 31B: first 290.5ms / total 1028.9ms
- warm medium Tomoko-like prompt, run 2-3 average
  - LM Studio 26B: first 879.6ms / total 1839.4ms
  - dflash 26B: first 341.2ms / total 2903.0ms
  - LM Studio 31B: first 4803.6ms / total 13033.4ms
  - dflash 31B: first 480.7ms / total 10657.3ms
- full Tomoko-like 10k prompt
  - LM Studio 26B / 31B: content chunks 0
  - warmed dflash 26B: first 251.7ms / total 3541.3ms
  - warmed dflash 31B: first 363.3ms / total 9402.0ms

### 次のセッションでやること
- 実 `make server-debug` で、dflash 長文 first content 改善が first audio まで効くか確認する

## 2026-06-07 セッション4

### やること（開始時に書く）
- dflash 用に Tomoko の persona/system prompt prefix を起動時 warm-up する
- 26B / 31B の dflash backend それぞれへ no-think のまま prefix cache を作る
- unit / ruff を通し、commit / push して作業ツリーを整理する

### やったこと
- startup warm-up に dflash prompt prefix warm-up を追加した
- 対象は `type="lm_studio"` かつ `chat_template_kwargs.enable_thinking=false` の backend に限定した
- conversation / session_summary / memory_extraction / persona_update / candidate_gen / diary の configured backend を見て、同じ backend は一度だけ warm-up するようにした
- warm-up prompt は `ThinkFastMode.system_prompt` の固定 persona / overlay prefix と短い user message にし、`max_tokens=4` で返答生成を最小化した
- dflash prompt prefix warm-up 失敗時は `logger.exception` で記録し、server startup 自体は継続するようにした

### 詰まったこと・解決したこと
- 既存の `LMStudioBackend.warm_up()` は短い疎通 prompt なので、Tomoko の長い persona prefix cache 作りには不足していた
- backend 実装側ではなく startup adapter 側に追加 warm-up を置き、通常 LM Studio backend や他 backend には影響しないようにした

### 検証
- red test: `uv run pytest -m unit tests/unit/test_startup_warmup.py::test_startup_warms_dflash_prompt_prefix_for_unique_no_think_backends -q`
  - prefix warm-up 未実装のため failure
- focused unit: `uv run pytest -m unit tests/unit/test_startup_warmup.py tests/unit/test_lm_studio_backend.py tests/unit/test_phase0_config.py -q`
  - 17 passed
- focused ruff: `uv run ruff check server/edge/main.py tests/unit/test_startup_warmup.py`
  - pass
- full unit: `uv run pytest -m unit -q`
  - 633 passed, 23 deselected

### 次のセッションでやること
- dflash 26B / 31B 起動直後の `make server-debug` で startup warm-up log と初回実会話 first content / first audio を確認する

## 2026-06-07 セッション5

### やること（開始時に書く）
- OpenAI-compatible prompt の固定 prefix を伸ばし、dflash の prompt cache が効きやすい構造へ変える
- `_tools/run_llm.sh` / `_tools/run_llm_stop.sh` を使って dflash を再起動し、生 OpenAI-compatible call で反応速度差を測る
- まずは実ブラウザではなく direct raw call で、有意な差が出るか確認する

### やったこと
- `_tools/run_llm.sh` / `_tools/run_llm_stop.sh` の dflash 31B=8081 / 26B=8082 screen 起動・停止手順を確認した
- 変更前相当の raw prompt と、固定 context usage rules を persona 直後に寄せた proposed prompt を direct call で比較した
- `ThinkFastMode.system_prompt` に `STATIC CONTEXT USAGE RULES` を追加し、startup warm-up が persona / overlay だけでなく固定 context rules まで含めるようにした
- `CURRENT LOCAL TIME` / `CALENDAR CONTEXT` / `RESEARCH CONTEXT` / `TASK CONTEXT` の繰り返し説明文を固定 rules 側へ移し、動的ブロック側は見出しと実データ中心にした
- prompt order test を追加し、`STATIC CONTEXT USAGE RULES` が `CURRENT LOCAL TIME` より前に来ることを固定した

### 詰まったこと・解決したこと
- 26B の手組み raw call では、warm-up 後初回 medium call が baseline first 5781.8ms / total 8040.5ms、proposed first 4535.2ms / total 6394.1ms で改善した
- ただし実装後の `ThinkFastMode` prompt では、再起動後初回 medium call が first 5924.1ms / total 8142.2ms、再測で first 4857.2ms / total 7259.2ms となり、baseline に対して小幅改善から同等程度だった
- 31B の単発 raw call は baseline first 25103.4ms / total 30826.2ms、proposed first 28152.6ms / total 34293.2ms で、ばらつきが大きく有意な改善とは言えなかった
- 2回目以降は full prompt 自体が dflash cache に載るため、構造差より full prompt cache hit が支配的になり、26B first は 0.22〜0.35 秒台まで落ちた

### 検証
- raw artifacts
  - `logs/dflash-prefix-raw-before-20260607.json`
  - `logs/dflash-prefix-raw-31b-before-20260607.json`
  - `logs/dflash-prefix-after-code-26b-20260607.json`
  - `logs/dflash-prefix-after-code-26b-repeat-20260607.json`
- dflash restart
  - `./_tools/run_llm_stop.sh && ./_tools/run_llm.sh`
  - 8082 / 8081 `/v1/models` readiness confirmed
- focused unit / ruff
  - `uv run pytest -m unit tests/unit/test_phase4_thinking.py tests/unit/test_startup_warmup.py -q`
  - 23 passed
  - `uv run ruff check server/gateway/thinking/fast.py tests/unit/test_phase4_thinking.py`
  - pass

### 次のセッションでやること
- 実 `make server-debug` 起動時の startup warm-up log と、初回 live conversation の first content / first audio を確認する
- 31B は複数回 restart の統計を取るまで、prefix 構造変更による改善を断定しない

## 2026-06-07 セッション6

### やること（開始時に書く）
- dflash 26B で、実運用に近い順番に会話が続くサンプルを複数 turn 叩く
- prompt 構造を変えても意味的に壊れないか確認する
- current prompt と、turn metadata / current time / task context を user message 側へ寄せた proposed raw payload の速度を比較する

### やったこと
- `_tools/run_llm_stop.sh && _tools/run_llm.sh` で dflash 26B / 31B を再起動し、26B `http://127.0.0.1:8082/v1/models` の readiness を確認した
- no-think dflash 26B に対して、6 turn のサンプル会話を current 構造で streaming 実測した
- dflash を再起動し、同じ 6 turn を proposed user-side metadata 構造で streaming 実測した
- proposed は system prompt を固定し、turn metadata / current time / task context / current utterance を現在 user message 直前の user content に入れる raw payload とした

### 詰まったこと・解決したこと
- proposed 構造は current より全 turn で first content / total とも速かった
- 6 turn の応答は文脈に沿っており、意味崩壊や人格崩壊は見られなかった
- ただし raw payload シミュレーションであり、Tomoko 実装として dynamic metadata を user 側へ移したわけではない
- 両構造とも短い follow-up question を返す傾向はあり、会話方針の調整は速度検証とは別に扱う

### 検証
- artifacts
  - `logs/dflash-26b-conversation-sim-current-20260607.json`
  - `logs/dflash-26b-conversation-sim-proposed-user-metadata-20260607.json`
- current structure
  - avg first content: 3671.6ms
  - avg total: 6056.7ms
  - first content min/max: 3233.2ms / 4288.1ms
- proposed user-side metadata structure
  - avg first content: 3053.7ms
  - avg total: 5178.0ms
  - first content min/max: 2820.9ms / 3264.6ms
- proposed - current
  - avg first content: -618.0ms
  - avg total: -878.8ms
  - per-turn first content delta: -1023.5ms, -605.5ms, -345.7ms, -503.8ms, -412.3ms, -817.0ms

### 次のセッションでやること
- dynamic turn metadata / current time / task context を実装上も current user message 側へ移すか判断する
- 実装する場合は `ThinkFastMode` の prompt assembly test を先に追加し、`make server-debug` で first audio まで確認する

## 2026-06-07 セッション7

### やること（開始時に書く）
- 本番 `ThinkFastMode` / 実 config / 実 PostgreSQL context / 実 dflash 26B で、変更前の連続会話速度と応答品質を測る
- dynamic turn metadata / current time / task context を本番コードで current user message 側へ移す
- 同じ実プログラム・実DB・実推論の連続会話を再測し、意味崩壊がないか、速度が上がったかを `_docs/latency.md` に記録する

### やったこと
- `_tools/bench_runtime_thinkfast_conversation.py` を追加し、`config/central_realtime.toml` / `InferenceRouter` / `ContextSnapshotBuilder` / 実 PostgreSQL store / 実 dflash 26B を使う6 turn simulation を作った
- script は専用 `conversation_sessions` を作り、各 turn の user / tomoko turn を `conversation_logs` に保存して、次 turn が実DB contextを読めるようにした
- 実装前 current `ThinkFastMode` を dflash 再起動後に測定した
- `ThinkFastMode` の system prompt を persona / overlay / `STATIC CONTEXT USAGE RULES` の固定 prefix にし、CURRENT LOCAL TIME / response directive / persona slice / calendar / research / task / memory / current utterance を最後の user message に移した
- 実装後も dflash を再起動し、同じ script / 同じ6 turn で再測定した

### 詰まったこと・解決したこと
- `_tools` script は repo root を `sys.path` に足す必要があり、script 先頭で明示した
- static rules には `CALENDAR CONTEXT` などの名称自体が残るため、unit test は dynamic header `## CALENDAR CONTEXT` が system に無いことを確認する形に直した
- after は平均 first / total とも改善したが、turn 4 / 6 では total が遅くなったため、個別 turn は dflash 側の生成揺れが残ると判断した
- 応答内容は全 turn で話題に沿っており、人格崩壊、前後文脈の取り違え、意味破綻は見られなかった

### 検証
- baseline before, dflash restarted
  - artifact: `logs/runtime-thinkfast-26b-before-user-metadata-restarted-20260607.json`
  - avg first content: 5142.1ms
  - avg total: 7715.7ms
  - min/max first content: 3284.4ms / 8158.7ms
- after user-side metadata, dflash restarted
  - artifact: `logs/runtime-thinkfast-26b-after-user-metadata-restarted-20260607.json`
  - avg first content: 4697.1ms
  - avg total: 7064.3ms
  - min/max first content: 3420.0ms / 8358.5ms
- after - before
  - avg first content: -445.0ms
  - avg total: -651.4ms
  - per-turn first content delta: +199.8ms, -1383.3ms, -1286.6ms, +88.2ms, -423.7ms, +135.6ms
  - per-turn total delta: -1548.7ms, -2370.0ms, -2936.5ms, +2114.0ms, -246.8ms, +1079.4ms

### 次のセッションでやること
- `make server-debug` の実ブラウザ会話で LLM first content 改善が first audio まで効くか確認する
- turn 4 / 6 のような total latency の揺れが TTS 前の生成長・dflash scheduling・prompt cache miss のどれかを backend trace で切り分ける
