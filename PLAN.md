# PLAN.md

Tomoko 音声対話システムの段階的実装計画。

**鉄則**: 各 Phase が単独で動く状態にしてから次に進む。

---

## マイルストーン一覧

| マイルストーン | 内容 | 目安 |
|---|---|---|
| **M1** | 話せるTomoko | 1〜2週間 |
| **M2** | 記憶があるTomoko | 1〜2週間 |
| **M3** | 自分から話すTomoko | 2〜3週間 |
| **M4** | インフラが安定したTomoko | 2〜3週間 |
| **M5** | 家族のTomoko | 未定 |

**M1が最重要。** M1が動いた瞬間に「面白い」か「思ってたのと違う」かがわかる。
M2以降はM1の感触を見てから設計を見直す余地がある。

---

# M1: 話せるTomoko

**ゴール**: 「トモコ」と呼ぶと声で返事が返ってくる。感情が文字で表示される。
一人・一台・ウェイクワードのみ。

## Phase 0: 環境構築

- [x] Python 3.11+ / uv セットアップ
- [x] docker-compose で PostgreSQL 起動（pgvector / PGroonga 拡張入り）
- [x] Ollama + `qwen2.5:7b` ダウンロード（最初は Ollama で動かす）
- [x] mlx-lm インストール + `mlx-community/Qwen2.5-7B-Instruct-4bit` ダウンロード
  ```bash
  pip install mlx-lm
  python -c "from mlx_lm import load; load('mlx-community/Qwen2.5-7B-Instruct-4bit')"
  ```
- [x] TTS の準備
  - M1フェーズ: macOS `say` コマンドが動くことを確認（追加インストール不要）
    ```bash
    say -v Kyoko "こんにちは、トモコです"
    ```
  - M1完了後に切り替える: `pip install kokoro-mlx misaki[ja]`（今はまだ不要）
- [x] irodori-tts をローカルで起動確認
- [x] faster-whisper の small モデルをダウンロード
- [x] Silero VAD を torch.hub からロードできることを確認
- [x] pytest + pytest-asyncio セットアップ
- [x] `config/central_realtime.toml` を最初の設定として作成
  - 最初は `type = "ollama"` で動かす
  - M1 完了後に `type = "mlx"` に切り替えて `pytest -m perf` で比較

**完了条件**: 上記すべてが個別に動く。`pytest -m unit` が通る。

---

## Phase 1: 最小ループ（エコーバック）

**目標**: マイクの音をサーバーに送り、そのまま返して再生する。配線だけ確認。

- [x] `client/audio-worklet.js`: AudioWorklet で float32 を 32ms チャンクで取得
- [x] `client/main.js`: WebSocket で送信、受信バイナリを AudioContext で再生
- [x] `server/edge/main.py` 初版: `/ws` でバイナリをそのまま送り返す

**完了条件**: 自分の声がエコーで返ってくる。レイテンシーを実測して `docs/latency.md` にメモ。

---

## Phase 2: VAD で発話終了を検出

- [x] `server/edge/pipeline/vad.py`: Silero VAD ラッパー
- [x] TomoroSession 初版
  - state: `idle` / `listening` / `processing`
  - 400ms 連続無音で `processing` に遷移
  - 状態遷移時に `{type: "state"}` を送信
- [x] クライアントで state を表示（デバッグ用）
- [x] `tests/unit/test_vad.py`: 状態遷移のユニットテスト

**完了条件**: 話し始めると "listening"、止まると "processing"。
無音閾値を 300 / 400 / 500ms で実測してメモ。

---

## Phase 3: 常時STT + 参加判断

- [x] `ambient_logs` テーブル作成
- [x] `server/edge/pipeline/stt.py`: 常時 STT、ambient_logs に書く
- [x] `server/edge/participation/base.py`: `ParticipationJudge` 抽象
- [x] `server/edge/participation/wake_word.py`: `WakeWordJudge`
- [x] `tests/unit/test_participation.py`

```python
async def test_wake_word_triggers():
    judge = WakeWordJudge()
    result = await judge.judge(ParticipationContext(transcript="トモコ、今日の天気は？"))
    assert result.should_participate == True

async def test_no_wake_word_stays_observer():
    judge = WakeWordJudge()
    result = await judge.judge(ParticipationContext(transcript="今日いい天気だね"))
    assert result.should_participate == False
```

**完了条件**: 「トモコ」で反応、それ以外は ambient_logs に溜まるだけ。

---

## Phase 4: LLM ストリーミングで返答テキスト

- [x] `server/shared/inference/backends/base.py`: `InferenceBackend` 抽象
- [x] `server/shared/inference/backends/ollama.py`: `OllamaBackend`
- [x] `server/shared/inference/router.py`: `InferenceRouter` 初版
- [x] `server/shared/config.py`: `NodeConfig`（TOML から読む）
- [x] `server/gateway/thinking/base.py`: `ThinkingMode` 抽象
- [x] `server/gateway/thinking/fast.py`: `ThinkFastMode`
- [x] `prompts/base_persona.md`: Tomoko の基本人格
- [x] `{type: "reply_text", delta: ...}` を順次送信
- [x] `tests/unit/test_router.py`

```python
async def test_router_reads_config():
    config = NodeConfig.load("config/central_realtime.toml")
    router = InferenceRouter(config, monitor=MockMonitor())
    backend = await router.select("conversation", "latency")
    assert backend is not None

async def test_privacy_stays_local():
    router = InferenceRouter(
        config=load_config("config/central_realtime.toml"),
        monitor=MockMonitor({"local_qwen7b": InferenceMetrics(latency_ms=600)})
    )
    backend = await router.select("conversation", "privacy")
    assert backend.privacy_allowed == True
```

**完了条件**: 話しかけると文字でストリーミング返答が出る。

---

## Phase 5: TTS ストリーミングで声を出す

**目標**: LLM 返答を句読点単位で TTS に流し、音声チャンクを順次再生する。
**最初は `say` コマンドで動かす。M1完了後に `kokoro-mlx` に切り替える。**

### M1フェーズ: SayBackend

- [x] `server/shared/inference/tts/base.py`: `TTSBackend` 抽象
- [x] `server/shared/inference/tts/say.py`: `SayBackend`
  - macOS `say` コマンドを叩く
  - 初回チャンクまで 10ms 以下、CPU 負荷ほぼゼロ
  - emotion → rate のマッピングで簡易感情表現
- [x] session で LLM トークンを蓄積、句読点（。！？）で TTS に流す
- [x] 音声チャンクをバイナリで WebSocket 送信
- [x] クライアントで AudioBufferSourceNode をスケジューリングして途切れなく再生
- [x] `config/central_realtime.toml` に `tts_backend = "say"` を設定

### パフォーマンステスト

```python
@pytest.mark.perf
async def test_e2e_latency_under_800ms():
    # VAD終了 → 最初の音声チャンク まで 800ms 以内
    # say を使うので TTS 区間はほぼゼロになるはず
    ...
```

**完了条件**: 話しかけると声で返事が返ってくる。`pytest -m perf` で 800ms 以内を確認。

### M1完了後: KokoroMLXBackend に切り替え

- [ ] `pip install kokoro-mlx misaki[ja]`
- [ ] `server/shared/inference/tts/kokoro_mlx.py`: `KokoroMLXBackend`
  - Gapless streaming 対応
  - emotion → voice のマッピング（jf_alpha / jf_beta）
- [ ] `config/central_realtime.toml` の `tts_backend` を `"say"` → `"kokoro_mlx"` に変更
- [ ] 日本語品質を確認して `docs/latency.md` に実測値を記録
- [ ] 品質が厳しければ VOICEVOX に切り替え（TTSBackend 抽象で差し替え可能）

---

## Phase 6a: 感情情報を DOM に出す（MVP）

自前プロトコル採用:
```
EMOTION:happy
うん、洗濯日和！
```

- [x] `prompts/base_persona.md` に出力フォーマット指示を追加
- [x] `ThinkFastMode` で最初の改行前後を分岐して emotion 送信
- [x] TTS には改行以降だけ流す
- [x] クライアントで `textContent = ev.value`

**完了条件**: 話の内容に応じて "happy" / "surprised" などが画面に出る。

### ✅ M1 完了条件

```
「トモコ、今日の天気は？」と話しかける
  → 声で返事が返ってくる
  → 画面に感情が文字で表示される
  → E2E レイテンシー 800ms 以内
  → pytest -m unit が全部通る
```

---

---

# M2: 記憶があるTomoko

**ゴール**: 数日前の話を覚えている。文脈を踏まえた返答ができる。

## Phase 6b: 静止画切り替えと声のトーン

（M1完了後の仕上げ）

- [x] `assets/images/` に立ち絵を配置
- [x] emotion イベントに `image` フィールドを追加（必ず音声より先に送る）

---

## Phase 7: 短期記憶

- [ ] `conversation_logs` テーブル作成
- [ ] 会話ターンごとに `(user_text, tomoko_text, timestamp, emotion)` を保存
- [ ] ThinkFastMode のプロンプトに直近 N ターンを差し込む

**完了条件**: 「さっき言った〇〇のことだけど」が通じる。

---

## Phase 8: 長期記憶（エピソード記憶）

- [ ] multilingual-e5-small でローカル embedding 生成
- [ ] pgvector に格納
- [ ] `server/gateway/thinking/deep.py`: `ThinkDeepMode`
  - 類似検索で top-K の過去会話をプロンプトに差し込む
- [ ] 短い発話 → fast、深い話題 → deep のモード選択

**完了条件**: 数日前の話題を「そういえばあの時...」として引き出せる。

### ✅ M2 完了条件

```
数日ぶりに話しかける
  → 前回の会話の文脈を踏まえた返答が来る
  → 「先週話してた〇〇、その後どうなった？」が通じる
```

---

---

# M3: 自分から話すTomoko

**ゴール**: 沈黙を破って話しかけてくる。日記を書く。言えなかったことを翌日話す。

## Phase 9: thinker + arrival 事前計算

- [ ] `utterance_candidates` / `arrival_candidates` テーブル作成
- [ ] `server/shared/candidate.py`: `UtteranceCandidate` 型定義
- [ ] `server/thinker/main.py`: 常駐ループ
  - `candidate_generation_loop` と `arrival_precompute_loop` を並行実行
- [ ] `server/thinker/sources/time_based.py`: 最初の情報源
- [ ] `server/thinker/evaluator/llm.py`: 発話すべきか判定
- [ ] `server/thinker/selection/highest.py`: `HighestPriority`
- [ ] `server/thinker/arrival.py`: 3 分ごとに arrival_candidates を作り直す
- [ ] docker-compose に thinker サービス追加

```python
@pytest.mark.perf
async def test_arrival_candidate_freshness():
    candidate = await db.fetch_latest_fresh_arrival_candidate()
    assert candidate is not None
    age = datetime.now() - candidate.computed_at
    assert age.total_seconds() < 300
```

---

## Phase 10: 自発発話 + 入室時の初手

- [ ] session に自発発話タイマーを追加（idle で N 秒 → キューから取り出す）
- [ ] 期限切れ候補の cleanup（dismissed_at を記録して削除）
- [ ] `on_session_start()` を実装
  - arrival_candidates から最新を取り出す
  - behavior に応じて振る舞う（speak_first / wait_silent / subtle_react）

**完了条件**:
- 何も話しかけなくても Tomoko が話しかけてくる
- ブラウザを開くと時刻・状況に応じた一言が出る

---

## Phase 11: 事前生成（pre-generation）

- [ ] `server/thinker/pregenerator.py`
  - priority > 0.8 → テキスト + TTS まで事前生成（maturity=2）
  - priority > 0.5 → テキストだけ（maturity=1）
- [ ] gateway で maturity=2 を優先的に選ぶ

**完了条件**: 高優先度の自発発話が即再生される（10ms 以内）。

---

## Phase 12: journalist（日記）

- [ ] `diary` テーブル作成
- [ ] `server/journalist/main.py`: 定期実行
  - conversation_logs + ambient_logs + dismissed 候補 + 感情ログを読む
  - LLM に日記を書かせる
  - dismissed_at の候補から「言えなかったこと」を自然に書かせる
- [ ] `server/thinker/sources/diary.py`: `DiarySource`
  - 昨日の日記から utterance_candidates に候補を積む
- [ ] docker-compose に journalist サービス追加

**完了条件**:
- 日記が毎日書かれる
- 翌日に「昨日日記に書いたんだけど」と話しかけてくる
- 「言えなかったこと」が日記に記録されている

### ✅ M3 完了条件

```
しばらく放置する
  → Tomoko が自分から話しかけてくる
ブラウザを開く
  → 時刻・久しぶり度合いに応じた一言が来る
翌朝
  → 昨日の会話や言えなかったことが日記になっている
  → 「昨日書いた日記なんだけど」と話しかけてくる
```

---

---

# M4: インフラが安定したTomoko

**ゴール**: 詰まったら自動フォールバック。設定ファイルを変えるだけで構成が変わる。

## Phase 13: InferenceRouter の強化

- [ ] `server/shared/inference/monitor.py`: `BackendHealthMonitor`
  - 定期的に実測して `inference_metrics` テーブルに書く
- [ ] `InferenceRouter` に実測値ベースのフォールバックを追加
- [ ] `server/shared/inference/backends/anthropic.py`: `AnthropicBackend`
  - privacy_allowed = False
- [ ] `config/central_realtime.toml` に cloud フォールバックを追加

```python
async def test_cloud_fallback_for_latency():
    router = InferenceRouter(
        config=load_config("config/central_realtime.toml"),
        monitor=MockMonitor({"local_qwen7b": InferenceMetrics(latency_ms=600)})
    )
    backend = await router.select("conversation", "latency")
    assert backend.name == "cloud_anthropic"

async def test_privacy_never_goes_to_cloud():
    router = InferenceRouter(
        config=load_config("config/central_realtime.toml"),
        monitor=MockMonitor({"local_qwen7b": InferenceMetrics(latency_ms=999)})
    )
    backend = await router.select("conversation", "privacy")
    assert backend.privacy_allowed == True
```

**完了条件**: ローカルが詰まった時に自動でクラウドに切り替わる。`pytest -m unit` 全通過。

---

## Phase 14: エッジ分離 + 回り込み除去

- [ ] `server/edge/main.py` をエッジ専用に整理
  - STT 結果をテキストで中央に送信（音声は外に出さない）
- [ ] `presence` / `edge_status` テーブル作成
- [ ] `server/gateway/resolver.py`: `DirectSpeakerResolver`
- [ ] `server/gateway/dedup.py`: `DuplicateSpeechFilter`
- [ ] `server/gateway/presence.py`: `PresenceManager`
- [ ] `config/edge_kitchen.toml` 作成
- [ ] docker-compose でエッジと中央を別サービスに分離

```python
async def test_duplicate_speech_filtered():
    filter = DuplicateSpeechFilter(db=MockDB(
        recent_logs=[AmbientLog(transcript="今日いい天気", device_id="living")]
    ))
    assert await filter.is_duplicate("今日いい天気", "kitchen", datetime.now())

async def test_loudest_edge_is_primary():
    resolver = DirectSpeakerResolver()
    primary = await resolver.resolve([
        PresenceReport(device_id="kitchen", audio_level_db=-20),
        PresenceReport(device_id="living",  audio_level_db=-10),
    ])
    assert primary.device_id == "living"
```

---

## Phase 15: エッジ軽量 LLM

- [ ] `config/edge_kitchen.toml` に `local_gemma` backend を追加
- [ ] エッジの InferenceRouter が中央ダウン時に local_gemma にフォールバック
- [ ] エッジ LLM と中央 LLM の品質差をメモ

```python
async def test_edge_config_uses_gemma():
    config = NodeConfig.load("config/edge_kitchen.toml")
    router = InferenceRouter(config, monitor=MockMonitor())
    backend = await router.select("conversation", "latency")
    assert backend.model == "gemma3:2b"
```

### ✅ M4 完了条件

```
ローカル LLM を意図的に重くする
  → 自動でクラウドにフォールバックする
  → privacy タスクはフォールバックしない

エッジを別マシンに移す
  → config/ を切り替えるだけで動く
  → pytest -m unit が全通過

キッチンで話す
  → リビングに回り込んでも二重応答しない
```

---

---

# M5: 家族のTomoko

**ゴール**: 複数デバイス・話者識別・人格の育ち。未定。

## Phase 16: ParticipationJudge の強化（任意）

- [ ] `LLMJudge`: 話題・文脈で参加判断
- [ ] `HybridJudge`: ウェイクワード + LLM 判断
- [ ] 参加モードを音声コマンドで切り替え
  - 「トモコ、静かにしてて」→ WakeWordJudge
  - 「自由に参加していいよ」→ HybridJudge

---

## Phase 17: 人格の育ち

- [ ] `ThinkPersonaUpdateMode`: 会話後に persona を微更新
- [ ] `prompts/persona_history/` に差分を残す

**完了条件**: 1 週間使うと初期人格と現在の人格に差が出る。

### ✅ M5 完了条件

```
家族の真剣な話が始まる
  → Tomoko が自然に引く（withdraw）
「トモコ、静かにしてて」と言う
  → ウェイクワードだけに戻る
1週間後
  → 最初の人格と今の人格が違う
```

---

## 将来の拡張アイデア

- 立ち絵の軽い Live2D 化（瞬きと口パクだけ）
- NewsSource（RSS から話題を取る）
- ScheduledSource（誕生日などの予定から候補を生む）
- 話者識別（pyannote）の本格導入
- 家族ごとの関係性データベース
- ロボティクス基盤への移植（Optimus 等が現実になった時）

---

## 2026-05-23 追記: Phase 7 の前に Phase 6.5 を追加する

上の M2 では Phase 6b の次に Phase 7「短期記憶」へ進む計画になっているが、この順序は否定する。
短期記憶へ進む前に、wake word 後の自然な会話継続と ambient 聞き取りへの復帰を扱う
**Phase 6.5: AttentionMode / 参加モード状態機械**を先に実装する。

理由:
- `ambient_logs` と `conversation_logs` の境界を先に決めないと、記憶に入れるべき発話が曖昧になる
- 将来の wake word 外参加は、今 Tomoko が会話中か、聞いているだけか、入ってよい空気かを前提に判定する必要がある
- 3年前の Unity 実装のように `isRecording` / `isCommunicating` / `isAITalking` が分散すると、後から自然な参加判断を足せない
- 「あ、聞いてなかった」は、音声処理の失敗ではなく `attended=false` の人格表現として扱う必要がある

### Phase 6.5: AttentionMode / 会話と聞き取りの自然遷移

**目標**: wake word 後は自然に会話が続き、会話が収束したら ambient 聞き取りに戻る。
常時 STT は続けるが、Tomoko が会話として注意を向けていたかどうかを明示的に分ける。

- [x] `server/shared/models.py` に `AttentionMode` / `ParticipationContext` の必要フィールドを追加する
  - `attention_mode`: `ambient` / `engaged` / `cooldown` / `withdrawn`
  - `attended`: Tomoko が会話として注意を向けていたか
  - `participation_mode`: `called` / `invited` / `observer` / `withdraw`
- [x] `TomoroSession` に `attention_mode` を集約する
  - wake word で `ambient -> engaged`
  - `engaged` 中は wake word なしの継続発話にも返答できる
  - Tomoko の返答完了後、一定時間の無発話で `engaged -> cooldown`
  - `cooldown` 中に関連発話があれば `engaged` に戻る
  - 一定時間何もなければ `cooldown -> ambient`
  - 「静かにして」「今は入らないで」系で `withdrawn`
- [x] `ParticipationJudge` を `attention_mode` 前提で判定できる形に拡張する
  - `ambient`: wake word か強い呼びかけ以外は原則 `observer`
  - `engaged`: 直前会話の続きなら `invited`
  - `cooldown`: 関連発話なら `invited`、無関係なら `observer`
  - `withdrawn`: 原則 `withdraw`
- [x] `ambient_logs` に `attention_mode` / `attended` / `participation_mode` を保存する
- [x] `conversation_logs` は `attended=true` の会話ターンだけを保存する
- [x] 「聞いてなかった」扱いの応答方針をテストで固定する
  - 内部的に `ambient_logs` へ記録されていても、`attended=false` の発話は直近会話文脈として使わない
  - 後からその話題を振られた場合は「その時はちゃんと聞いてなかった」と返せる余地を残す
- [x] 状態遷移時は必ず `log.info` で記録する
- [x] `tests/unit/test_attention_mode.py` を追加する

**完了条件**:
- 「トモコ」で呼ぶと `engaged` になり、続く発話は wake word なしでも返答対象になる
- 会話が途切れると `cooldown` を経て `ambient` に戻る
- `ambient` の発話は STT/ambient_logs には残るが、会話文脈には入らない
- `conversation_logs` には Tomoko が注意を向けた会話だけが保存される
- `pytest -m unit` が通る

### 将来の wake word 外参加への接続

Phase 6.5 では高度な LLM 参加判定までは実装しない。
ただし、将来の Phase 16 `LLMJudge` / `HybridJudge` はこの `attention_mode` を前提にする。

最終的には次のように判定する:

```text
ambient + 関連度低い
  -> 聞いていただけ

ambient + 強い呼びかけ/名前/質問
  -> engaged

engaged + 継続発話
  -> wake word なしで返答

cooldown + 関連発話
  -> invited として復帰

available/ambient + 自発発話候補と強く接続できる話題
  -> 自然に一言だけ入る

withdrawn
  -> 関連していても入らない
```

---

## 2026-05-23 追記: Phase 6.6.0 TurnTaking / BargeInDetector を追加する

Phase 6.5 の AttentionMode だけでは、Tomoko が話している最中の人間発話を自然に扱えない。
ただし、3年前の Unity 実装のように `isAITalking` 中の録音処理を止める方針は否定する。
Tomoko 発話中も人間が「ちょっと待って」「違う違う」「待って待って」と割り込むことはあり得るため、
マイク入力と STT は続けたまま、TTS 中に得られた発話を分類する。

### Phase 6.6.0: TurnTaking / BargeInDetector

**目標**: Tomoko は原則として発話中の文を言い切る。ただし、発話中に緊急度の高い割り込みが入った場合は、
次の文を送らない、または仕切り直す。相槌やスピーカー回り込みは会話割り込みとして扱わない。

VAD に似た構造で扱う:

```text
VAD:
  audio chunk
    -> speech / silence score
    -> threshold + duration
    -> speech_start / speech_end

BargeInDetector:
  transcript while Tomoko speaking
    -> echo / backchannel / soft_interrupt / hard_interrupt / new_question score
    -> keyword + duration/window + hysteresis
    -> continue / finish_sentence / restart_turn
```

- [x] `server/shared/models.py` に `BargeInDecision` DTO を追加する
  - `kind`: `echo` / `backchannel` / `soft_interrupt` / `hard_interrupt` / `new_question`
  - `action`: `continue_speaking` / `finish_sentence` / `restart_turn`
  - `reason`: 判定理由
- [x] `server/gateway/turn_taking/barge_in.py` を追加する
  - embedding は主判定に使わない
  - TTS 回り込み検出は、再生中時間窓 + 文字列/音素寄り類似度を優先する
  - semantic embedding は将来の話題関連度の補助に限定する
- [x] Tomoko 発話中の transcript を通常の `ParticipationJudge` に直行させず、先に `BargeInDetector` に通す
- [x] 分類ルールを最初はルールベースで固定する
  - `echo`: 直近 Tomoko 発話と文字列類似度が高い
  - `backchannel`: 「うん」「はい」「へえ」「なるほど」「そうなんだ」
  - `soft_interrupt`: 「ちょっと待って」「待って」「違う」「それ違う」
  - `hard_interrupt`: 「待って待って」「違う違う」「ストップ」「やめて」「止めて」
  - `new_question`: Tomoko 発話中の別質問
- [x] ヒステリシスを入れる
  - Tomoko 発話開始直後の短時間は判定しない
  - 短すぎる発話は原則 `backchannel` または `continue_speaking`
  - echo 判定を interrupt 判定より優先する
  - hard interrupt は反復語や強い停止語を優先する
- [x] M1 の `say` は文単位チャンクなので、最初は「再生中チャンクを止める」ではなく「次の文を送らない」で実装する
- [ ] 将来、クライアントから `playback_started` / `playback_ended` のテレメトリを送る余地を残す
  - クライアントは判定しない
  - 再生状態という事実だけを `/ws` に返す
- [x] `tests/unit/test_barge_in.py` を追加する

**完了条件**:
- Tomoko 発話中の相槌では Tomoko が話し続ける
- Tomoko 発話中の「ちょっと待って」は文末で仕切り直し候補になる
- Tomoko 発話中の「違う違う」「待って待って」「ストップ」は次の TTS 文を送らず、聞き直しに入る
- Tomoko 自身の声の回り込みは `echo` として observer 相当に扱われる
- TTS 中の人間発話をすべて捨てる実装になっていない
- `pytest -m unit` が通る

---

## 2026-05-23 追記: Phase 6.6.1 AudioPlaybackControl を追加する

Phase 6.6.0 の BargeInDetector は hard interrupt を分類できるが、すでにクライアントへ送った音声の停止はできない。
TTS バックエンドを `say` から kokoro / irodori に変える前に、サーバー主導の基本再生制御プロトコルを作る。

### Phase 6.6.1: AudioPlaybackControl

**目標**: サーバーが「どの返答音声を開始したか」「どの返答音声を終えたか」「どの返答音声を止めるか」を
WebSocket JSON イベントで明示し、クライアントは命令に従って再生中/予約済み音声を止められるようにする。

- [x] 返答ごとに `turn_id` を発行する
- [x] 最初の音声バイナリより前に `{"type":"audio_start","turn_id":"..."}` を送る
- [x] 返答音声の送信完了後に `{"type":"audio_end","turn_id":"..."}` を送る
- [x] hard interrupt で `{"type":"audio_control","action":"stop","turn_id":"..."}` を送る
- [x] クライアントは再生中/予約済みの `AudioBufferSourceNode` を `turn_id` ごとに保持する
- [x] `audio_control: stop` を受けたら対象 turn の source を `stop()` し、`nextPlaybackTime` を現在時刻へ戻す
- [x] WebSocket の順序保証を前提に、binary audio chunk は直前の `audio_start.turn_id` に属すると扱う
- [x] TCP 的な sequence 並べ替えは実装しない

**完了条件**:
- `audio_start` が音声バイナリより先に届く
- `audio_end` が返答完了時に届く
- hard interrupt 時に `audio_control stop` が届く
- クライアントがサーバー命令だけで再生中/予約済み音声を停止できる
- `pytest -m unit` が通る
