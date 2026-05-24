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

**完了条件**: 自分の声がエコーで返ってくる。レイテンシーを実測して `_docs/latency.md` にメモ。

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

- [x] `pip install kokoro-mlx misaki[ja]`
- [x] `server/shared/inference/tts/kokoro_mlx.py`: `KokoroMLXBackend`
  - Gapless streaming 対応
  - emotion → voice のマッピング（jf_alpha / jf_beta）
- [x] `config/central_realtime.toml` の `tts_backend` を `"say"` → `"kokoro_mlx"` に変更
- [x] 日本語品質を確認して `_docs/latency.md` に実測値を記録
- [x] 品質が厳しければ VOICEVOX に切り替え（TTSBackend 抽象で差し替え可能）

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

## Phase 6.6.1.2: Follow-up 誤起動の抑制

Phase 6.6.1 / playback telemetry により、Tomoko 音声の回り込みは実用上問題ない水準まで改善した。
一方で、`engaged` / `cooldown` 中に小さな物音や Whisper の定型 hallucination が
`attention_engaged_followup` / `attention_cooldown_followup` として会話継続する問題が残っている。

- [x] STT transcript を全件ログに出す
  - `text` / `audio_level_db` / `attention_mode` / `state`
- [x] 低信頼 follow-up を `observer` 扱いにする
  - 空文字
  - 1〜2文字の短文
  - 低音量の短文
  - Whisper が無音・ノイズで出しがちな定型文
- [x] 低信頼 observer 発話では attention idle を延長しない
- [x] `tests/unit/test_participation.py` / `tests/unit/test_attention_mode.py` に回帰テストを追加

**完了条件**:
- 回り込みは `playback_active_chunk` / `playback_ended_grace` で抑止される
- 小さな物音や `ご視聴ありがとうございました` 系 hallucination が follow-up 参加しない
- 低信頼発話で `cooldown -> ambient` 復帰が妨げられない
- `pytest -m unit` が通る

---

## Phase 7: 短期記憶

- [x] `conversation_logs` テーブル作成
- [x] 会話ターンごとに `(user_text, tomoko_text, timestamp, emotion)` を保存
- [x] ThinkFastMode のプロンプトに直近 N ターンを差し込む

**完了条件**: 「さっき言った〇〇のことだけど」が通じる。

### 2026-05-24 実装結果

Phase 7 は既存 `conversation_logs` の role 行保存を活かし、短期文脈の読み出しと
`ThinkFastMode` への差し込みを追加する形で完了した。

- `PostgresConversationLogWriter.read_recent_turns(limit=...)` を追加した
- `TomoroSession` が reply 生成時に直近 `ConversationTurn` を読み、現在の user transcript と重複する末尾だけ除外するようにした
- `ThinkFastMode` が直近 user/tomoko turns を OpenAI 互換 messages の `user` / `assistant` role として渡すようにした
- 短期文脈の上限は `RECENT_CONTEXT_TURN_LIMIT = 12`
- `ruff check .`、`pytest -m unit`、`pytest -m perf --tb=short tests/perf/test_phase5_latency.py` が通過した

### 2026-05-24 追記: interrupted turn の保存

人間判断により、`conversation_logs` は role 形式のまま維持しつつ、返答状態を保存する。

- `conversation_logs.status TEXT NOT NULL DEFAULT 'completed'` を追加した
- 通常完了した Tomoko 返答は `completed`
- hard interrupt で止められた Tomoko 返答は `interrupted`
- 将来用に `cancelled` / `error` も `ConversationLogStatus` として予約した
- 短期記憶の直近文脈では `completed` だけを使う
- `interrupted` は日記や「言えなかったこと」の材料として残す

---

## Phase 8: 長期記憶（エピソード記憶）

- [x] multilingual-e5-small でローカル embedding 生成
- [x] pgvector に格納
- [x] `server/gateway/thinking/deep.py`: `ThinkDeepMode`
  - 類似検索で top-K の過去会話をプロンプトに差し込む
- [x] 短い発話 → fast、深い話題 → deep のモード選択

**完了条件**: 数日前の話題を「そういえばあの時...」として引き出せる。

### 2026-05-24 実装結果

Phase 8 は、既存 `conversation_logs` を原本として保ち、embedding だけを
`conversation_embeddings` に分離する形で実装した。

- `sentence-transformers` の `intfloat/multilingual-e5-small` を `EmbeddingBackend` として追加した
- `conversation_embeddings(conversation_log_id, embedding vector(384), model, embedded_at)` を追加した
- `PostgresConversationMemoryStore` で未embedding turn の backfill と cosine 類似検索を実装した
- `ThinkDeepMode` は top-K の `MemoryHit` を system prompt に差し込み、通常の emotion/text streaming 契約は維持する
- `TomoroSession` は記憶 cue や長めの相談文では deep、短い発話では fast を選ぶ
- 現在の user transcript 自身が検索結果に混ざった場合は除外する
- `_tools/embed_conversation_logs.py --limit N` で既存 `conversation_logs` を backfill できる
- ローカル PostgreSQL に `conversation_embeddings` を適用し、3件の既存 turn を embedding 済み
- 実測は `_docs/latency.md` に記録した
- `ruff check .`、`pytest -m unit`、`pytest -m perf --tb=short tests/perf/test_phase5_latency.py` が通過した

## Phase 8.5: 会話セッション境界

上の Phase 7/8 の短期記憶・長期記憶の実装は否定しない。
ただし、現状の `conversation_logs` は時系列の role 行であり、「今の会話のまとまり」を表す境界がない。
そのため、直近文脈は存在していても、実会話では「さっきの話」が平たく混ざって見える可能性がある。

**目標**: `attention_mode` を起点に会話セッションを作り、短期文脈をまず同一セッションから読む。
これにより、Tomoko が会話中の前の応答や人間の発話を、現在の会話の流れとして扱えるようにする。

- [x] `conversation_sessions` テーブルを作成する
  - `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
  - `started_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `ended_at TIMESTAMPTZ`
  - `start_reason TEXT NOT NULL`
  - `end_reason TEXT`
  - `device_id TEXT NOT NULL`
  - `summary_text TEXT`
  - `summary_status TEXT NOT NULL DEFAULT 'not_ready'`
  - `summary_model TEXT`
  - `summary_generated_at TIMESTAMPTZ`
  - `summary_embedding vector(384)`
  - `summary_embedding_model TEXT`
  - `summary_embedded_at TIMESTAMPTZ`
  - `summary_error TEXT`
- [x] `conversation_logs` に `conversation_session_id UUID NULL` を追加する
  - `conversation_sessions(id)` を参照する
  - 既存ログは NULL のまま維持し、移行で無理に過去セッションを推定しない
- [x] `TomoroSession` に現在の会話セッション ID を持たせる
  - authoritative state は引き続き `TomoroSession` が所有する
  - クライアントにセッション判断ロジックを置かない
- [x] `attention_mode` が `ambient -> engaged` へ遷移した瞬間、または最初の `should_participate=True` 発話で会話セッションを開始する
  - wake word による開始は `start_reason="wake_word"` または `called`
  - follow-up による開始は `start_reason="followup"` または `invited`
  - 二重作成を避け、既に active session があれば再利用する
- [x] `engaged` / `cooldown` 中の user / tomoko turn は同じ `conversation_session_id` に紐づける
  - ambient / observer 発話は `ambient_logs` に残し、会話セッションには入れない
  - hard interrupt で `interrupted` として保存する Tomoko turn も、発話中の session に紐づける
- [x] `cooldown -> ambient` で会話セッションを閉じる
  - `ended_at` と `end_reason="attention_timeout"` を保存する
  - `summary_status="pending"` にする
  - 「静かにして」などで `withdrawn` に入る場合は `end_reason="withdrawn"` として閉じる
- [x] 短期文脈の読み出しを変更する
  - まず同一 `conversation_session_id` の直近 completed turn を読む
  - 足りない場合だけ、最近の completed turn で補う
  - 現在の user transcript は従来通り重複除外する
- [x] `ThinkFastMode` / `ThinkDeepMode` の外部契約は変えない
  - `ThinkingInput.context` は引き続き `ConversationTurn` の list
  - WebSocket エンドポイントやメッセージタイプは増やさない
- [x] unit test を追加する
  - `ambient -> engaged` で session が 1 つだけ作られる
  - `engaged` / `cooldown` 中の user / tomoko turn に同じ session ID が付く
  - `cooldown -> ambient` で session が閉じる
  - 直近 context は同一 session を優先し、足りない時だけ過去 completed turn で補う
  - observer / ambient 発話は session context に混ざらない

**完了条件**:
- 「トモコ、さっきの続きだけど」が同一会話セッションの文脈を優先して返答される
- ambient に戻った後の新しい会話では、前セッションの文脈が必要以上に強く混ざらない
- `conversation_logs.conversation_session_id` から会話のまとまりを追える
- `pytest -m unit` が通る

### 2026-05-24 実装結果

Phase 8.5 は `conversation_sessions` を会話単位の境界として追加し、オンライン経路では開始・終了だけを行う形で実装した。

- `docker/postgres/init/004_conversation_sessions.sql` を追加した
  - `conversation_sessions` に session metadata / summary fields / `summary_embedding vector(384)` を持たせる
  - `conversation_logs.conversation_session_id` を追加し、既存ログは NULL のまま維持する
- `PostgresConversationSessionStore` を追加した
  - session 開始時は `summary_status='not_ready'`
  - session 終了時は `ended_at` / `end_reason` を保存し、`summary_status='pending'` にする
- `TomoroSession.active_conversation_session_id` を追加した
  - 最初の参加発話で session を開始する
  - follow-up 中は既存 active session を再利用する
  - `cooldown -> ambient` は `end_reason='attention_timeout'`、`withdrawn` は `end_reason='withdrawn'` で閉じる
- user / tomoko turn 保存時に active session ID を渡すようにした
  - 既存 unit test の小さな in-memory writer と互換性を保つため、対応 writer にだけ keyword を渡す
- 短期文脈は同一 session の completed turn を優先し、足りない場合だけ最近の completed turn で補うようにした
- `tests/unit/test_phase85_conversation_sessions.py` を追加した
- ローカル PostgreSQL に DDL を適用した
- `ruff check .`、`pytest -m unit`、`pytest -m perf --tb=short tests/perf/test_phase5_latency.py` が通過した

## Phase 8.6: セッション要約索引

Phase 8.5 の会話セッション境界を前提に、閉じた会話セッションを background worker が要約し、
`conversation_sessions` に要約テキストと要約 embedding を保存する。

この Phase では、要約 embedding 用の別テーブルは追加しない。
`conversation_sessions` は会話単位のメタ情報・要約・検索用 embedding をまとめて持つ。
将来、複数 embedding モデルや複数種類の要約を保持する必要が出た時だけ分離を検討する。

**目標**: log 的な turn 列を毎回走査せず、会話単位の「索引カード」から短期記憶・長期記憶を引けるようにする。

- [x] `session_summarizer` を追加する
  - `summary_status='pending'` かつ `ended_at IS NOT NULL` の session を拾う
  - 同じ `conversation_session_id` の `conversation_logs` を時系列で読む
  - `InferenceRouter.select("session_summary", "privacy")` で要約を生成する
  - `EmbeddingBackend.embed_passage(summary_text)` で要約 embedding を作る
  - `conversation_sessions.summary_text` / `summary_embedding` / metadata を更新する
- [x] `TomoroSession` は要約生成をしない
  - online path では session を閉じ、`summary_status='pending'` にするだけ
  - LLM 要約や embedding 生成の計算コストを `/ws` 受信ループに乗せない
- [x] `summary_status` を運用できるようにする
  - `not_ready`: active session または要約対象外
  - `pending`: session 終了済み、要約待ち
  - `processing`: worker が処理中
  - `completed`: 要約と embedding 保存済み
  - `error`: 失敗。`summary_error` に理由を残す
- [x] session summary 検索を長期記憶候補に使う
  - 現在発話の query embedding で `conversation_sessions.summary_embedding` を検索する
  - 関連 session の `summary_text` を `MemoryHit` 相当として `ThinkDeepMode` に渡す
  - 必要なら該当 session 内の turn や turn embedding へ掘る
- [x] 既存 turn embedding は残す
  - `conversation_embeddings` は細かい turn 検索用
  - `conversation_sessions.summary_embedding` は会話単位の粗い検索用
  - 原本は常に `conversation_logs`
- [x] unit test / integration test を追加する
  - pending session が要約され、`summary_status='completed'` になる
  - 要約失敗時に `summary_status='error'` と `summary_error` が残る
  - session summary 検索が関連 session を返す
  - online `TomoroSession` 経路で summarizer が呼ばれない

**完了条件**:
- 会話終了後、別プロセスで `conversation_sessions.summary_text` と `summary_embedding` が埋まる
- 「この前話してた〇〇」で session summary から関連会話を引ける
- 要約生成が失敗しても原本 `conversation_logs` は残り、再実行できる
- `pytest -m unit` が通る

### 2026-05-24 実装結果

Phase 8.6 は、online `/ws` 経路から要約生成を分離し、閉じた会話 session を別プロセスで索引化する形で実装した。

- `server/background/session_summarizer.py` を追加した
  - `summary_status='pending'` かつ `ended_at IS NOT NULL` の session を `processing` として claim する store 契約にした
  - session 内の completed turn を時系列で読み、`InferenceRouter.select("session_summary", "privacy")` で要約を生成する
  - `EmbeddingBackend.embed_passage(summary_text)` で summary embedding を作り、`conversation_sessions` 同一行へ保存する
  - 失敗時は `summary_status='error'` と `summary_error` を残し、原本 `conversation_logs` は変更しない
- `PostgresConversationSessionSummaryStore` を追加した
  - pending claim / session turn read / summary complete / error mark / summary vector search を担当する
  - `conversation_sessions.summary_embedding` に HNSW index を追加した
- `_tools/summarize_pending_sessions.py` を追加した
  - background worker 相当として pending session を任意件数処理できる
- `InferenceRouter` に `session_summary` role を追加した
  - `config/central_realtime.toml` に `session_summary_backend` / `session_summary_fallback` を追加した
- `TomoroSession` は要約生成を呼ばない
  - online 経路では従来通り session close 時に `summary_status='pending'` へ進めるだけ
  - deep memory 検索では completed session summary を読み取り専用で検索し、turn-level memory と併用する
- `tests/unit/test_phase86_session_summary.py` を追加した
  - pending session の completed 化、失敗時 error 化、summary 検索、Null store を固定した
  - `tests/unit/test_phase8_memory.py` で online `TomoroSession` が summary 生成系メソッドを呼ばず、summary search だけ使うことを固定した
- ローカル PostgreSQL に更新済み DDL を適用した
- `ruff check .` と `pytest -m unit` が通過した

### 2026-05-24 追記: background process 入口の配置補正

上の「`_tools/summarize_pending_sessions.py` を追加した」という配置は、background worker の入口としては
`_tools/` よりも役割が曖昧だったため補正する。

`summarize_pending_sessions.py` はルートの `background-process/` 配下へ移動し、
Makefile から起動する。

- `make session-summarizer`: `--watch` 付きで pending session を定期処理する
- `make session-summarizer-once`: 1 batch だけ処理して終了する

## Phase 8.7: 用語集ログと人格スナップショット

Phase 8.6 の session summary を材料に、要約で落ちやすい印象的フレーズ・関係性マーカー・人格変化を
versioned JSONB snapshot として保存する。

**目標**: 後から外部分析で「いつ、どの会話が、Tomoko の語彙や性格状態にどう影響したか」を追跡できるようにする。

- [x] `persona_lexicon_versions` テーブルを作成する
  - `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
  - `version INTEGER NOT NULL`
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `source_session_id UUID REFERENCES conversation_sessions(id)`
  - `previous_version_id UUID REFERENCES persona_lexicon_versions(id)`
  - `reason TEXT NOT NULL`
  - `lexicon_json JSONB NOT NULL`
  - `diff_json JSONB NOT NULL`
  - `schema_version INTEGER NOT NULL DEFAULT 1`
  - `model TEXT`
  - `status TEXT NOT NULL DEFAULT 'completed'`
- [x] `persona_state_versions` テーブルを作成する
  - `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`
  - `version INTEGER NOT NULL`
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `source_session_id UUID REFERENCES conversation_sessions(id)`
  - `previous_version_id UUID REFERENCES persona_state_versions(id)`
  - `reason TEXT NOT NULL`
  - `state_json JSONB NOT NULL`
  - `diff_json JSONB NOT NULL`
  - `schema_version INTEGER NOT NULL DEFAULT 1`
  - `model TEXT`
  - `status TEXT NOT NULL DEFAULT 'completed'`
- [x] JSONB 分析用 index を追加する
  - `lexicon_json` / `state_json` に GIN index を張る
  - よく使う key は expression index を検討する
- [x] プログラム側モデルを追加する
  - `PersonaLexiconSnapshot`
  - `PersonaStateSnapshot`
  - `PersonaVersionDiff`
  - `schema_version` ごとの loader / validator
  - DB 入出力時は JSONB をモデルクラスへ変換し、生 dict を持ち回らない
- [x] `lexicon_update` worker を追加する
  - completed session summary と必要な raw turns を読む
  - 印象的フレーズ、用語、訂正、関係性マーカーを抽出する
  - 前 version からの `diff_json` を作る
  - `persona_lexicon_versions` に新 version を追加する
- [x] `persona_update` を snapshot 方式へ寄せる
  - 最新 `persona_state_versions.state_json` を読み込む
  - session summary / lexicon diff / diary を材料に次 version を作る
  - `state_json` と `diff_json` を保存する
- [x] 応答生成での利用は subset に限定する
  - 最新 snapshot 全量を毎回 prompt に入れない
  - 現在発話・関連 session summary に関係する term / phrase / speaking_style だけを取り出す
  - `ThinkingInput` に渡す場合は DTO を追加して境界を明確にする
- [x] unit test / integration test を追加する
  - JSONB snapshot をモデルクラスへ round-trip できる
  - schema_version が違う snapshot を loader が扱える
  - diff_json から追加/更新/廃止の変動点を追える
  - 外部分析用の jsonb query が期待する term / phrase を拾える

**完了条件**:
- 会話セッション由来の用語集 version と人格状態 version が DB に残る
- `diff_json` で変動点を追跡できる
- JSONB は PostgreSQL で検索でき、プログラム内ではモデルクラスとして扱える
- `pytest -m unit` が通る

### 2026-05-24 実装結果

Phase 8.7 は、用語集と人格状態を versioned JSONB snapshot として保存する土台を実装した。

- `docker/postgres/init/005_persona_snapshots.sql` を追加した
  - `persona_lexicon_versions` / `persona_state_versions` を作成する
  - `lexicon_json` / `state_json` / `diff_json` に GIN index を張る
  - version / source session / created_at index を追加する
- `server/shared/models.py` に schema version 付きモデルクラスを追加した
  - `PersonaLexiconSnapshot`
  - `PersonaStateSnapshot`
  - `PersonaVersionDiff`
  - `LexiconTerm`
  - `PersonaPromptSlice`
- `server/shared/persona.py` に `PostgresPersonaSnapshotStore` を追加した
  - completed session summary と raw turns を読み出す
  - 最新 lexicon / state snapshot をモデルクラスとして読む
  - 新しい lexicon / state version を JSONB として保存する
- `server/background/persona_updater.py` を追加した
  - completed session summary を材料に lexicon / persona state の次 version を作る
  - LLM extractor は JSON だけを返す契約にし、保存時は loader / validator を通す
- `background-process/update_persona_snapshots.py` と Makefile entry を追加した
  - `make persona-updater`
  - `make persona-updater-once`
- 応答生成で使う場合は `select_terms_for_prompt()` / `to_prompt_slice()` の subset DTO を通し、
  JSONB snapshot 全量を prompt に直接入れない契約にした
- `tests/unit/test_phase87_persona_snapshots.py` と
  `tests/integration/test_phase87_persona_snapshots_db.py` を追加した
- ローカル PostgreSQL に DDL を適用した
- `ruff check .`、`pytest -m unit`、Phase 8.7 integration test が通過した

## Phase 8.8: ContextSnapshotBuilder 初段

短期記憶、長期記憶、session summary、用語集、人格スナップショットを `ThinkingMode` が直接読み分ける設計は、
今後の実装が増えるほどレイテンシーとテスト範囲を管理しづらくする。
この Phase では、LLM に渡す文脈を組み立てる読み取り専用の `ContextSnapshotBuilder` を追加する。

**目標**: Tomoko のメイン対話推論に渡す文脈取得を一箇所に集約し、depth ごとの絶対ラウンドトリップ速度を perf test で固定する。
長期運用で DB 上のログ・要約・embedding・人格 snapshot が増えても、context 生成時間を固定予算内に収める。

- [ ] `TomokoContextSnapshot` DTO を追加する
  - `depth`
  - `recent_turns`
  - `session_summaries`
  - `memory_hits`
  - `lexicon_terms`
  - `persona_slice`
  - `token_budget_hint`
  - `build_elapsed_ms`
  - `source_counts`
- [ ] `ContextBuildPolicy` を追加する
  - `depth`
  - `max_build_ms`
  - `max_prompt_tokens`
  - `max_same_session_turns`
  - `max_recent_turns`
  - `max_session_summaries`
  - `max_memory_hits`
  - `max_lexicon_terms`
  - `allow_turn_memory_search`
  - `allow_persona_slice`
- [ ] `ContextBuildTrace` を追加する
  - `budget_ms`
  - `elapsed_ms`
  - `timed_out`
  - `included_counts`
  - `skipped_sources`
  - `stage_timings_ms`
  - `cache_hits`
  - `source_errors`
- [ ] `ContextDepth = fast | normal | deep | reflective` を追加する
  - `fast`: active session の直近 turn
  - `normal`: fast + 関連 session summary + 関連 lexicon 少量
  - `deep`: normal + turn embedding / session 内代表 turn
  - `reflective`: 日記・人格更新用。online 対話では使わない
- [ ] `ContextSnapshotBuilder` を追加する
  - 読み取り専用にする
  - session 開始/終了、summary 生成、persona update、lexicon update はしない
  - DB row / JSONB をそのまま返さず、DTO / モデルクラスへ変換する
- [ ] context build を時間予算付き best-effort にする
  - `max_build_ms` を超えたら未完了 source は skipped として打ち切る
  - timeout は応答失敗ではなく degraded context として扱う
  - 同一 session の recent turns を baseline とし、長期記憶・用語集・人格 slice は optional enrichment とする
- [ ] context source を parallel DB I/O で読む
  - same session recent turns
  - recent completed turns
  - session summary vector search
  - turn embedding vector search
  - persona state
  - lexicon snapshot
  - 返却順ではなく priority / relevance / recency / token budget で assemble する
- [ ] `ContextSnapshotBuilder` 内部に process-local TTL cache を追加できる境界を作る
  - 初段では no-op / disabled でもよい
  - cache は DB read の speed-up のみ。source of truth にはしない
  - cache hit / miss / age_ms / ttl_ms を trace に出せるようにする
  - Redis は導入しない。単一サーバー運用中は process-local cache で十分とする
- [ ] 初段の fallback 動作を実装する
  - Phase 8.5 未実装でも既存 `read_recent_turns()` で `fast` が動く
  - Phase 8.6 未実装なら `session_summaries=[]`
  - Phase 8.7 未実装なら `lexicon_terms=[]` / `persona_slice=None`
  - 既存 Phase 8 の `conversation_embeddings` は `deep` で使える
- [ ] `TomoroSession` から context 読み込みを builder に寄せる
  - active `conversation_session_id` と transcript を渡す
  - `should_use_deep_memory()` 相当の判断は depth 選択へ寄せる
  - `ThinkingInput` には snapshot または snapshot から変換した context を渡す
- [ ] `ThinkingMode` の DB 依存を増やさない
  - `ThinkFastMode` / `ThinkDeepMode` は snapshot DTO を使う
  - DB / memory store / JSONB loader の詳細を import しない
- [ ] ログを追加する
  - depth
  - elapsed_ms
  - source_counts
  - token_budget_hint
- [ ] unit test を追加する
  - `fast` が active session の recent turns を優先する
  - active session がない時に既存 recent turns fallback が効く
  - 未実装 source は空 list / None で返る
  - builder が DB 更新系 method を呼ばない
  - budget 超過時に optional source が skipped になり、snapshot 自体は返る
  - same session recent turns が返る限り degraded context として応答可能
  - parallel source の返却順に依存せず、assemble 後の priority が安定する
  - cache hit 時も `ContextBuildTrace` に source / age_ms / ttl_ms が残る
- [ ] perf test を追加する
  - `pytest -m perf tests/perf/test_context_snapshot_latency.py`
  - `fast` は 20ms 以内
  - `normal` は 50ms 以内
  - `deep` は 100ms 以内
  - timeout / degraded path も perf test で観測する

**完了条件**:
- LLM に渡す文脈取得が `ContextSnapshotBuilder` 経由になる
- `fast` / `normal` / `deep` の snapshot build latency を perf test で測れる
- 記憶や人格情報が増えても、オンライン文脈取得の予算を一箇所で管理できる
- context build が timeout しても、最低限の same session context で応答継続できる
- context build trace により、遅延原因が DB / index / query / cache / retrieval strategy / PC 性能のどこにあるか分析できる
- `pytest -m unit` が通る

### 2026-05-24 実装結果

append-only 制約により上のチェックボックスは直接変更しないが、Phase 8.8 初段は実装済み。

- `server/shared/models.py` に `ContextDepth` / `ContextBuildPolicy` / `ContextBuildTrace` / `TomokoContextSnapshot` を追加した
- `server/gateway/context.py` に `ContextSnapshotBuilder` を追加した
  - same session recent turns / recent turns / session summary search / turn memory search / lexicon / persona slice を時間予算内で読む
  - source は `asyncio.wait(timeout=...)` で parallel に扱い、未完了 source は skipped として degraded snapshot を返す
  - cache は初段では no-op だが、`ContextBuildTrace.cache_hits` の境界は用意した
- `TomoroSession` の reply context 読み込みを builder 経由にした
  - `should_use_deep_memory()` は depth 選択へ寄せた
  - `ThinkingInput.context` / `long_term_memory` / `context_snapshot` を snapshot から作る
- `ThinkFastMode` / `ThinkDeepMode` は DB を読まず、`ThinkingInput.context_snapshot` の lexicon / persona slice だけを prompt に変換するようにした
- `server/edge/main.py` で `PostgresPersonaSnapshotStore` を `TomoroSession` に渡すようにした
- `tests/unit/test_phase88_context_snapshot.py` と `tests/perf/test_context_snapshot_latency.py` を追加した

初段の未実施・後続扱い:

- process-local TTL cache の実体実装と age / ttl trace は Phase 8.8.1 で扱う
- perf test は `fast` の 20ms 目標を固定した。`normal` / `deep` の実 DB + embedding を含む絶対値は、実データ量が増えた段階で Phase 8.8.1 として追加する

## Phase 8.8.1: ContextSnapshotBuilder 運用 hardening

Phase 8.8 の初段実装後、長期運用に耐えるための計測・劣化運転・cache 境界を固める。

- [ ] `ContextBuildTrace` を `_docs/latency.md` または debug log に出す
  - `depth`
  - `budget_ms`
  - `elapsed_ms`
  - `timed_out`
  - `included_counts`
  - `skipped_sources`
  - `stage_timings_ms`
  - `cache_hits`
- [ ] source ごとの timeout / cancellation を実装する
  - `same_session` は required
  - `recent_turns` は preferred
  - `session_summary_search` / `turn_memory_search` / `persona_slice` / `lexicon_terms` は optional
- [ ] DB connection pool を context build の parallelism と合わせて調整する
  - 1 response あたりの最大 parallel query 数を設定値にする
  - pool starvation が trace で分かるようにする
- [ ] process-local TTL cache を必要最小限で有効化する
  - `persona_state`
  - `lexicon_snapshot`
  - `recent_turns`
  - `same_session_turns`
  - `session_summary_search`
  - authoritative state は cache しない
- [ ] stale / cancelled result を捨てる
  - `session_id`
  - `turn_id`
  - `context_build_id`
  - deadline 超過後に戻った result は prompt に入れない
- [ ] regression test を追加する
  - 遅い optional query がある場合でも `max_build_ms` 内に snapshot が返る
  - cache が古くなったら DB へ fallback する
  - cache miss と DB timeout が区別して trace される
  - 同じ入力で parallel query の完了順が変わっても final snapshot の優先順位が安定する

**完了条件**:
- context build は non-blocking / parallel / budgeted に動く
- timeout は応答失敗ではなく degraded context として扱われる
- trace によりチューニング対象を局所化できる
- 単一サーバー運用では Redis なしで process-local TTL cache による高速化余地がある
- `pytest -m unit` と `pytest -m perf tests/perf/test_context_snapshot_latency.py` が通る

## Phase 8.8.5: TomoroSession 状態管理の最小足場

M2 の途中で行う。

短期記憶、長期記憶、conversation session、playback telemetry、barge-in、
interrupted turn、`ContextSnapshotBuilder` が `TomoroSession` 周辺に集まり始めている。
このままメイン層に判断を残すと、M3 の自発発話へ進んだ時に状態機械が分散して見通しが悪くなる。

ただし、M2 で本格的な event-driven architecture は導入しない。
ここでは、メイン層から判断を剥がすための最小足場だけを作る。

**目標**:
メイン層を薄い I/O adapter に寄せ、`TomoroSession` を stateful control core に近づける。
ただし既存の動作を大きく作り変えず、playback telemetry と transcript finalized の判断集約から始める。

- [ ] `TomoroRuntimeState` DTO を追加する
  - `attention_mode`
  - `vad_state`
  - `playback_state`
  - `active_session_id`
  - `active_turn_id`
  - `speaking_turn_id`
  - `context_build_id`
  - `updated_at`
- [ ] `TomoroSession.get_now_state()` を追加する
  - 現在状態の snapshot を返す
  - 外部は返された state を変更しない
- [ ] `SessionEvent` / `StateEmission` / `SessionCommand` / `TransitionResult` の最小 DTO を追加する
  - 初期実装は `type: str` + `payload: dict` でよい
  - 個別 dataclass への厳密化は M3 以降に回す
- [ ] `TomoroSession.post_event(event)` を追加する
  - 状態変更の入口を将来一本化するための public entrypoint とする
  - 既存 handler を一気に全移行しない
- [ ] playback telemetry を最初に event 化する
  - `playback_started`
  - `playback_ended`
  - active playback chunk / grace window の更新を `post_event()` 経由に寄せる
- [ ] transcript finalized の最終判断を `TomoroSession` に寄せる
  - wake word / follow-up / observer / withdrawn
  - playback echo
  - hard interrupt
  - interrupted turn 保存
  - reply generation 開始
  - これらの判断がメイン層に残らないようにする
- [ ] メイン層から判断を剥がす
  - メイン層は WebSocket / timer / backend result を `SessionEvent` に変換する
  - メイン層は `StateEmission` / `SessionCommand` を実行する
  - メイン層で participation / playback / session lifecycle の判断をしない
- [ ] `_reduce(event) -> TransitionResult` の最小実装を追加する
  - 原則として `_reduce()` 内では `await` しない
  - DB / LLM / TTS / WebSocket send は `SessionCommand` として外に出す
- [ ] unit test を追加する
  - `playback_started` event で playback state が更新される
  - `playback_ended` event で active chunk が解除される
  - active playback 中の transcript は hard interrupt 以外 echo / observer 扱いになる
  - hard interrupt では audio stop command が出る
  - hard interrupt された Tomoko turn は `interrupted` 保存 command になる
  - メイン層を通さず、`post_event()` と `TransitionResult` だけで主要状態遷移をテストできる

**完了条件**:
- playback telemetry が `post_event()` 経由で処理される
- transcript finalized の参加判断・echo・interrupt の最終判断が `TomoroSession` に寄る
- メイン層に participation / playback / session lifecycle の判断が残らない
- `TomoroRuntimeState` を `get_now_state()` で読める
- reducer が `StateEmission` / `SessionCommand` を返せる
- 既存の M2 会話・記憶・TTS・barge-in の動作が壊れない
- `pytest -m unit` が通る

### Phase 8.8.5 ではやらないこと

- 本格的な EventBus
- 外部 pub/sub
- Redis / message queue
- 状態機械ライブラリ導入
- event sourcing
- DB 永続化 event log
- 全 event の厳密 dataclass 化
- `AttentionStateMachine` / `PlaybackTracker` / `TurnLifecycleManager` の完全分離
- command runner の全面刷新

これらは、M3 で自発発話・言えなかったこと・入室時発話が増え、
実際に困った段階で Phase 10.5 以降として扱う。

### ✅ M2 完了条件

```
数日ぶりに話しかける
  → 前回の会話の文脈を踏まえた返答が来る
  → 「先週話してた〇〇、その後どうなった？」が通じる
  → 会話セッション単位の summary / embedding から関連会話を引ける
  → 用語集・人格スナップショットの version 差分から変化を追跡できる
  → ContextSnapshotBuilder の perf test で文脈取得レイテンシーを監視できる
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

## Phase 10.5: TomoroSession runtime hardening（必要になったら実施）

M3 で自発発話、入室時の初手、言えなかったことの再提示が入ると、
会話開始理由が wake word だけではなくなる。

```text
called:
  人間が「トモコ」と呼んだ

invited:
  engaged / cooldown 中の follow-up

initiative:
  Tomoko が自分から話しかけた

arrival:
  入室・存在検知に対する初手

resume_unspoken:
  interrupted turn や日記由来の「言えなかったこと」を話す
```

この段階で、M2 の最小 `post_event()` 足場だけでは見通しが悪くなった場合、
`TomoroSession` runtime を強化する。

**実施条件**:
- 自発発話と人間発話の競合が増えた
- wake word / follow-up / initiative / arrival の優先順位がメイン層に漏れ始めた
- stale な LLM delta / TTS chunk / context build result が問題になった
- `TomoroSession` の `_resolve_transcript_event()` が肥大化し、テストが読みにくくなった
- audio stop / interrupted 保存 / resume_unspoken の順序バグが出た

- [ ] `SessionEvent` を個別 dataclass へ分ける
  - `TranscriptFinalized`
  - `PlaybackStarted`
  - `PlaybackEnded`
  - `TimerTick`
  - `ContextBuildCompleted`
  - `LLMDeltaReceived`
  - `LLMCompleted`
  - `TTSChunkReady`
  - `CommandFailed`
- [ ] `TomoroSession` 内部に event queue / drain loop を追加する
  - `_event_queue: asyncio.Queue[SessionEvent]`
  - `_draining` guard
  - `_drain_events()`
  - event の逐次処理順を `TomoroSession` に閉じ込める
- [ ] command runner を整理する
  - command を `asyncio.create_task` で実行する
  - command の結果は必ず `SessionEvent` として `TomoroSession` に戻す
  - command runner は state を直接変更しない
- [ ] stale result を捨てる仕組みを強化する
  - `session_id`
  - `turn_id`
  - `chunk_id`
  - `context_build_id`
  - 現在 state と一致しない command result は stale として無視する
- [ ] 自発発話用の開始理由を state / command に追加する
  - `wake_word`
  - `followup`
  - `initiative`
  - `arrival`
  - `resume_unspoken`
- [ ] priority policy を `TomoroSession` 内に閉じ込める
  - hard interrupt > active playback echo 判定
  - withdrawn > follow-up
  - human transcript > Tomoko initiative
  - current turn > stale command result
  - same session context > long-term memory
- [ ] 必要に応じて component を切り出す
  - `AttentionStateMachine`
  - `PlaybackTracker` / `AudioTurnController`
  - `ConversationSessionManager`
  - `TurnLifecycleManager`
  - `BargeInDetector`
  - `ReplyPipeline`
  - ただし authoritative state は `TomoroSession` に残す
- [ ] regression test を追加する
  - Tomoko initiative 中に人間が話したら initiative を止める
  - arrival 発話中に wake word が来たら人間発話を優先する
  - interrupted turn が `resume_unspoken` 候補になる
  - stale LLM delta / TTS chunk が現在 turn に混ざらない
  - withdrawn 中は initiative / follow-up が抑制される

**完了条件**:
- M3 の自発発話と人間発話が競合しても、最終判断が `TomoroSession` に閉じている
- command result は必ず event として戻り、state を直接変更しない
- stale result を安全に捨てられる
- priority policy がメイン層に漏れていない
- `pytest -m unit` が通る

### Phase 10.5 でもまだやらないこと

- 外部 pub/sub 基盤
- DB 永続化 event sourcing
- 複数プロセス間 event bus
- Redis / message queue 前提の runtime 化

これらは M4 のインフラ安定化で、複数 node / 複数 process の必要が明確になった時に検討する。

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

- [ ] `persona_update` worker が `persona_state_versions` を更新する
- [ ] `persona_lexicon_versions` の用語集・関係性マーカーを persona update の入力に使う
- [ ] `prompts/persona_history/` は人間向け export として扱い、DB の versioned JSONB snapshot を正とする
- [ ] 応答生成時は最新 snapshot 全量ではなく、関連 subset だけを DTO 経由で prompt に渡す

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
- [x] 将来、クライアントから `playback_started` / `playback_ended` のテレメトリを送る余地を残す
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

---

## 2026-05-23 追記: Phase 6.6.2 STT Hallucination Filter を追加する

Phase 6.6.1.2 の follow-up 誤起動抑制は、低信頼発話を会話参加へ流さないための対策だった。
しかし MLX Whisper small の実ログで、ambient 中の partial/final transcript 自体にはまだ典型的な hallucination が出ている。
このまま `ambient_logs` に蓄積すると、将来の記憶・参加判断・デバッグの土台が汚れる。

このため、Phase 6.6.1.2 の「参加判定前ガード」だけでは不十分と判断し、
STT 結果そのものをルールベースで分類・抑制する **Phase 6.6.2: STT Hallucination Filter** を追加する。

実ログで確認した対象例:

```text
今日は また また また また...
今日は日曜日の日曜日です...
今日は1日 Have a Have Have Have...
お疲れ様でした
どこにもご視聴ください
ご視聴ありがとうございました
字幕をご視聴
```

### Phase 6.6.2: STT Hallucination Filter

**目標**: Whisper / MLX Whisper が無音・ノイズ・不安定入力で出す典型 hallucination を、
会話参加判定・ambient_logs・partial 表示の手前で低信頼として扱う。
ただし、人間の実発話を過剰に捨てないため、最初は explainable なルールベースに限定する。

- [ ] `server/edge/pipeline/stt_filter.py` を追加する
  - `TranscriptFilter` / `TranscriptFilterDecision` を定義する
  - `action`: `accept` / `suppress_partial` / `drop`
  - `reason`: `empty` / `too_short` / `known_hallucination_phrase` / `repetition_loop` / `mixed_language_loop` / `low_audio_short_text`
- [ ] partial transcript に filter を適用する
  - `suppress_partial` は画面表示・ログの noisy partial を抑える
  - final transcript は別途判定し、必要なら `drop`
- [ ] final transcript に filter を適用する
  - `drop` は participation 判定へ進めない
  - `drop` した transcript は `ambient_logs` に入れる場合も `attended=false` / `participation_mode=observer` / reason 付きにするか、完全に保存しないかを実装時に決める
- [ ] 反復ループを検出する
  - 同一 token / 短い n-gram の過剰反復
  - 「また」「Have」「今日は日曜日」などの短句反復
  - 一定長以上で unique ratio が低い transcript
- [ ] 既知定型 hallucination を検出する
  - 「ご視聴ありがとうございました」
  - 「字幕をご視聴」
  - 「お疲れ様でした」「お疲れ様です」（低音量・短文時）
  - 「チャンネル登録」「高評価」系
- [ ] 音量と長さを組み合わせる
  - 低音量かつ短文は `drop`
  - 十分な音量・自然な長さの実発話は保存する
- [ ] filter 判定を `server.session` log に出す
  - `text` / `action` / `reason` / `audio_level_db` / `attention_mode` / `is_partial`
- [ ] `tests/unit/test_stt_filter.py` を追加する
  - `また` 反復を drop
  - `Have` 反復を drop
  - `今日は日曜日の日曜日です` 反復を drop
  - 低音量の「お疲れ様でした」を drop
  - 通常発話「MLXにすると速くなっている気がする」は accept
  - wake word を含む短い発話は過剰に drop しない
- [ ] `tests/unit/test_phase3_stt.py` / `tests/unit/test_participation.py` に session 統合テストを追加する

**完了条件**:
- 実ログで出た反復 hallucination が partial 表示・参加判定に流れない
- 既知定型 hallucination が follow-up 会話継続を起こさない
- 通常の短い wake word 発話は落ちない
- filter 判定理由がログから追える
- `pytest -m unit` が通る

### 2026-05-23 実装結果

上の Phase 6.6.2 は、`TranscriptFilter` を session の partial/final transcript 入口に接続する形で実装した。
append-only 制約により既存チェックボックスは書き換えず、この追記で完了状態を記録する。

- `server/edge/pipeline/stt_filter.py` を追加し、`accept` / `suppress_partial` / `drop` を返すルールベース filter を実装した
- `TranscriptFilterDecision` DTO を `server/shared/models.py` に追加した
- partial transcript は `suppress_partial` 時に UI へ送らない
- final transcript は `drop` 時に participation 判定・ambient_logs へ進めず破棄する
- 反復ループ、混在英語ループ、既知定型 hallucination、低音量短文を検出する
- filter 判定を `server.session` log に出す
- `tests/unit/test_stt_filter.py` と `tests/unit/test_phase3_stt.py` に回帰テストを追加した
- `ruff check .` と `pytest -m unit` が通過した

### 2026-05-24 追記: TTSベンチ生成物の保存先補正

上の「聞き比べ用 WAV を `artifacts/tts-bench-cached/` に保存する」という運用は否定する。
`artifacts/` はプログラムが生成する成果物であり、リポジトリのルートに置いて git 管理する対象ではない。

今後 `_tools/bench_tts_backends.py` の出力先は `logs/tts-bench/` とする。
`logs/` は git 管理外なので、TTS 聞き比べ用 WAV はローカル生成物として扱う。
過去に commit されていた `artifacts/` 配下の WAV は git 管理対象から外す。

### 2026-05-23 境界修正

`ReplyAudioPipeline` という切り方は、emotion / image を含む reply 全体の変換責務としては音声寄りに見えすぎたため、
`session -> reply -> audio/emotion/image` の依存方向へ修正した。

- `server/gateway/reply/` を追加した
  - `pipeline.py`: `ReplyPipeline`
  - `audio.py`: `ReplyAudioPlanner`
  - `emotion.py`: `ReplyEmotionState`
  - `image.py`: `EmotionImageMapper`
- `TomoroSession` は `ReplyPipeline` だけを import する
- `ReplyPipeline` が内部で audio / emotion / image helper を使う
- 既存互換のため `server/gateway/reply_audio.py` は re-export の薄い shim にした

## Phase 6.6.3: Concurrent session hardening (TomoroSession race fixes)

 追記の趣旨: 上の方針・実装は総じて良好だが、`TomoroSession` の状態更新（`_audio_sequence`,
 `_active_audio_turn_id`, `_tomoko_speaking_until` など）に対して複数経路から同時にアクセスされる
 可能性があるため、並行性に関する防御策を明文化して実装する。

 **目標**: TomoroSession の内部状態更新を最小限のクリティカルセクションで保護し、
 WebSocket ハンドラ／STT ストリーム／TTS ストリーム／playback telemetry が同時に発生しても
 状態不整合やオーディオシーケンスの破綻が起きないようにする。

 - [ ] `server/session.py`: `TomoroSession` に `asyncio.Lock`（`self._lock`）を導入する
   - 保護対象（例）: `_begin_audio_turn` / `_ensure_audio_turn_started` / `_end_audio_turn` /
     `_stop_active_audio_turn` / `_mark_tomoko_speaking` / `_audio_sequence` の増分
   - ロックは短時間で解放すること。外部 I/O（長時間 await する操作）は原則ロック外で行う。
 - [ ] `server/session.py`: `_flush_tts_text` / `_ensure_audio_turn_started` 等の
   クリティカルセクションを `async with self._lock` で保護する（状態更新のみを囲む）
 - [ ] `server/session.py`: `handle_playback_telemetry` を `async def` に変更し、状態更新をロックで保護する
 - [ ] `server/edge/main.py`: `_handle_client_text_event` から `session.handle_playback_telemetry(...)` を `await` するよう修正
 - [ ] ドキュメント: `ARCHITECTURE.md` と `server/session.py` の主要 public メソッドに docstring を追加し、
   どのメソッドが外部から呼ばれる想定か、同期/非同期の契約を明記する
 - [ ] テスト: 並行シナリオを再現する unit/integration テストを追加
   - 同時に TTS チャンク受信と playback telemetry が来るケースで `_audio_sequence` が連番になること
   - `_stop_active_audio_turn` が並行呼び出しで turn_id を二重解放しないこと
 - [ ] `pytest -m unit` を実行し全通過を確認

 **理由**:
 - 実装方針（1 本の WebSocket / 状態の一元化）は正しく、追加の並列防御は運用強度を上げるための補強である。
 - ロック導入は最小限に留め、性能劣化を避けるために外部 I/O をロック外へ出す設計ルールを守る。

**完了条件**:
- 追加した並行テストが通る
- `pytest -m unit` が通る
- ログに `TomoroSession` の状態遷移とロック取得に関するデバッグ出力が残る（任意で短期有効）

### 2026-05-23 最小実装結果

上の Phase 6.6.3 は全項目を一度に進めず、kokoro / irodori TTS 差し替え前に効果が大きい範囲だけを先に実装した。
今回の対象は `TomoroSession` の audio turn / playback telemetry に限定する。

- `TomoroSession` に `asyncio.Lock` を追加した
- `audio_start` / `audio_end` / `audio_control stop` の送信予約を lock 内で確定し、外部 I/O は lock 外で実行するようにした
- `_audio_sequence` 採番と `_tomoko_speaking_until` 更新を短い critical section に入れた
- `handle_playback_telemetry` を `async def` に変更し、active playback chunk 更新と playback grace 更新を lock で保護した
- `/ws` の text event 処理から `handle_playback_telemetry` を `await` するようにした
- `tests/unit/test_session_concurrency.py` を追加し、`audio_start` / `audio_control stop` の二重送信防止と telemetry の async 契約を固定した

未実施:
- `/ws` 受信ループと reply/TTS 生成の本格的な並行化
- `ARCHITECTURE.md` の大きな追記
- actor/queue 化

このため Phase 6.6.3 は「最小 hardening 済み」だが、「真の並行 barge-in 対応完了」ではない。

---

## 2026-05-23 追記: Phase 6.6.4 TomoroSession responsibility split を追加する

別 LLM からの「`TomoroSession` が魔窟化する可能性があるため、早めに境界だけ切るべき」という指摘は妥当と判断する。
現状の `TomoroSession` は状態機械の集約点として設計意図に沿っているが、すでに以下を同時に抱えている。

- VAD/STT 後の発話完了処理
- transcript filter と参加判断連携
- AttentionMode の状態遷移
- BargeInDetector 連携
- reply 生成フロー
- TTS sentence flush
- audio turn / playback telemetry / stop control

上の Phase 6.6.3 の「最小 hardening」は否定しない。
ただし、kokoro / irodori TTS 差し替えや `/ws` 受信ループと reply/TTS 生成の本格並行化へ進む前に、
`TomoroSession` から副責務を切り出して、状態機械の見通しを保つ。

### Phase 6.6.4: TomoroSession responsibility split

**目標**: `TomoroSession` を会話状態機械のオーケストレーターとして残し、audio/playback と reply/TTS の副責務を小さなコンポーネントへ分離する。
ただし状態を分散させない。会話の authoritative state は引き続き `TomoroSession` に置き、切り出し先は明示的に渡された入力から結果 DTO / command を返す形にする。

- [ ] `AudioTurnController` を追加する
  - `turn_id` 発行
  - `audio_start` / `audio_end` / `audio_control stop` event の予約
  - `_audio_sequence` 採番
  - playback telemetry による active chunk / grace window 管理
  - 外部 I/O は持たず、送信すべき event / chunk metadata を返すだけにする
- [ ] `ReplyAudioPipeline` または同等の小さな helper を追加する
  - `ThinkingEvent` の `emotion` / `text_delta` / `reply_done` を処理する
  - 句読点単位の TTS flush 対象テキストを決める
  - `TTSInput` / `AudioChunkOut` 生成は既存 `TTSBackend` 抽象を使う
  - WebSocket 送信は `TomoroSession` 側に残す
- [ ] `TomoroSession` の public entrypoint を明確にする
  - `process_audio_chunk`
  - `handle_playback_telemetry`
  - 将来必要なら `cancel_current_turn`
  - それ以外は private helper または切り出し先へ移す
- [ ] 状態所有ルールを `ARCHITECTURE.md` に追記する
  - `TomoroSession` が会話 state / attention state の唯一の所有者
  - `AudioTurnController` は audio turn state の所有者だが、会話参加判断はしない
  - `ReplyAudioPipeline` は変換パイプラインであり、attention / participation を判断しない
- [ ] 既存挙動を変えない characterization test を先に追加する
  - `audio_start` が binary chunk より先に出る
  - hard interrupt で stop event が一度だけ出る
  - active playback chunk 中の回り込みが通常参加判定に流れない
  - emotion event が TTS 音声より先に出る
- [ ] リファクタ後に `tests/unit/test_session_concurrency.py` / `test_barge_in.py` / `test_phase4_thinking.py` を通す
- [ ] `pytest -m unit` を通す

**完了条件**:
- `server/session.py` が状態機械の読み筋を保ち、audio/playback と reply/TTS の詳細を直接抱え込まない
- 状態の authoritative owner が分散していない
- WebSocket エンドポイントは増えない
- クライアントへ判断ロジックを移していない
- 既存の音声再生、barge-in、playback telemetry、emotion 表示の挙動が変わらない
- `pytest -m unit` が通る

### 2026-05-23 実装結果

上の Phase 6.6.4 は、`TomoroSession` の public entrypoint と WebSocket protocol を変えずに実装した。
append-only 制約により既存チェックボックスは書き換えず、この追記で完了状態を記録する。

- `server/gateway/audio_turn.py` に `AudioTurnController` を追加した
  - `turn_id` 発行、`audio_start` / `audio_end` / `audio_control stop` event 予約、audio sequence 採番を担当する
  - playback telemetry の active chunk / grace window を管理する
  - `send_event` / `send_audio` は呼ばず、送信すべき event / chunk metadata だけを返す
- `server/gateway/reply_audio.py` に `ReplyAudioPipeline` を追加した
  - `ThinkingEvent` から emotion / reply text / TTS flush command への変換だけを担当する
  - TTS 実行と WebSocket 送信は `TomoroSession` 側に残した
- `TomoroSession` は会話状態機械、参加判断、attention 遷移、WebSocket 送信順序のオーケストレーションに寄せた
- 既存 private helper は既存テスト互換のため delegate として残した
- `ARCHITECTURE.md` に `TomoroSession` / `AudioTurnController` / `ReplyAudioPipeline` の状態所有ルールを追記した
- `tests/unit/test_audio_turn_controller.py` と `tests/unit/test_reply_audio_pipeline.py` を追加した
- `ruff check .` と `pytest -m unit` が通過した

### 2026-05-23 追記: reply 境界を audio/display に補正

上の `ReplyAudioPipeline` および `audio/emotion/image` の整理は、さらに補正する。
`emotion` と `image` は別々の副責務ではなく、reply 表示状態と表示 asset 解決の同じ関心として扱う。
将来 image 以外の pose / animation / mouth shape などが増える可能性があるため、名称も `image` ではなく
`display` に寄せる。

- `server/gateway/reply/display.py` に `ReplyDisplayPlanner` を置く
  - emotion state を所有する
  - emotion から現在の表示 asset を解決する
  - 将来の表示要素追加時もこの境界を拡張する
- `ReplyPipeline` は `ReplyAudioPlanner` と `ReplyDisplayPlanner` を束ねる
- `TomoroSession` は `ReplyPipeline` だけを知り、audio / display の個別 helper を直接 import しない
- 旧 `reply/emotion.py` / `reply/image.py` のように display concern を分散させない

**完了条件の補正**:
- reply 配下の依存方向が `session -> reply -> audio/display` になっている
- emotion event の WebSocket 互換性は維持される
- `pytest -m unit` が通る

### 2026-05-23 追記: Kokoro MLX streaming TTS 実装結果

M1 Phase 5 の「M1完了後: KokoroMLXBackend に切り替え」は、`say` を残したまま
`kokoro_mlx` backend を追加し、`config/central_realtime.toml` の default TTS backend を
`kokoro_mlx` に変更する形で進めた。

- `server/shared/inference/tts/kokoro_mlx.py` を追加した
  - `kokoro_mlx.KokoroTTS.from_pretrained()` を使う
  - `generate_stream(text, voice, speed, sample_rate)` の同期 iterator を `asyncio.to_thread` で消費する
  - numpy audio chunk を chunk ごとに RIFF/WAVE に包んで `AudioChunkOut` として返す
- `pyproject.toml` / `uv.lock` に `kokoro-mlx` と `misaki[ja]` を追加した
- `config/central_realtime.toml` に `[backends.kokoro_mlx]` を追加し、`tts_backend = "kokoro_mlx"` に変更した
- `TomoroSession` の reply 生成を background task 化した
  - `/ws` 受信ループが reply/TTS 生成で止まらない
  - sentence flush ごとに TTS queue へ即投入する
  - TTS worker は audio chunk 生成ごとに即 `/ws` へ送る
- hard interrupt 時に reply task / TTS worker を cancel し、既存の `audio_control stop` で playback も止める
- `tests/unit/test_kokoro_mlx_tts.py` と `tests/unit/test_streaming_tts_pipeline.py` を追加した
- `ruff check .` と `pytest -m unit` が通過した
- 既存 `say` perf regression は `pytest -m perf --tb=short tests/perf/test_phase5_latency.py` で通過した

未実施:
- Kokoro 実モデルの初回 download / warm-up / real latency 計測
- Kokoro 日本語音質の手動確認

**完了条件の補正**:
- `say` の同期ファイル生成は TTS worker 内に閉じ込められ、マイク入力処理を止めない
- Kokoro MLX は streaming chunk を生成次第 WebSocket binary として送れる
- barge-in hard interrupt で生成中 TTS と playback の両方を止められる
- `pytest -m unit` が通る

### 2026-05-24 追記: Irodori MLX TTS 実装結果

日本語品質改善のため、Kokoro MLX に加えて Irodori v3 の MLX backend を追加した。
最初に `Irodori-TTS-Server` の OpenAI 互換 HTTP wrapper を試したが、これは mlx-audio 版ではなく
内部 streaming も未実装だったため採用しない。

- `mlx-audio` を GitHub 最新から依存追加した
  - `mlx-audio @ git+https://github.com/Blaizzy/mlx-audio.git`
- `server/shared/inference/tts/irodori_mlx.py` を追加した
  - `mlx_audio.tts.utils.load_model()` で `mlx-community/Irodori-TTS-500M-v3-8bit` をロードする
  - Irodori v3 の生成結果を RIFF/WAVE に包んで `AudioChunkOut` として返す
  - `voice` は `"none"` なら _reference audio なし、パスなら `ref_audio` として渡す
  - emotion style は `duration_scale` の簡易マッピングで反映する
- `config/central_realtime.toml` の `tts_backend` を `irodori_mlx` に切り替えた
- mlx-audio の Irodori v3 は `stream=True` が現時点で未実装のため、Tomoko 側では sentence flush / TTS queue による文単位逐次生成とする
- 実モデル smoke で `こんにちは。` から RIFF/WAVE chunk が返ることを確認した
- キャッシュ済み短文合成は 2959.1ms、1 chunk、126,764 bytes
- `tests/unit/test_irodori_mlx_tts.py` を追加した
- `ruff check .` と `pytest -m unit` が通過した

### 2026-05-24 追記: Irodori streaming / startup warm-up 確認結果

上の Irodori MLX TTS は、MLX + Irodori v3 ではあるが、Irodori モデル内部の真の streaming ではない。
GitHub 最新 `mlx-audio` の Irodori 実装では、v2/v3 共通の `Model.generate(..., stream=True)` が
`NotImplementedError` になるため、v2 へ切り替えても streaming は得られない。

- v2 への切り替えは現時点では行わない
  - v3 は README 上 recommended
  - v3 は automatic duration prediction と sway sampling がある
  - v2 にしても streaming 不可
- `TTSBackend.warm_up()` を追加した
- FastAPI lifespan の `_warm_up_app()` で STT に続いて TTS backend も warm-up する
- `_create_default_tts_backend()` は backend instance を `app.state._default_tts_backend` に保持し、warm-up 済み instance を `/ws` で再利用する
- cached warm-up 実測は STT 1262.1ms / Irodori MLX TTS 2831.9ms
- `ruff check .` と `pytest -m unit` が通過した

### 2026-05-24 追記: Irodori MLX streaming backend 実装結果

上の「Irodori モデル内部の真の streaming は未実装」という判断は維持する。
ただし、mlx-audio の Irodori v3 は `seconds` を明示すると duration predictor をスキップでき、
短い発話単位なら warm-up 後に 100ms 前後で RIFF/WAVE chunk を返せる。

そのため、`irodori_mlx` とは別にレイテンシー優先の `irodori_mlx_stream` backend を追加した。

- `server/shared/inference/tts/irodori_mlx_stream.py` を追加した
  - `mlx_audio.tts.utils.load_model()` で同じ `mlx-community/Irodori-TTS-500M-v3-8bit` をロードする
  - text を日本語句読点と最大文字数で短い発話単位に分割する
  - 各単位を `seconds` 明示、`num_steps=6`、sway sampling で生成する
  - 生成できた単位から `AudioChunkOut` として逐次 yield する
- 既存 `irodori_mlx` は品質寄りの単発 backend として残した
- `config/central_realtime.toml` の default TTS backend を `irodori_mlx_stream` に切り替えた
- warm-up 済み実測で `うん、わかった。少し待ってね。` は first chunk 107.0ms、total 206.9ms、2 chunk
- `tests/unit/test_irodori_mlx_stream_tts.py` を追加した
- `ruff check .` と `pytest -m unit` が通過した

制約:
- これは Irodori 内部の diffusion / vocoder が生成途中で audio を吐く streaming ではない
- Tomoko の TTS backend 境界で、短い Irodori v3 生成を複数回走らせて先頭音声を早く返す方式である

### 2026-05-24 追記: Qwen3-TTS MLX backend と比較ベンチ

Irodori 以外の MLX ローカル日本語 TTS 候補として、`mlx-audio` の Qwen3-TTS backend を追加した。

- `server/shared/inference/tts/qwen3_mlx.py` を追加した
  - `mlx_audio.tts.utils.load_model()` で Qwen3-TTS MLX model をロードする
  - `Model.generate(..., stream=True)` を使う
  - 同期 generator は worker thread で消費し、chunk が出るたび `AudioChunkOut` として返す
  - `lang_code="Japanese"` を固定し、emotion style は `instruct` と `speed` に変換する
- `config/central_realtime.toml` に2つの backend を追加した
  - `qwen3_tts_mlx_small`: `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit`
  - `qwen3_tts_mlx_large`: `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`
- `_tools/bench_tts_backends.py` を追加した
  - `irodori_mlx` / `irodori_mlx_stream` / `qwen3_tts_mlx_small` / `qwen3_tts_mlx_large` を同じ文で測る
  - warm-up、first chunk、total、chunk 数、音声長を出す
  - 聞き比べ用 WAV を `artifacts/tts-bench-cached/` に保存する

キャッシュ済み実測、文は `うん、わかった。少し待ってね。`:

| backend | warm-up | first chunk | total | chunks | audio |
|---|---:|---:|---:|---:|---:|
| `irodori_mlx` | 2933.0ms | 659.2ms | 659.2ms | 1 | 3520.0ms |
| `irodori_mlx_stream` | 1310.8ms | 96.6ms | 192.7ms | 2 | 1360.0ms |
| `qwen3_tts_mlx_small` | 511.8ms | 142.6ms | 545.3ms | 8 | 2480.0ms |
| `qwen3_tts_mlx_large` | 544.2ms | 216.7ms | 820.5ms | 8 | 2480.0ms |

現時点では default backend は `irodori_mlx_stream` のままにする。
理由は first chunk と total が最短で、Tomoko の会話レイテンシー目標に最も近いためである。
ただし自然さは自動評価できないので、保存した WAV を人間が聞いて判断する。

### 2026-05-24 追記: TTS直前Gemma日本語化の実装結果

Irodori stream x1 を default のまま維持し、英字・時刻・日本語以外の文字体系がTTS文に混じった時だけ
Gemma 4 E2B で読み上げ用日本語へ正規化する。

- `server/gateway/reply/speech_normalizer.py` を追加した
  - default model は `mlx-community/gemma-4-e2b-it-4bit`
  - runner は `mlx-vlm`
  - 混入検出がない純日本語は即時 return し、Gemma を呼ばない
  - 混入ありの文だけ TTS 用日本語へ変換する
- `TomoroSession._flush_tts_text()` に正規化を挟み、TTS backend へ渡す直前にだけ変換する
- `ReplyPipeline` は表示用には従来どおり sanitizing 済み delta を使うが、TTS buffer には raw delta を保持する
  - これにより、英語を削り落としてからTTSへ渡すのではなく、Gemma が `today` / `3pm` などを日本語化できる
- `_warm_up_app()` で `ReplySpeechNormalizer.warm_up()` も実行し、Gemma 初回ロードと初回生成を起動時に前払いする
- `irodori_mlx_stream` の duration 推定は x1 に戻した
- 実測 smoke:
  - cold: 3756.2ms
  - warm: 163.2ms
  - `トモコ、today の meeting は 3pm からだよ。`
  - `トモコ、今日の会議は午後三時からですよ。`
- `ruff check .` と `pytest -m unit` が通過した
