# f.md — 完成目標・ギャップ・人間なしで進めるステップ計画

作成日: 2026-07-03
根拠: README.md / ARCHITECTURE.md / PLAN.md / LOG.md / task.md / memo.md / _docs/latency.md

## 1. 完成目標の確認

Tomoko v2 の完成目標は「計算モデルを持って会話する存在」である。具体的には:

1. **ターン概念を持たない任意タイミング発話**
   ユーザー発話への応答・自発発話・カレンダー通知・言い直し・停止を、すべて同じ
   `materials -> pressures -> LlmFireGate -> LLM -> PreparedSpeechCandidate -> SpeechEmissionGate -> SpeechOrder`
   経路で出す。hot-path は人格判断を持たず speech-order を物理実行するだけ。
2. **体感レイテンシー目標(S16 完了条件)**
   first audio がユーザー発話終了前、または終了直後の目標範囲に入る。
   現状は 860ms(clean)〜5800ms(final 起点)と不安定で、ここが最大の未達成項目。
3. **上書き可能な前のめり会話**
   意味飽和度はアクセル、上書き(replace/stop)はブレーキ。partial 起点で早期発話し、
   final STT との乖離や user overlap で破綻なく言い直せる。
4. **周辺プロセスによる文脈供給**
   think(candidate) / info-aquire(calendar, world) / user-status(presence, OCR) /
   summary(要約・embedding) が pressure の材料を供給する。
5. **中長期到達点: 「口喧嘩できる Tomoko」**
   motivation を「閾値を動かす圧力」として設計し、semantic が低くても motivation が
   高ければ短く撤回可能な割り込みを出す。残る中心課題は motivation の設計と明記されている。

## 2. 現状とギャップ

### 完了している主線(再実装しない)

- PLAN S1〜S8, S12〜S15, S17〜S21: DTO / SemanticSaturationJudge / pressure model /
  二段 gate / speech-order executor / DB split / WS-origin control plane /
  prefix-cache prompt / E2B semantic early-start / MaAI 相槌 lane / VAP p_yielding lane。
- fake・real 両方の smoke 群(`make v2-*-smoke`)、scheduler report、score_breakdown の DB 保存。
- `scripts/v2_say_latency_smoke.py --input-wav` により、録音/合成 WAV を `/ws` に流せる。

### ギャップ一覧(優先度順)

| # | ギャップ | 出典 |
|---|---|---|
| G1 | S16: first audio が目標範囲に入らない(不安定) | PLAN S16 未チェック |
| G2 | S11: final STT が partial とずれた場合の speech-order 上書き | PLAN S11 未チェック |
| G3 | S9: replace 時の短い無音/fade、発話途中上書きの live 確認 | PLAN S9 未チェック |
| G4 | S10: calendar append の scenario smoke(fake/live) | PLAN S10 未チェック |
| G5 | VAP p_yielding が turn_opportunity に効く場面を silence fallback と切り分けて確認 | LOG 2026-06-22 |
| G6 | motivation の設計・実装(閾値を動かす圧力、前のめり割り込み) | ARCHITECTURE 未来メモ |
| G7 | think-process が実質スタブ(candidate 生成なし) | server/think 56行 |
| G8 | info-aquire / summary / user-status の pressure 結線が最小 | server/* スタブ |
| G9 | task.md「Phase 6.5: AttentionMode / 会話と聞き取りの自然遷移」未着手 | task.md |
| G10 | internal WS 8765 のローカル競合の恒久対応 | LOG 2026-06-22 |

## 3. 人間なしで進めるための原則

人間による物理デバッグ(マイクに向かって話す・耳で聞く)を、以下の 5 つで代替する。

1. **合成音声リプレイ**: `say -v Kyoko` で seed 発話を WAV 化し
   `scripts/v2_say_latency_smoke.py --input-wav` 系で `/ws` に流す。
   ぶつ切り・ポーズ挿入・被せ再生もプログラムで合成する(memo.md のシャドウベンチ構想)。
2. **artifact assertion**: smoke が出す JSON artifact(`logs/*.json`)の
   `scheduler_decision` / `speech_order` / `score_breakdown` / `binary_audio` 件数 /
   latency を assert する。耳の代わりに「音声イベント列」を検証する。
3. **DB assertion**: `v2_stt_observations` / `v2_speech_scheduler_decisions` /
   `v2_speech_orders` / `v2_utterances` を integration test で検証する。
4. **統計レポート**: 単発 smoke ではなく N 回実行して p50/p95 を出し、
   目標値との差分をレポート化する(`make v2-scheduler-report` の拡張)。
5. **LLM-as-judge(補助)**: 会話 transcript の自然さ・重複・取りこぼしを
   E2B/31B に 0..1 スコアで判定させ、人間の主観評価の一次近似にする。

各ステップの完了条件は必ず「コマンドが exit 0」で判定できる形にする。
毎ステップ共通: `make check`(ruff + unit)、`LOG.md` 追記、`_docs/latency.md` 追記(latency 影響時)。

## 4. ステップバイステップ計画

### Step 0: シナリオ・リプレイハーネスを一本化する(基盤)

すべての後続ステップの検証器を先に作る。

- [x] `scripts/seeds/` に日本語 seed 発話コーパス(応答要求・雑談・言い淀み・stop 系・
      「ただ/でも/というか」系の意味反転文)を 100〜500 本置く。→ `scripts/seeds/utterances.txt` 約150本
- [x] シナリオ定義(JSON)を導入する。1 シナリオ = イベント列:
      `{say: "...", cut_at_ratio, pause_ms, overlap_during_playback, expect: {...}}`
      → `scripts/scenarios/*.json`、step 単位の `expect` window assertion も対応
- [x] `scripts/v2_scenario_replay.py` を作る。say で WAV 合成 → ぶつ切り/ポーズ/被せを
      適用 → `/ws` に送信 → artifact を保存 → `expect` を assert して exit code を返す。
- [x] `make v2-scenario-replay SCENARIO=...` と、全シナリオ一括の `make v2-scenario-suite` を追加する。
- [x] fake runtime と real runtime(`make run` 起動済み)の両モードで動くようにする。
      (real モードは Step 1 以降の real シナリオで実地検証する)

完了条件:
- [x] fake runtime で `make v2-scenario-suite` が exit 0。(2026-07-03, basic-reply / two-turn-reply)
- [x] 代表シナリオ 1 本の artifact に `speech_order` / `score_breakdown` / latency が残る。
      (`logs/scenario-basic-reply-20260703-234953.json`)

### Step 1: G2 — final STT divergence の上書き(PLAN S11 残)

- [x] 失敗する unit test: partial 起点 speech-order 後、normalize 済み final が
      partial と大きく乖離した場合、reconcile suppress ではなく
      `replace_current` の新 speech-order を出す。
      → `test_tomoko_conversation_core_replaces_conflicting_final_after_partial_order`
- [x] `TomokoConversationCore` の reconcile 判定に「乖離時は上書き」分岐を実装する。
      → divergent final は reconcile せず通常経路へ落とし、emission 後に
      `final diverged from active partial reply; replacing` で replace_current を強制。
      乖離 final 処理時に active partial 状態をクリアし、以後の stale partial は
      final と照合して reconcile suppress する。
- [x] リプレイシナリオ: fake STT イベント列(partial×2 + 乖離 final)を
      `TOMOKO_V2_FAKE_STT_EVENTS` で注入する `final-divergence` シナリオを追加。
      partial 起点 speech_order -> 乖離 final の replace_current を
      WS split プロセス境界越しに assert。(real runtime での cut WAV 版は Step 4 で実施)

完了条件:
- [x] focused unit + `make v2-scenario-replay SCENARIO=final-divergence` が exit 0。(2026-07-03)
- [x] PLAN S11 の未チェック 2 項目にチェックを入れられる。→ チェック済み

### Step 2: G3 — overlap / replace の仕上げ(PLAN S9 残)

- [x] `replace_current` 実行時に短い無音または fade marker を挟む実装。
      → client/main.js: replace_current 受信で 80ms fade + 150ms 無音 gap 後に新音声
      (物理 playback 制御なので client 側が正しい置き場所)
- [x] リプレイシナリオ: `overlap-stop`(被せ stop intent -> stop order、audio 0件)/
      `overlap-replace`(被せ質問 -> replace_current)を追加し fake WS split で PASS。
- [x] 副産物のバグ修正 2 件(いずれも unit test 先行):
      1. WS split で stop が `cancel_order` のまま実行されず物理 stop されない
         → `stop_order_from_cancel_event()` で実行可能な STOP order に変換
      2. tomoko core の `current_speech_order` が playback 終了後も残り、
         次の返答が append になる → `playback_state` 受信で core をクリア

完了条件:
- [x] fake WS split で overlap-stop / overlap-replace が exit 0。(2026-07-04)
- [x] `make v2-scenario-replay SCENARIO=overlap-replace` が real runtime で exit 0。
      2026-07-04: `make autopilot` 内の real check で
      `logs/scenario-real-overlap-replace-20260704-044127.json` が PASS。

### Step 3: G4 — calendar append シナリオ(PLAN S10 残)

- [x] fixture: DB テーブルではなく `TOMOKO_V2_FAKE_CALENDAR`(offset_min/title の JSON)で
      calendar materials を realtime core に注入する方式に変更。
      DB -> core の calendar 読み込みは info-aquire 結線(Step 7)で行うため、
      Step 3 では provider 注入で「返答 -> 予定通知」経路そのものを検証する。
- [x] 実装: `server/tomoko/calendar.py`(urgency 計算 / 直近予定選択 / 通知文 template)、
      `TomokoConversationCore` に calendar_items_provider と final 返答後の
      `_maybe_calendar_followup()`(閾値 0.6、通知済み key は dedupe)、
      `TomokoConversationResult.followup_orders`、realtime -> ws_control -> hot-path の
      followup speech_order 伝搬(ack に `order_count` を入れて hot path に余分な待ちを入れない)。
- [x] リプレイシナリオ `calendar-append`: 返答 replace_current -> append_after_current、
      audio 2 発話分、reason に calendar 由来を assert。全 PASS。

完了条件:
- [x] `make v2-scenario-replay SCENARIO=calendar-append` が exit 0。(2026-07-04)
      PLAN S10 は live 確認(Step 4 real runtime)以外チェック済み。

### Step 4: G1 — latency 回帰スイートと S16 の詰め(最重要)

- [x] `scripts/v2_latency_suite.py`: seed から 10 本 × 3 回を replay し、
      voice-end→first audio の p50/p95、partial 起点率、reconcile 率、
      false-early 率を JSON + Markdown レポートに出す。`make v2-latency-suite` を追加。
- [x] 目標値を明文化する(例: partial 起点時 p50 ≤ 800ms、final 起点時 p50 ≤ 1500ms、
      p95 ≤ 2500ms)。レポートが目標超過なら exit 非 0 にして回帰ゲート化する。
- [x] 改善レバーを 1 つずつ計測しながら入れる(各レバーごとに suite を回して前後比較):
  - 蒸留 saturation scorer(make-model 成果物)を partial gate の既定にして E2B 呼び出しを減らす。
  - partial lane の coalesce 間隔・`stream_interval_ms` の調整。
  - VOICEVOX 分割発話(先頭短文だけ先に合成)で first audio を前倒しする。
  - dflash prefix cache のヒット率を artifact で確認し、prompt を崩す変更を検知する。
  - 2026-07-04: request-complete partial の初回 confirmation fast-start を追加。
    ただし real 3-seed suite は partial 起点 0/3 のままで、S16 目標には未到達。
  - 2026-07-04: `latency_stage` event と stage p50/p95 summary を追加。
    最新 1-seed artifact では STT 337.3ms に対し Tomoko/LLM 3480.7ms、TTS 3676.3ms。
  - 2026-07-04: suite が各 run 前に `latency_control/reset_conversation` を送るようにした。
    reset 後の 1-seed artifact でも final-origin 6310.7ms で、主因は Tomoko/LLM 3078.5ms + TTS 2232.8ms。
  - 2026-07-04: speech-order の TTS を `HotPathConversationResult` 生成中に待たず、
    WebSocket sender 側で `SpeechOrderExecutor.execute_stream()` から逐次 binary audio を送るようにした。
    deferred-only artifact `logs/latency-suite-20260704-011937.{json,md}` は
    final-origin 5076.1ms、Tomoko/LLM 2182.8ms、order→first audio 2133.8ms。
  - 2026-07-04: VOICEVOX 合成前に speech-order text を文単位に分割し、先頭短文を先に合成するようにした。
    artifact `logs/latency-suite-20260704-012122.{json,md}` は final-origin 3425.6ms、
    order→first audio 861.8ms、binary audio 3 chunks。3-seed artifact
    `logs/latency-suite-20260704-012148.{json,md}` は final-origin p50 4490.2ms / p95 5728.1ms。
  - 2026-07-04: Tomoko LLM stream を最初の完全文で打ち切り、speech-order を first sentence にするようにした。
    stale internal WS で no-audio/ConnectionClosedError が出たため、`RemoteTomokoWsCore` は
    reset / observation で closed socket を捨てて 1 回 reconnect するようにした。
    stable artifact `logs/latency-suite-20260704-012939.{json,md}` は final-origin 2627.4ms、
    STT 334.3ms、Tomoko/LLM 742.4ms、order→first audio 1114.1ms。
    最新 3-seed artifact `logs/latency-suite-20260704-013026.{json,md}` は no-audio 0、
    partial-origin 0/3、final-origin p50 3122.4ms / p95 5143.9ms、
    stage total p50 1470.3ms / p95 2679.2ms。S16 は未達。
  - 2026-07-04: 「よくうまく動く」ことを優先し、未完了 topic partial には full answer ではなく
    短い acknowledgement speech-order (`うん、聞いてるよ。`) を出すようにした。
    final STT で本回答へ replace できるよう、active partial ack は final reconcile 対象にしない。
  - 2026-07-04: 長い一文の speech-order は文末だけでなく、一定長以上の読点でも TTS 分割するようにした。
    天気返答のような長い一文でも first chunk を先に送れる。
  - 2026-07-04: `今何時` / `今いつ` 系は LLM に渡さず local system time の direct speech にした。
    古い日付を LLM が返す問題を避け、Tomoko stage は約 19ms まで短縮。
  - 2026-07-04: `latency_control/reset_conversation` は Tomoko state だけでなく、
    hot-path の VAD / streaming STT / active trace も reset するようにした。
    first-audio で測定を切った後の Apple Speech 遅延イベントが次 run に混ざる問題を解消。
  - 2026-07-04: Step 9 の full autopilot で 10 seeds × 3 repeats まで拡張し、
    `logs/latency-suite-20260704-044312.{json,md}` が `runs_no_audio=0`、
    final-origin p50 1339.7ms / p95 1343.2ms、partial-origin p50 -163.8ms で PASS。
- [x] 目標に入ったら PLAN S16 の最終項目にチェックを入れ、`_docs/latency.md` に記録する。

完了条件:
- [x] `make v2-latency-suite` が real runtime で目標値内で exit 0(3 回連続)。
      2026-07-04 実測では `logs/latency-suite-20260704-005033.{json,md}` が
      no-audio 0 で完走したが、final-origin p50 7298.7ms / p95 14026.4ms で exit 2。
      stage breakdown 追加後の `logs/latency-suite-20260704-010457.{json,md}` も
      no-audio 0 だが final-origin 7938.6ms で exit 2。
      reset つきの `logs/latency-suite-20260704-011004.{json,md}` は
      no-audio 0、final-origin 6310.7ms で exit 2。
      partial acknowledgement + direct clock + full hot-path reset 後は 3 回連続で exit 0:
      `logs/latency-suite-20260704-015746.{json,md}` は partial-origin 2/3、
      all p50 447.9ms / p95 1236.2ms、final-origin 1323.7ms、partial-origin p50 384.6ms。
      `logs/latency-suite-20260704-015947.{json,md}` は partial-origin 2/3、
      final-origin 1316.3ms、partial-origin p50 372.9ms。
      `logs/latency-suite-20260704-020002.{json,md}` は partial-origin 2/3、
      final-origin 1320.7ms、partial-origin p50 375.9ms。

### Step 5: G5 — VAP p_yielding の効果切り分け

- [x] リプレイシナリオ: 無音を silence fallback 閾値未満に抑えた状態で発話を終える
      scripted STT event を作り、`turn_opportunity` が p_yielding 由来で上がるかを
      score_breakdown の系列で assert する。
      `scripts/scenarios/vap-yielding-opportunity.json` は partial decision で
      `yielding=0.92 / silence=0.0 / from_yielding=1.0`、
      final decision で `yielding=0.0 / silence=1.0 / from_silence=1.0` を同一 artifact 内で assert。
- [x] say 音声では韻律が単調で p_yielding が立ちにくい場合は、
      `_reference/` の実録音 WAV を corpus に加える(既存 test.m4a 方式)。
      今回は deterministic regression を優先し、scripted `p_yielding` で代替したため
      実録音 corpus 追加は不要。
- [x] 効かない場合は重み(`maai_weight` 相当)の調整または VAP 入力の平滑化を検討し、
      結果を MEMORY.md に記録する。
      2026-07-04 時点では p_yielding / silence fallback の切り分けが効いたため、重み調整は不要。

完了条件:
- [x] silence fallback と p_yielding を区別した検証結果が artifact + LOG.md に残る。
      `logs/scenario-vap-yielding-opportunity-20260704-021212.json` 単体 PASS。
      `make v2-scenario-suite` でも `logs/scenario-vap-yielding-opportunity-20260704-021449.json` PASS。

### Step 6: G6 — motivation の設計と実装

ARCHITECTURE の未来メモを実装に落とす。人間の体感調整は後回しにし、
まず「閾値を動かす圧力」としての機構と観測性を作る。

- [x] `MotivationPressure` を拡張: 会話熱量(直近発話密度)、話題継続度、
      personality(talkativeness/curiosity/restraint) から motivation スカラーを計算する。
- [x] `LlmFireGate` / `SpeechEmissionGate` の閾値を motivation で動的にシフトする
      (`semantic medium + motivation high -> 前のめり fire` の表を unit test で固定)。
- [x] 低 semantic 発話は「短い撤回可能な割り込み」プロンプト(concise/interjection)に
      切り替える。
- [x] リプレイシナリオ: 同一音声列で motivation low/high を環境変数で切り替え、
      fire タイミングと発話長が変わることを artifact 比較で assert。
- [x] hot-path には motivation そのものではなく threshold profile だけを渡す
      (ARCHITECTURE の責務分担を維持)。
      2026-07-04 実装では motivation 自体を hot-path に渡さず、Tomoko 側で
      `threshold_shift` を `score_breakdown` / `speech_order` に落として hot-path は物理実行だけを担う。

完了条件:
- [x] motivation による閾値シフトの unit test 一式が通る。
      `uv run pytest -m unit tests/unit/test_v2_semantic_scheduler.py tests/unit/test_v2_speech_order_flow.py tests/unit/test_v2_scenario_replay.py tests/unit/test_v2_internal_ws.py -q`
      は 70 passed。
- [x] A/B リプレイ比較レポートが `logs/` に残る。
      high/low threshold A/B は `logs/scenario-motivation-threshold-high-20260704-023203.json` /
      `logs/scenario-motivation-threshold-low-20260704-023204.json`。
      low semantic high motivation の短い interjection は
      `logs/scenario-motivation-interjection-high-20260704-023202.json` で
      text=`いや、それってさ。`、reason=`motivation interjection before complete request`、
      `motivation_interjection=1.0` を assert。
      `make v2-scenario-suite` と `make check` も PASS(ruff passed, unit 195 passed / 1 deselected)。

### Step 7: G7/G8 — 周辺プロセスの実体化(candidate / presence / summary)

すべて fake data + integration test で人間なしに検証できる。

- [x] user-status: presence(在席/不在) を `WorldMaterials`/pressure に結線し、
      不在時は自発発話を suppress する unit + integration test。OCR は既存 sidecar を使い、
      スクリーンショットは fixture 画像で回す。
      2026-07-04: presence を `WorldMaterials.user_present` /
      `WorldPressure.user_presence,user_absence` に結線し、prompt snapshot の
      `user_status` にも反映。`TOMOKO_V2_FAKE_USER_STATUS` と internal WS
      `user_status` event を追加した。不在時は calendar followup append を suppress。
      replay artifact は `logs/scenario-user-status-absent-pressure-20260704-024009.json`。
      2026-07-04 追加: 純粋な `initiative_tick` でも user absent の場合は suppress する
      public `/ws` replay を追加し、`logs/scenario-candidate-initiative-absent-suppressed-20260704-025845.json` と
      suite 最新 `logs/scenario-candidate-initiative-absent-suppressed-20260704-025853.json` で
      speech_order / prompt_complete が出ないことを assert。残りは OCR fixture 画像を使った integration test。
      2026-07-04 追加2: `observation_from_ocr_artifact()` を追加し、fixture 画像 path ->
      `ocr_text()` -> `UserStatusObservation` の経路を unit で固定した。
      `make check` は 218 passed / 2 deselected。残りは実 OCR sidecar を使う画像 integration。
      2026-07-04 追加3: 実 OCR sidecar/tesseract を使う fixture image integration を追加し、
      `make test-integration` で PASS。
- [x] summary-process: セッション close 時に「キーワード + 結論 1 文」要約と embedding を
      DB に書く。fixture 会話ログからの integration test。
      2026-07-04: deterministic `summarize_session()` の fixture test と、
      `insert_session_summary_sql()` / `insert_summary_embedding_sql()` を追加。
      `tests/integration/test_v2_db_schema.py` に summary / embedding insert を追加した
      (`TEST_DATABASE_URL` 未設定では skip)。`make check` は 203 passed / 1 deselected。
      2026-07-04 追加: `materialize_summaries_from_db()` を追加し、ended session かつ
      未要約の `v2_conversation_sessions` から utterances を読んで
      `v2_session_summaries` / `v2_summary_embeddings` に書く runner を作った。
      `server.runtime process summary` の heartbeat tick にも結線。
      `make check` は 216 passed / 1 deselected。
      残りは `TEST_DATABASE_URL` ありの実 DB integration と、session close を発生させる
      runtime smoke での確認。
      2026-07-04 追加2: DB materializer chain integration を追加し、
      `make test-integration` で summary -> candidate まで PASS。
- [x] think-process: summary/embedding と calendar/world info を突き合わせて
      candidate を積む最小実装。candidate_pressure が gates に効くことを
      リプレイシナリオ(無音が続く → initiative 発話)で assert。
      2026-07-04: `candidate_provider` を `TomokoConversationCore` に追加し、
      active `CandidateRecord` を prompt snapshot の `VOLATILE_RECALL` と
      `WorldMaterials.candidate_pressure` / `WorldPressure.candidate_pressure` に結線。
      fake runtime 用に `TOMOKO_V2_FAKE_CANDIDATES` / scenario JSON `fake_candidates` を追加し、
      `logs/scenario-candidate-pressure-context-20260704-024648.json` で
      `pressure_world_candidate_pressure=0.9` を assert。
      2026-07-04 追加: Tomoko-owned `initiative_tick` を public `/ws` と internal WS に追加し、
      `TomokoConversationCore.handle_initiative_tick()` が active candidate + silence から
      `PromptScope.INITIATIVE` の short prompt と speech-order を作るようにした。
      `scripts/scenarios/candidate-initiative-silence.json` で transcript なしに
      `speech_order(reason=candidate pressure initiative tick)` と TTS が出ることを assert。
      artifact は `logs/scenario-candidate-initiative-silence-20260704-025714.json`、
      suite 最新は `logs/scenario-candidate-initiative-silence-20260704-025854.json`。
      2026-07-04 追加2: `server.think.main` に `build_candidates()` /
      `calendar_item_seeds()` / `summary_memory_seed()` / `world_info_seed()` を追加し、
      summary / calendar / world info を `CandidateSeed` -> `CandidateRecord` に正規化する
      pure helper を作った。fake runtime では `TOMOKO_V2_FAKE_CALENDAR` と
      `TOMOKO_V2_FAKE_WORLD_INFO` から candidate を生成して provider に積む。
      `scripts/scenarios/calendar-initiative-silence.json` と
      `scripts/scenarios/world-info-initiative-silence.json` で、明示 `fake_candidates` なしでも
      calendar/world info 由来の initiative 発話が出ることを assert。
      artifacts は `logs/scenario-calendar-initiative-silence-20260704-030428.json` /
      `logs/scenario-world-info-initiative-silence-20260704-030523.json`、
      suite 最新は `logs/scenario-calendar-initiative-silence-20260704-030553.json` /
      `logs/scenario-world-info-initiative-silence-20260704-030610.json`。
      2026-07-04 追加3: `insert_candidate_sql()` と `candidate_from_row()` を追加し、
      `CandidateRecord` を `v2_candidates` へ `source/source_key` で upsert して戻せる
      DB bridge を作った。`tests/integration/test_v2_db_schema.py` に
      `v2_candidates` insert を追加した (`TEST_DATABASE_URL` 未設定では skip)。
      `assign_conversation_session()` の open session 再利用 path が
      `last_activity_at` を更新する regression test も追加。
      2026-07-04 追加4: Tomoko realtime が `TOMOKO_V2_DB_CANDIDATES=1` のとき
      `v2_candidates` の active rows を TTL 付きで読み、`TomokoConversationCore.update_candidate_records()`
      経由で prompt/gate の candidate_provider に合流させる read bridge を追加した。
      `make v2-tomoko` はこの env を渡す。fake runtime は DB 読みを無効のまま保つ。
      `make check` は 214 passed / 1 deselected、`make v2-scenario-suite` も PASS。
      2026-07-04 追加5: `select_recent_session_summaries_sql()` /
      `select_recent_world_interpretations_sql()` と `materialize_candidates_from_db()` を追加し、
      think-process が DB の session summary / world interpretation rows から
      `build_candidates()` -> `insert_candidate_sql()` で `v2_candidates` に upsert できる
      write bridge を作った。`server.runtime process think` の heartbeat tick にも結線。
      `make check` は 215 passed / 1 deselected。
      2026-07-04 追加6: info-aquire 側で fake calendar/world fixture を
      `v2_world_*` rows に materialize できるようにしたため、
      summary/world -> candidate -> Tomoko realtime の DB chain は unit で一通り結線済み。
      残りは `TEST_DATABASE_URL` ありの実 DB integration と、OCR fixture 画像の integration。
      2026-07-04 追加7: `TEST_DATABASE_URL` ありの DB chain integration が
      `make test-integration` で PASS。
- [x] info-aquire: calendar 取り込みは fake JSON fixture から DB へ。実 Google Calendar /
      Chrome 経由調査は人間確認が要るため後段(§5)へ回す。
      2026-07-04: `materialize_info_fixtures_from_payloads()` /
      `materialize_info_fixtures_from_env()` を追加し、`TOMOKO_V2_FAKE_CALENDAR` /
      `TOMOKO_V2_FAKE_WORLD_INFO` を deterministic UUID の
      `v2_world_documents` / `v2_world_items` / `v2_world_interpretations` に upsert する
      入口を作った。`server.runtime process info` の heartbeat tick にも結線。
      materializer chain の DB integration test も追加した
      (`TEST_DATABASE_URL` 未設定では skip)。`make check` は 217 passed / 2 deselected。
      2026-07-04 追加: `make test-integration` で DB materializer chain が PASS。

完了条件:
- [x] 各プロセスの integration test が `make test-integration` に入り exit 0。
      2026-07-04: summary -> info -> think -> active candidates の DB chain integration test を追加。
      この環境では `TEST_DATABASE_URL` 未設定のため 2 skipped。
      2026-07-04 追加: `TEST_DATABASE_URL` 既定値を Makefile に追加し、
      local Postgres で `make test-integration` が 3 passed / 218 deselected。
- [x] 「無音 → candidate 由来の自発発話」リプレイシナリオが exit 0。
      `make v2-scenario-replay SCENARIO=candidate-initiative-silence` PASS。
      最新 suite でも `candidate-initiative-silence` / `candidate-initiative-absent-suppressed` が PASS。

### Step 8: G9 — AttentionMode / 会話と聞き取りの自然遷移(task.md Phase 6.5)

- [x] 設計を PLAN.md に Phase として起こす: 呼びかけ(wake)で会話モードへ、
      無音 gap / 「もういいよ」系 stop intent で聞き取りモードへ戻る遷移を、
      新しい状態機械ではなく pressures / gates の閾値プロファイル切り替えとして表現する
      (「ターン概念を持たない」原則を壊さない)。
- [x] モードは tomoko-process が所有し、hot-path へは threshold profile として渡す。
      2026-07-04: `TomokoConversationCore.attention_mode` を tomoko-process 側に置き、
      score_breakdown には `attention_mode_conversation` /
      `attention_mode_ambient` と `attention_ambient_min_saturation` だけを出す。
      hot-path は threshold profile の観測値を物理実行するだけで、状態判定を持たない。
- [x] リプレイシナリオ: 呼びかけ → 応答 → 長い無音 → 独り言(低 saturation)には
      反応しない → 再呼びかけで復帰、を assert。
      2026-07-04: `scripts/scenarios/attention-mode-idle-wake.json` を追加。
      `recommended_silence_ms=10000` の final observation で ambient へ落とし、
      低 saturation 独り言は speech_order / prompt_complete 0、再 wake で復帰することを
      step expect で固定した。

完了条件:
- [x] 遷移の unit test + attention シナリオ replay が exit 0。
      focused unit は 2 passed。
      `make v2-scenario-replay SCENARIO=attention-mode-idle-wake` は
      `logs/scenario-attention-mode-idle-wake-20260704-034352.json` で PASS。
      `make v2-scenario-suite` も AttentionMode を含めて PASS。

### Step 9: 仕上げ — 恒久回帰ゲートと運用

- [x] G10: internal WS 起動時に port listen 元を検査し、他プロセスが 8765 を
      掴んでいる場合は明示エラー + `TOMOKO_INTERNAL_WS_PORT` 案内を出す(既定は変えない)。
      2026-07-04: `server/runtime_ports.py` と
      `server.runtime guard-internal-ws-port` を追加し、`make v2-tomoko` の
      uvicorn 起動前に `0.0.0.0:8765` の listen 元を検査する。
      空き port では `[tomoko:runtime] internal_ws_port_available ...` を出し、
      競合時は PID/command と `TOMOKO_INTERNAL_WS_PORT=<free-port>` の案内を出して
      非 0 exit する。unit で free / occupied port と Makefile 結線を固定。
- [x] `make autopilot`(仮): `make check` → fake `v2-scenario-suite` →
      (runtime 起動済みなら) real suite + `v2-latency-suite` を連続実行する単一ゲート。
      2026-07-04: `scripts/v2_autopilot.py` と Makefile target を追加。
      最新 artifact `logs/autopilot-20260704-044545.json` は `passed: true`。
      内訳は `make check` (236 passed / 3 deselected)、`make test-integration`
      (3 passed / 236 deselected)、`make v2-scenario-suite`、
      real overlap replace/stop、real calendar append、
      `v2-latency-suite LATENCY_SUITE_COUNT=10 LATENCY_SUITE_REPEATS=3` が全て exit 0。
      latency は `runs_no_audio=0`、final-origin p50 1339.7ms / p95 1343.2ms、
      partial-origin p50 -163.8ms。
- [x] LLM-as-judge スクリプト: 直近セッションの transcript を 31B に渡し、
      重複発話・取りこぼし・不自然な割り込みを列挙させて JSONL に残す(調整の材料)。
      2026-07-04: `scripts/v2_llm_judge.py` と `make v2-llm-judge` を追加。
      最新実行は `logs/scenario-calendar-append-20260704-044302.json` を 31B に渡し、
      `logs/llm-judge.jsonl` に `naturalness=1.0`、duplicate/missed/awkward 0 を記録。
      fenced JSON も parse でき、runtime が無ければ skipped JSONL として残す。
- [x] 以上が通った状態を PLAN.md / LOG.md / MEMORY.md に反映する。
      2026-07-04: PLAN Phase S23 / LOG 追加22 / MEMORY Step 9 / `_docs/latency.md` に反映。

## 5. それでも人間が必要なもの(残余)

自動化で潰せないため、まとめて最後に人間へ依頼する項目:

1. **実マイク・実スピーカーでの体感確認**: エコー、被せ時の聴感、fade の自然さ。
   replay は 16kHz PCM 直入れなので、部屋の残響・マイク AGC は再現できない。
2. **weight / threshold の好み調整**: レポート(score_breakdown 分類)を見ながらの
   「too chatty / too quiet」の最終判断。候補プロファイルを 2〜3 個用意して選んでもらう。
3. **実 Google Calendar / Chrome(Perplexity) 連携の認証と実データ確認**。
4. **8765 競合の運用判断**(検出+案内で足りるか、既定 port を変えるか)。
5. **「口喧嘩できる Tomoko」の関係性評価**: 割り込みが不快か楽しいかは人間しか判定できない。
   Step 6 の A/B レポートを見せて判断材料を渡す。

## 6. 実行順序まとめ

```text
Step 0 (ハーネス)
  -> Step 1 (final divergence)   … PLAN S11 完了
  -> Step 2 (overlap/fade)       … PLAN S9 完了
  -> Step 3 (calendar append)    … PLAN S10 完了
  -> Step 4 (latency suite)      … PLAN S16 完了・最重要
  -> Step 5 (p_yielding 検証)
  -> Step 6 (motivation)
  -> Step 7 (周辺プロセス)
  -> Step 8 (AttentionMode)      … task.md Phase 6.5
  -> Step 9 (回帰ゲート・運用)
  -> §5 を人間にまとめて依頼
```

各ステップは「失敗する test を先に書く → 実装 → replay/suite で exit 0 → LOG.md 追記」の
PLAN.md 共通ルールに従う。
