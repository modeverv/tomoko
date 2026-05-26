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

- [x] `TomokoContextSnapshot` DTO を追加する
  - `depth`
  - `recent_turns`
  - `session_summaries`
  - `memory_hits`
  - `lexicon_terms`
  - `persona_slice`
  - `token_budget_hint`
  - `build_elapsed_ms`
  - `source_counts`
- [x] `ContextBuildPolicy` を追加する
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
- [x] `ContextBuildTrace` を追加する
  - `budget_ms`
  - `elapsed_ms`
  - `timed_out`
  - `included_counts`
  - `skipped_sources`
  - `stage_timings_ms`
  - `cache_hits`
  - `source_errors`
- [x] `ContextDepth = fast | normal | deep | reflective` を追加する
  - `fast`: active session の直近 turn
  - `normal`: fast + 関連 session summary + 関連 lexicon 少量
  - `deep`: normal + turn embedding / session 内代表 turn
  - `reflective`: 日記・人格更新用。online 対話では使わない
- [x] `ContextSnapshotBuilder` を追加する
  - 読み取り専用にする
  - session 開始/終了、summary 生成、persona update、lexicon update はしない
  - DB row / JSONB をそのまま返さず、DTO / モデルクラスへ変換する
- [x] context build を時間予算付き best-effort にする
  - `max_build_ms` を超えたら未完了 source は skipped として打ち切る
  - timeout は応答失敗ではなく degraded context として扱う
  - 同一 session の recent turns を baseline とし、長期記憶・用語集・人格 slice は optional enrichment とする
- [x] context source を parallel DB I/O で読む
  - same session recent turns
  - recent completed turns
  - session summary vector search
  - turn embedding vector search
  - persona state
  - lexicon snapshot
  - 返却順ではなく priority / relevance / recency / token budget で assemble する
- [x] `ContextSnapshotBuilder` 内部に process-local TTL cache を追加できる境界を作る
  - 初段では no-op / disabled でもよい
  - cache は DB read の speed-up のみ。source of truth にはしない
  - cache hit / miss / age_ms / ttl_ms を trace に出せるようにする
  - Redis は導入しない。単一サーバー運用中は process-local cache で十分とする
- [x] 初段の fallback 動作を実装する
  - Phase 8.5 未実装でも既存 `read_recent_turns()` で `fast` が動く
  - Phase 8.6 未実装なら `session_summaries=[]`
  - Phase 8.7 未実装なら `lexicon_terms=[]` / `persona_slice=None`
  - 既存 Phase 8 の `conversation_embeddings` は `deep` で使える
- [x] `TomoroSession` から context 読み込みを builder に寄せる
  - active `conversation_session_id` と transcript を渡す
  - `should_use_deep_memory()` 相当の判断は depth 選択へ寄せる
  - `ThinkingInput` には snapshot または snapshot から変換した context を渡す
- [x] `ThinkingMode` の DB 依存を増やさない
  - `ThinkFastMode` / `ThinkDeepMode` は snapshot DTO を使う
  - DB / memory store / JSONB loader の詳細を import しない
- [x] ログを追加する
  - depth
  - elapsed_ms
  - source_counts
  - token_budget_hint
- [x] unit test を追加する
  - `fast` が active session の recent turns を優先する
  - active session がない時に既存 recent turns fallback が効く
  - 未実装 source は空 list / None で返る
  - builder が DB 更新系 method を呼ばない
  - budget 超過時に optional source が skipped になり、snapshot 自体は返る
  - same session recent turns が返る限り degraded context として応答可能
  - parallel source の返却順に依存せず、assemble 後の priority が安定する
  - cache hit 時も `ContextBuildTrace` に source / age_ms / ttl_ms が残る
- [x] perf test を追加する
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

- [x] `ContextBuildTrace` を `_docs/latency.md` または debug log に出す
  - `depth`
  - `budget_ms`
  - `elapsed_ms`
  - `timed_out`
  - `included_counts`
  - `skipped_sources`
  - `stage_timings_ms`
  - `cache_hits`
- [x] source ごとの timeout / cancellation を実装する
  - `same_session` は required
  - `recent_turns` は preferred
  - `session_summary_search` / `turn_memory_search` / `persona_slice` / `lexicon_terms` は optional
- [x] DB connection pool を context build の parallelism と合わせて調整する
  - 1 response あたりの最大 parallel query 数を設定値にする
  - pool starvation が trace で分かるようにする
- [x] process-local TTL cache を必要最小限で有効化する
  - `persona_state`
  - `lexicon_snapshot`
  - `recent_turns`
  - `same_session_turns`
  - `session_summary_search`
  - authoritative state は cache しない
- [x] stale / cancelled result を捨てる
  - `session_id`
  - `turn_id`
  - `context_build_id`
  - deadline 超過後に戻った result は prompt に入れない
- [x] regression test を追加する
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

### 2026-05-24 実装結果

Phase 8.8.1 は、Phase 8.8 初段の builder 契約を変えずに運用 hardening として実装した。

- `ContextSnapshotBuilder` に process-local TTL cache を追加した
  - `same_session_turns` / `recent_turns` / `session_summaries` / `memory_hits` / `lexicon_terms` / `persona_slice` を短い TTL で cache する
  - cache は DB read の speed-up に限定し、active session / attention / playback などの authoritative state は cache しない
- `ContextCacheTrace` を追加し、`ContextBuildTrace.cache_entries` に source ごとの hit / age_ms / ttl_ms を残すようにした
- `ContextBuildTrace.cache_hits` と `max_parallel_sources` を builder debug log に含めた
- `ContextBuildPolicy.max_parallel_sources` を追加し、1 response あたりの source 実行並列数を policy で制限できるようにした
- deadline 超過で pending source を cancel し、完了済み source だけで degraded snapshot を assemble する挙動を regression test で固定した
- cache hit、TTL expiry 後の DB fallback、cache miss + DB timeout の trace 区別を unit test に追加した

検証:
- `mise exec -- uv run ruff check server/gateway/context.py server/shared/models.py tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase88_context_snapshot.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_context_snapshot_latency.py`

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

- [x] `TomoroRuntimeState` DTO を追加する
  - `attention_mode`
  - `vad_state`
  - `playback_state`
  - `active_session_id`
  - `active_turn_id`
  - `speaking_turn_id`
  - `context_build_id`
  - `updated_at`
- [x] `TomoroSession.get_now_state()` を追加する
  - 現在状態の snapshot を返す
  - 外部は返された state を変更しない
- [x] `SessionEvent` / `StateEmission` / `SessionCommand` / `TransitionResult` の最小 DTO を追加する
  - 初期実装は `type: str` + `payload: dict` でよい
  - 個別 dataclass への厳密化は M3 以降に回す
- [x] `TomoroSession.post_event(event)` を追加する
  - 状態変更の入口を将来一本化するための public entrypoint とする
  - 既存 handler を一気に全移行しない
- [x] playback telemetry を最初に event 化する
  - `playback_started`
  - `playback_ended`
  - active playback chunk / grace window の更新を `post_event()` 経由に寄せる
- [x] transcript finalized の最終判断を `TomoroSession` に寄せる
  - wake word / follow-up / observer / withdrawn
  - playback echo
  - hard interrupt
  - interrupted turn 保存
  - reply generation 開始
  - これらの判断がメイン層に残らないようにする
- [x] メイン層から判断を剥がす
  - メイン層は WebSocket / timer / backend result を `SessionEvent` に変換する
  - メイン層は `StateEmission` / `SessionCommand` を実行する
  - メイン層で participation / playback / session lifecycle の判断をしない
- [x] `_reduce(event) -> TransitionResult` の最小実装を追加する
  - 原則として `_reduce()` 内では `await` しない
  - DB / LLM / TTS / WebSocket send は `SessionCommand` として外に出す
- [x] unit test を追加する
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

### 2026-05-24 実装結果

Phase 8.8.5 は、本格 EventBus ではなく `TomoroSession` 内の最小 event-shaped runtime として実装した。

- `server/shared/models.py` に `TomoroRuntimeState` / `SessionEvent` / `StateEmission` / `SessionCommand` / `TransitionResult` を追加した
- `TomoroSession.get_now_state()` を追加し、attention / VAD / playback / active session / turn ID / context build ID を snapshot として読めるようにした
- `TomoroSession.post_event()` と `_reduce()` を追加した
  - playback telemetry は `playback_started` / `playback_ended` event として処理する
  - `_reduce()` は `StateEmission` / `SessionCommand` を返す最小契約を持つ
- `handle_playback_telemetry()` は `post_event()` 経由に変更した
- transcript finalized の reducer 入口を追加し、active playback 中の echo / hard interrupt を `TransitionResult` だけでテストできるようにした
  - 既存の実会話処理はまだ全面 command runner 化せず、Phase 8.8.5 の「最小足場」に留めた
- `AudioTurnController` に runtime snapshot 用の read-only property を追加した
- `tests/unit/test_phase885_session_runtime.py` を追加した

検証:
- `mise exec -- uv run ruff check server/shared/models.py server/gateway/audio_turn.py server/session.py tests/unit/test_phase885_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase885_session_runtime.py tests/unit/test_session_concurrency.py tests/unit/test_barge_in.py`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase5_latency.py`

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

上の M3 ゴールをそのまま一気に実装すると、DB schema、候補生成、LLM 判定、arrival 事前計算、
常駐 loop、docker-compose 変更が混ざり、LLM が判断を補いながら進める危険がある。

そのため Phase 9 は以下の小 Phase に分解する。
各 Phase は「テストを先に書ける単位」にし、online `/ws` 経路や `TomoroSession` にはまだ接続しない。
Phase 10 で session から候補を消費するまでは、Phase 9 は background 側の候補プール構築だけを担当する。

### Phase 9.0: candidate schema / DTO / store

**目標**: thinker が使う候補プールの DB 契約を固定する。
この Phase では LLM も常駐 loop も実装しない。

- [x] `docker/postgres/init/006_candidates.sql` を追加する
  - `utterance_candidates`
  - `arrival_candidates`
  - 必要な index
- [x] `utterance_candidates` は次の lifecycle を持つ
  - `created_at`: 候補を作った時刻
  - `expires_at`: 話せなかった場合に期限切れ扱いにする時刻
  - `spoken_at`: 実際に話した時刻。Phase 10 で更新する
  - `dismissed_at`: 話したかったが期限切れになった時刻。journalist の材料にする
  - `maturity`: `0=seed only`, `1=text ready`, `2=audio ready`
- [x] `arrival_candidates` は次の lifecycle を持つ
  - `computed_at`: 事前計算した時刻
  - `valid_until`: この時刻を過ぎた候補は使わない
  - `used_at`: Phase 10 で入室時に使ったら更新する
- [x] `server/shared/candidate.py` を追加する
  - `UtteranceCandidate`
  - `ArrivalCandidate`
  - `ArrivalBehavior = Literal["speak_first", "wait_silent", "subtle_react"]`
  - `CandidateMaturity = Literal[0, 1, 2]` または enum
  - `ArrivalContextSnapshot`
- [x] DB row から DTO へ変換し、生 `dict` を application 層で持ち回らない
- [x] `PostgresCandidateStore` を追加する
  - `insert_utterance_candidate(...)`
  - `fetch_active_utterance_candidates(now, limit)`
  - `mark_utterance_spoken(candidate_id, spoken_at)`
  - `mark_expired_utterance_candidates(now)` または `dismiss_expired_utterance_candidates(now)`
  - `insert_arrival_candidate(...)`
  - `fetch_latest_fresh_arrival_candidate(now, device_id | None)`
  - `mark_arrival_used(candidate_id, used_at)`
- [x] unit test を追加する
  - DTO round-trip
  - expired / spoken / dismissed 候補は active fetch から除外される
  - priority 降順、created_at 昇順で active candidate が返る
  - fresh な arrival candidate だけが返る
- [x] integration test を追加する
  - PostgreSQL に DDL を適用して store round-trip が通る

**完了条件**:
- 候補プールの schema / DTO / store の契約が固定される
- `pytest -m unit` が通る
- 追加した integration test が手元で通る

### 2026-05-24 実装結果

Phase 9.0 は thinker / arrival precompute が使う候補プールの DB 契約と DTO/store 境界だけを実装した。
LLM evaluator、deterministic source、常駐 loop、online `/ws` 経路への接続はまだ行わない。

- `docker/postgres/init/006_candidates.sql` を追加した
  - `utterance_candidates` / `arrival_candidates` と active / fresh fetch 用 index を作成する
  - `maturity` と `behavior` は CHECK 制約で許可値を固定する
  - `spoken_at` と `dismissed_at` が同時に立たないよう terminal state 制約を置く
- `server/shared/candidate.py` を追加した
  - `UtteranceCandidate`
  - `ArrivalCandidate`
  - `ArrivalContextSnapshot`
  - `CandidateMaturity`
  - `ArrivalBehavior`
  - `CandidateStore` / `InMemoryCandidateStore` / `PostgresCandidateStore`
- `ArrivalContextSnapshot` は schema_version 付き JSONB snapshot として保存し、DB 境界で DTO に変換する
- `PostgresCandidateStore` は active utterance fetch と fresh arrival fetch で、期限切れ / 発話済み / dismissed / 使用済み候補を除外する
- `tests/unit/test_phase90_candidates.py` と `tests/integration/test_phase90_candidates_db.py` を追加した

検証:
- `mise exec -- uv run ruff check server/shared/candidate.py tests/unit/test_phase90_candidates.py tests/integration/test_phase90_candidates_db.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase90_candidates_db.py`

### Phase 9.1: deterministic source / selection

**目標**: LLM なしで seed 候補を生成し、候補選択の最小ルールを固定する。
この Phase ではまだ LLM evaluator と arrival 事前計算は実装しない。

- [x] `server/thinker/sources/base.py` を追加する
  - `InformationSource` 抽象
  - `async def collect(context: ThinkerSourceContext) -> list[CandidateSeed]`
- [x] `server/shared/candidate.py` に `CandidateSeed` / `ThinkerSourceContext` を追加する
  - `seed_text`
  - `source`
  - `priority`
  - `expires_at`
  - `context_tags`
  - `dedupe_key`
- [x] `server/thinker/sources/time_based.py` を追加する
  - 時刻だけから deterministic な候補を作る
  - 例: 朝 / 昼 / 夜 / 深夜の軽い一言 seed
  - 外部 API や LLM は呼ばない
- [x] `server/thinker/selection/base.py` を追加する
  - `SelectionStrategy` 抽象
- [x] `server/thinker/selection/highest.py` を追加する
  - `HighestPriority`
  - priority 降順、urgent 優先、expires_at 昇順、created_at 昇順で安定選択する
- [x] dedupe 方針を固定する
  - 同じ `dedupe_key` の active candidate が存在する場合は新規 insert しない
  - `dismissed_at` / `spoken_at` 済みは別候補として再生成してよい
- [x] unit test を追加する
  - time_based source は同じ時刻入力で同じ seed を返す
  - dedupe_key が安定する
  - HighestPriority の tie-break が安定する
  - LLM / DB なしでテストできる

**完了条件**:
- LLM なしで `utterance_candidates` に seed-only candidate を積める設計が固定される
- 候補選択の tie-break がテストで固定される
- `pytest -m unit` が通る

### 2026-05-24 実装結果

Phase 9.1 は LLM / DB なしで seed 生成と候補選択の規則を固定する最小実装として完了した。

- `CandidateSeed` / `ThinkerSourceContext` を `server/shared/candidate.py` に追加した
- `InformationSource` 抽象と `TimeBasedSource` を追加した
  - 朝 / 昼 / 夜 / 深夜の時刻 bucket だけから deterministic seed を返す
  - 外部 API / LLM は呼ばない
- `SelectionStrategy` 抽象と `HighestPriority` を追加した
  - priority 降順、urgent 優先、expires_at 昇順、created_at 昇順で安定選択する
- dedupe は schema を増やさず、`context_tags` に `dedupe:<dedupe_key>` を保存する方針で固定した
  - active candidate に同じ dedupe tag があれば `insert_seed_candidate_once()` は `None` を返す
  - `spoken_at` / `dismissed_at` 済み candidate は再生成できる
- `tests/unit/test_phase91_deterministic_sources.py` を追加した

検証:
- `mise exec -- uv run ruff check server/shared/candidate.py server/thinker tests/unit/test_phase91_deterministic_sources.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py`

### Phase 9.2: LLM evaluator

**目標**: seed 候補を「話す価値があるか」「どの文面にするか」へ進める。
この Phase では音声事前生成はしない。`maturity=0 -> 1` までを扱う。

- [x] `server/thinker/evaluator/base.py` を追加する
  - `UtteranceEvaluator` 抽象
  - `async def evaluate(seed: CandidateSeed, context: ThinkerEvaluationContext) -> EvaluatedUtterance | None`
- [x] `server/shared/candidate.py` に `ThinkerEvaluationContext` / `EvaluatedUtterance` を追加する
  - `should_keep`
  - `generated_text`
  - `priority`
  - `urgent`
  - `reason`
  - `context_tags`
- [x] `server/thinker/evaluator/llm.py` を追加する
  - `InferenceRouter.select("candidate_gen", "privacy")` を使う
  - 会話原文ではなく、ContextSnapshotBuilder 由来の要約・用語・人格 subset など必要最小限だけを渡す
  - JSON response を期待し、parse failure は候補破棄または `should_keep=False` として扱う
- [x] evaluator prompt の出力 schema を `PLAN.md` または docstring に固定する
  - `should_keep: bool`
  - `generated_text: str | null`
  - `priority: float`
  - `urgent: bool`
  - `reason: str`
- [x] LLM evaluator の失敗時挙動を固定する
  - backend selection 失敗、timeout、JSON parse failure は online 会話を止めない
  - 失敗した seed は DB に保存しないか、`source_error` を log に残して捨てる
- [x] unit test を追加する
  - fake backend の JSON から `EvaluatedUtterance` を作れる
  - `should_keep=false` は保存されない
  - malformed JSON は例外を外へ漏らさず破棄される
  - privacy task として router が呼ばれる

**完了条件**:
- seed candidate を text-ready candidate に昇格できる
- LLM 失敗が background worker 内で閉じる
- `pytest -m unit` が通る

### 2026-05-24 実装結果

Phase 9.2 は、seed を LLM で評価して `maturity=1` の text-ready candidate として保存できる最小境界を実装した。
online `/ws` 経路と `TomoroSession` には接続していない。

- `ThinkerEvaluationContext` / `EvaluatedUtterance` を追加した
  - ContextSnapshotBuilder 由来の要約・用語・人格 subset を渡すための DTO
  - 生の会話原文や DB row / JSONB dict を evaluator へ持ち込まない
- `UtteranceEvaluator` 抽象と `LLMUtteranceEvaluator` を追加した
  - `InferenceRouter.select("candidate_gen", "privacy")` を使う
  - prompt docstring 相当として JSON output schema を固定した
  - malformed JSON / backend selection failure / runtime failure は `None` として捨てる
- `InferenceRouter` に `candidate_gen` role を追加し、default config では `lmstudio_gemma4_e2b` + local fallback を使う
- `CandidateStore.insert_evaluated_utterance_once()` を追加した
  - `should_keep=false` / evaluator failure は保存しない
  - `should_keep=true` は `maturity=1`、`generated_text` ありで保存する
  - dedupe は Phase 9.1 と同じ `context_tags` の `dedupe:<key>` を使う
- `tests/unit/test_phase92_llm_evaluator.py` を追加した

検証:
- `mise exec -- uv run ruff check server/shared/candidate.py server/shared/config.py server/shared/inference/router.py server/thinker tests/unit/test_phase92_llm_evaluator.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_router.py tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py tests/unit/test_phase92_llm_evaluator.py`

### Phase 9.3: arrival precompute

**目標**: 入室時の初手を 3 分以内に使える形で事前計算する。
この Phase ではまだ session から arrival candidate を消費しない。

- [x] `server/thinker/arrival.py` を追加する
  - `ArrivalPrecomputer`
  - `async def precompute_once(now, device_id | None) -> ArrivalCandidate`
- [x] `ArrivalContextSnapshot` の schema を固定する
  - `schema_version`
  - `computed_at`
  - `device_id`
  - `local_time`
  - `time_since_last_session_sec | None`
  - `session_count_today`
  - `urgent_candidate_count`
  - `top_urgent_seeds`
  - `persona_hint`
- [x] behavior を固定する
  - `speak_first`: 入室時に一言話す
  - `wait_silent`: 何も言わず待つ
  - `subtle_react`: Phase 10 以降で表示だけ変える余地。Phase 9 では保存だけ
- [x] arrival prompt の出力 schema を固定する
  - `behavior`
  - `utterance_text`
  - `reason`
- [x] LLM 失敗時 fallback を固定する
  - `behavior="wait_silent"`
  - `utterance_text=None`
  - `valid_until=now + 3 minutes`
- [x] unit test を追加する
  - fresh arrival candidate が保存される
  - LLM 失敗時に wait_silent fallback が保存される
  - `valid_until` を過ぎた candidate は fetch されない
  - context_snapshot が DTO として round-trip する
- [x] perf test を追加する
  - `precompute_once` が実 backend なしの fake 構成で十分速い
  - freshness test は `computed_at` / `valid_until` を見る

**完了条件**:
- `arrival_candidates` に常に fresh な候補を置ける
- 入室時に使うかどうかの判断材料が DB に揃う
- `pytest -m unit` が通る

### 2026-05-24 実装結果

Phase 9.3 は、入室時に消費する候補を background 側で 3 分 TTL の fresh candidate として保存する境界まで実装した。
online `/ws` 経路と `TomoroSession` からの消費はまだ行わない。

- `server/thinker/arrival.py` を追加した
  - `ArrivalPrecomputer.precompute_once(now, device_id)` が context snapshot を組み立てる
  - urgent な active utterance candidate を集め、`top_urgent_seeds` と `urgent_candidate_count` に入れる
  - optional な `ArrivalStatsReader` から `time_since_last_session_sec` / `session_count_today` / `persona_hint` を受け取る
- `ArrivalContextSnapshot` を Phase 9.3 schema へ更新した
  - `computed_at` / `local_time` / `time_since_last_session_sec` / `session_count_today` / `urgent_candidate_count` / `top_urgent_seeds` / `persona_hint`
  - DB 境界では JSONB を DTO に変換し、生 `dict` を application 層で持ち回らない
- arrival prompt の JSON schema を `behavior` / `utterance_text` / `reason` に固定した
- LLM 失敗、malformed JSON、`speak_first` なのに発話文がない場合は `wait_silent` fallback として保存する
- `tests/unit/test_phase93_arrival_precompute.py` と `tests/perf/test_phase93_arrival_precompute_latency.py` を追加した

検証:
- `mise exec -- uv run ruff check server/shared/candidate.py server/thinker/arrival.py tests/unit/test_phase93_arrival_precompute.py tests/perf/test_phase93_arrival_precompute_latency.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase90_candidates.py tests/unit/test_phase91_deterministic_sources.py tests/unit/test_phase92_llm_evaluator.py tests/unit/test_phase93_arrival_precompute.py`
- `mise exec -- uv run pytest -m perf --tb=short tests/perf/test_phase93_arrival_precompute_latency.py`

### Phase 9.4: thinker process loop

**目標**: background process として candidate generation と arrival precompute を定期実行できるようにする。
この Phase で初めて loop / CLI / Makefile / docker-compose を扱う。

- [x] `server/thinker/main.py` を追加する
  - `candidate_generation_loop`
  - `arrival_precompute_loop`
  - `asyncio.gather(...)` で並行実行
  - graceful shutdown を扱う
- [x] `background-process/run_thinker.py` を追加する
  - `--once`
  - `--watch`
  - `--candidate-interval-sec`
  - `--arrival-interval-sec`
  - default arrival interval は 180 秒
- [x] `Makefile` に entry を追加する
  - `make thinker`
  - `make thinker-once`
- [ ] docker-compose への thinker service 追加は最後に行う
  - 既存 DB service に依存する
  - online `/ws` service とは疎結合にする
  - Redis / pub-sub は導入しない
- [x] loop の観測ログを追加する
  - generated seed count
  - kept candidate count
  - arrival behavior
  - elapsed_ms
  - error count
- [x] unit test を追加する
  - `--once` 相当の runner が candidate / arrival を 1 回ずつ呼ぶ
  - interval loop は cancellation で止まる
  - source / evaluator failure が片方の loop 全体を落とさない
- [x] integration / smoke test を追加する
  - local PostgreSQL に対して `thinker-once` を実行し、candidate が保存される

**完了条件**:
- `make thinker-once` で seed candidate と arrival candidate が保存される
- `make thinker` で background loop として継続実行できる
- `pytest -m unit` が通る
- 追加した integration / smoke test が手元で通る

### 2026-05-24 実装結果

Phase 9.4 は local background process として、candidate generation と arrival precompute を once / watch で実行できる形まで実装した。
online `/ws` 経路や `TomoroSession` からの消費はまだ行わない。

- `server/thinker/main.py` を追加した
  - `ThinkerProcess.run_candidate_generation_once()` で source → seed 保存 → evaluator → text-ready 保存を実行する
  - `run_arrival_precompute_once()` で `ArrivalPrecomputer` を呼び、arrival candidate を更新する
  - `candidate_generation_loop()` / `arrival_precompute_loop()` を `asyncio.gather(...)` で並行実行できるようにした
  - source / evaluator / store / arrival の失敗は error count と log に閉じ、background worker 全体を落とさない
- `background-process/run_thinker.py` を追加した
  - `--once`
  - `--watch`
  - `--candidate-interval-sec`
  - `--arrival-interval-sec`
- `Makefile` に `make thinker` / `make thinker-once` を追加した
- loop 観測ログとして generated seed count / inserted seed count / kept candidate count / arrival behavior / elapsed_ms / error count を出す
- `tests/unit/test_phase94_thinker_loop.py` を追加した
  - once runner
  - cancellation
  - source / evaluator failure fallback
- `tests/integration/test_phase94_thinker_smoke.py` を追加し、local PostgreSQL に candidate / arrival が保存されることを確認した
- `make thinker-once` を実行し、LM Studio 経由で seed と text-ready candidate、arrival candidate が保存されることを確認した

docker-compose への thinker service 追加は、現時点では行わない。
現在の `docker/docker-compose.yml` は PostgreSQL service のみで、Tomoko アプリ用 Docker image / Dockerfile がまだない。
ここで Linux container 前提の service を追加すると、Apple Silicon / MLX / LM Studio 前提の runtime と噛み合わない半端な定義になるため、
M4 のインフラ安定化で app image 方針を決めてから追加する。

検証:
- `mise exec -- uv run ruff check server/thinker/main.py background-process/run_thinker.py tests/unit/test_phase94_thinker_loop.py tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase94_thinker_loop.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase94_thinker_smoke.py`
- `mise exec -- uv run python background-process/run_thinker.py --help`
- `make -n thinker thinker-once`
- `make thinker-once`

### Phase 9 全体の完了条件

- `utterance_candidates` に text-ready または seed-only candidate が継続的に積まれる
- `arrival_candidates` に 3 分以内の fresh candidate が維持される
- thinker は background process であり、online `/ws` 経路をブロックしない
- PostgreSQL が唯一の真実であり、Redis / pub-sub / EventBus は導入しない
- `TomoroSession` は Phase 9 では候補を消費しない。消費は Phase 10 で扱う
- `pytest -m unit` が通る

```python
@pytest.mark.perf
async def test_arrival_candidate_freshness():
    candidate = await db.fetch_latest_fresh_arrival_candidate()
    assert candidate is not None
    now = datetime.now(candidate.computed_at.tzinfo)
    assert candidate.computed_at <= now < candidate.valid_until
```

### 2026-05-24 Phase 9 全体確認結果

Phase 9.4 の「docker-compose への thinker service 追加は最後に行う」という未チェック項目は、Phase 9 の不足としては扱わない。
この項目は、現行の `docker/docker-compose.yml` が PostgreSQL service のみであり、Tomoko アプリ用 Docker image / Dockerfile がまだないため、
M4 のインフラ安定化で app image 方針を決めてから実施する項目として扱う。

したがって Phase 9 全体の完了判定は、候補プール schema / DTO / store、deterministic source、LLM evaluator、
arrival precompute、local thinker process loop が動き、`make thinker-once` と Phase 9 の unit / integration / perf 検証が通ることで満たす。

確認結果:
- `utterance_candidates` には `make thinker-once` で seed-only candidate が保存される
- `arrival_candidates` には `make thinker-once` で fresh candidate が保存される
- thinker は `background-process/run_thinker.py` / `make thinker` / `make thinker-once` の local background process として動く
- online `/ws` 経路と `TomoroSession` からの候補消費は Phase 10 に残す
- Redis / pub-sub / EventBus は導入していない

---

## Phase 10: 自発発話 + 入室時の初手

Phase 9 までは background 側で `utterance_candidates` / `arrival_candidates` を作るだけだった。
Phase 10 では、それらを online `/ws` 経路に接続し、`TomoroSession` から消費できるようにする。

この Phase の主目的は「自発発話を賢くすること」ではなく、
候補消費の入口と lifecycle 更新を `TomoroSession` の event / command 境界に固定すること。

まだ Phase 10.5 の event queue / drain loop / 個別 event dataclass へは進まない。
ただし、メイン層に priority 判断や arrival behavior 判断を置かない。
メイン層は timer / WebSocket / DB result を `SessionEvent` に変換し、
`TomoroSession` から返った `SessionCommand` を実行するだけにする。

### Phase 10.0: initiative / arrival の session 契約

**目標**: 候補消費に必要な `SessionEvent` / `SessionCommand` の文字列契約を先に固定する。

- [x] `SessionEvent` の type 契約を追加する
  - `session_started`
  - `idle_timer_elapsed`
  - `initiative_candidate_loaded`
  - `arrival_candidate_loaded`
  - `candidate_command_failed`
- [x] `SessionCommand` の type 契約を追加する
  - `fetch_initiative_candidate`
  - `fetch_arrival_candidate`
  - `start_initiative_reply`
  - `start_arrival_reply`
  - `mark_utterance_spoken`
  - `dismiss_utterance_candidate`
  - `mark_arrival_used`
- [x] command payload に必要な ID を入れる
  - `candidate_id`
  - `arrival_candidate_id`
  - `reason`: `initiative` / `arrival`
  - `started_by`: `initiative` / `arrival`
  - 必要なら `session_id` / `turn_id`
- [x] DB read / DB write は `TomoroSession._reduce()` では実行しない
  - `fetch_*` / `mark_*` は `SessionCommand` として外へ出す
  - 実行結果は `*_candidate_loaded` event として戻す
- [x] `TomoroSession` は `get_now_state()` の snapshot を読んで発話可能か判断する
  - `attention_mode == "ambient"`
  - `vad_state == "idle"`
  - `playback_state` が再生中ではない
  - `withdrawn` では initiative / arrival を抑制する

**テスト観点**:
- `idle_timer_elapsed` は発話可能 state の時だけ `fetch_initiative_candidate` を返す
- `session_started` は発話可能 state の時だけ `fetch_arrival_candidate` を返す
- `withdrawn` / playback 中 / listening 中 / processing 中は候補 fetch command を返さない
- DB read / write を mock command として観測できる

**完了条件**:
- 候補消費の判断入口が `TomoroSession.post_event()` に閉じている
- メイン層が発話可否や behavior を再判断しない形になっている
- `pytest -m unit tests/unit/test_phase10_session_contract.py` が通る

### Phase 10.1: 自発発話 candidate の消費

**目標**: idle が一定時間続いた時、active な `utterance_candidates` から 1 件を選んで話せるようにする。

- [x] idle timer は adapter 側で管理し、期限到達時に `idle_timer_elapsed` event を投げる
  - timer は state の source of truth ではない
  - timer は `TomoroSession` の state を直接変更しない
  - 現行の45秒は「自発発話判断」の間隔であり、固定で45秒ごとに発話するという意味ではない
- [x] `fetch_initiative_candidate` command runner を実装する
  - `CandidateStore.fetch_active_utterance_candidates(now, limit=...)` を呼ぶ
  - 選択は Phase 9.1 の `HighestPriority` と同じ規則にする
  - 候補なしなら `initiative_candidate_loaded` に `candidate=None` を入れて戻す
- [x] `initiative_candidate_loaded` の reducer を実装する
  - 候補なしなら何もしない
  - `generated_text` がある candidate だけ `start_initiative_reply` command を返す
  - `maturity=0` / `generated_text is None` は Phase 10 では話さず `dismiss_utterance_candidate` へ回す
- [x] 発話開始後または発話完了後に `mark_utterance_spoken` command を返す
  - 初段では「reply 開始 command を出した時点」で spoken としてよい
  - audio が失敗する場合はブラウザ側クラッシュに近く、実運用では返ってこない想定なので現状維持する
- [x] expired cleanup は物理削除ではなく `mark_expired_utterance_candidates(now)` による `dismissed_at` 更新にする
  - cleanup は thinker loop でも行う
  - online 側では initiative fetch 前の軽い command として呼んでよい
  - update 文一発で処理負荷が安いため、online 経由で実行する

**テスト観点**:
- active candidate がない時は何も話さない
- `generated_text` がある最高優先 candidate が `start_initiative_reply` になる
- `generated_text` がない seed-only candidate は online では話さない
- spoken 済み / dismissed 済み / expired candidate は選ばれない
- 発話に使った candidate は `mark_utterance_spoken` される
- expired cleanup は削除ではなく `dismissed_at` 更新になる

**完了条件**:
- 何も話しかけなくても、ambient idle 中に Tomoko が候補から一言話せる
- 自発発話 candidate の lifecycle が `spoken_at` / `dismissed_at` で追える
- WebSocket endpoint は増えていない
- クライアントに自発発話判断ロジックがない
- `pytest -m unit tests/unit/test_phase101_initiative_consumption.py` が通る

### Phase 10.2: arrival candidate の消費

**目標**: ブラウザ接続または入室検知時に、fresh な `arrival_candidates` を 1 件だけ消費する。

- [x] online adapter は接続開始時または存在検知時に `session_started` event を投げる
  - `on_session_start()` という別入口は作らず、`post_event(SessionEvent(type="session_started"))` に寄せる
  - device 判定がある場合は event payload に `device_id` を入れる
- [x] `fetch_arrival_candidate` command runner を実装する
  - `CandidateStore.fetch_latest_fresh_arrival_candidate(now, device_id)` を呼ぶ
  - fresh candidate がなければ `arrival_candidate_loaded` に `candidate=None` を入れて戻す
- [x] `arrival_candidate_loaded` の reducer を実装する
  - `candidate=None`: 何もしない
  - `behavior="speak_first"`: `start_arrival_reply` command を返す
  - `behavior="wait_silent"`: 発話せず `mark_arrival_used` command だけ返す
  - `behavior="subtle_react"`: Phase 10 では発話せず `arrival_subtle_react` emission と `mark_arrival_used` command を返す
- [x] `speak_first` なのに `utterance_text is None` の candidate は話さない
  - Phase 9.3 の fallback と同じく安全側に倒す
  - `mark_arrival_used` は実行して、同じ壊れた candidate を繰り返さない
- [x] arrival 発話は command payload 上の開始理由 `arrival` として扱う
  - ただし arrival / initiative 発話だけでは conversation session を開始しない
  - 人間が返事した時に通常の参加判断経路で conversation session を開始する
  - Phase 10.5 の `started_by` state 強化までは command payload に閉じる

**テスト観点**:
- fresh arrival candidate がない時は何も話さない
- `speak_first` は `start_arrival_reply` になる
- `wait_silent` は発話 command を返さない
- `subtle_react` は発話 command を返さず emission だけ返す
- `speak_first` で text がない場合は発話しない
- 消費した arrival candidate は `mark_arrival_used` される
- used 済み / expired / device 不一致 candidate は使われない

**完了条件**:
- ブラウザを開いた時、fresh な arrival candidate に応じて初手が出る
- `wait_silent` / `subtle_react` が勝手に発話へ化けない
- arrival の消費履歴が `used_at` で追える
- `pytest -m unit tests/unit/test_phase102_arrival_consumption.py` が通る

### Phase 10.3: online adapter / command runner 接続

**目標**: Phase 10.0〜10.2 の session 契約を、既存 `/ws` の薄い adapter として実行できるようにする。

- [x] `server/edge/main.py` または既存の WebSocket handler に command runner を追加する
  - `fetch_initiative_candidate`
  - `fetch_arrival_candidate`
  - `mark_utterance_spoken`
  - `dismiss_utterance_candidate`
  - `mark_arrival_used`
  - `start_initiative_reply`
  - `start_arrival_reply`
- [x] command runner は state を直接変更しない
  - DB / reply start / WebSocket send を実行する
  - 結果や失敗は `SessionEvent` として `TomoroSession` に戻す
- [x] idle timer loop を追加する
  - interval は短くしすぎない
  - `TomoroSession.get_now_state()` を読んで、必要な時だけ `idle_timer_elapsed` を投げる
  - timer が発話可否を決めず、最終判断は `TomoroSession` に任せる
- [x] log を追加する
  - idle timer elapsed
  - initiative fetch result
  - initiative selected / skipped reason
  - arrival fetched behavior
  - arrival used / skipped reason
  - command failed
- [ ] `_docs/latency.md` に実測を追記する
  - idle timer event から first audio chunk まで
  - arrival session_started から first audio chunk まで

**テスト観点**:
- command runner は `SessionCommand` を実行し、結果 event を `post_event()` に戻す
- command failure は WebSocket handler を落とさず `candidate_command_failed` event になる
- idle timer は withdrawn / playback 中に発話を開始しない
- `/ws` endpoint は 1 本のまま

**完了条件**:
- 実 browser session で arrival 初手と idle 自発発話が確認できる
- state 遷移と候補消費が log で追える
- `_docs/latency.md` に Phase 10 の実測が残っている
- `pytest -m unit` が通る

2026-05-24 判断: Phase 10 は unit 実装済みで完了扱いとし、browser 実測と latency 追記は後続の体験確認で行う。

### Phase 10.4: Phase 10 全体の regression / 完了判定

- [ ] Phase 10 の unit test をまとめて実行する
  - `tests/unit/test_phase10_session_contract.py`
  - `tests/unit/test_phase101_initiative_consumption.py`
  - `tests/unit/test_phase102_arrival_consumption.py`
- [ ] 必要なら integration smoke を追加する
  - test DB に initiative / arrival candidate を挿入する
  - command runner 経由で spoken_at / used_at が更新される
  - 既存候補データと干渉しないよう device_id / context_tags / inserted IDs で隔離する
- [ ] `pytest -m unit` を通す
- [ ] `pytest -m integration tests/integration/test_phase10_candidate_consumption.py` を通す
  - integration smoke を追加した場合のみ
- [ ] `pytest -m perf --tb=short` で Phase 10 追加 perf を通す
  - perf test を追加した場合のみ

**Phase 10 全体の完了条件**:
- `TomoroSession` が initiative / arrival の最終判断を持つ
- メイン層は timer / WebSocket / DB result と command runner だけを担当する
- active candidate から自発発話できる
- fresh arrival candidate から入室時の初手を出せる
- used / spoken / dismissed lifecycle が DB で追える
- WebSocket endpoint を増やしていない
- Redis / pub-sub / EventBus / event sourcing を導入していない
- `pytest -m unit` が通る

### 2026-05-24 Phase 10 人間判断

- 明示的なスタートボタンは残置する。接続時 `session_started` の現状実装は維持する
- initiative / arrival の発話可能条件は現状維持する
- 自発発話判断の45秒間隔は、発話固定間隔ではなく候補取得判断の間隔として扱う
- initiative / arrival 発話だけでは conversation session を開始しない。人間が返事した時に開始する
- `spoken_at` は reply 開始 command を出した時点でよい
- seed-only / text 未生成 candidate は online で捨てる
- expired cleanup は online 経由で行う
- `wait_silent` / `subtle_react` の used 扱いは現状維持する
- `subtle_react` の演出と emotion / image は未来で検討する
- Phase 10.5 runtime hardening は今はやらない
- Phase 10 は unit 実装済みで完了扱いにする

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
- [x] `TomoroSession` 内部に event queue / drain loop を追加する
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
- [x] 自発発話用の開始理由を state / command に追加する
  - `wake_word`
  - `followup`
  - `initiative`
  - `arrival`
  - `resume_unspoken`
- [x] priority policy を `TomoroSession` 内に閉じ込める
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

### 2026-05-25 実装結果

Phase 10.5 は、外部 EventBus や event sourcing へ広げず、`TomoroSession.post_event()` の内側だけを
小さな event queue / drain loop にした。

- `TomoroSession` に `_event_queue` / `_event_drain_lock` / `_drain_events()` / `_process_event()` を追加した
  - 複数の `post_event()` が同時に呼ばれても、`TomoroSession` 内で enqueue 順に reducer を通る
  - playback telemetry のような即時 state 反映 command も `_process_event()` 内で処理する
- Phase 10 の candidate fetch command に `request_id` を追加した
  - `fetch_initiative_candidate` / `fetch_arrival_candidate` が発行時点の request id を payload に持つ
  - `CandidateCommandRunner` は DB read 結果を `initiative_candidate_loaded` / `arrival_candidate_loaded` event として戻す時に request id を引き継ぐ
  - 古い request id の result は `stale_result` として捨てる
- human transcript / attention change で既に発話不能になっている場合は、遅れて届いた initiative / arrival result を
  既存の `not_speakable` priority で抑制する
- 既存の `SessionEvent` 文字列契約は維持した
  - 個別 dataclass 化は、event 種類がさらに増えて payload contract が読みにくくなった時に行う
- command runner は引き続き state を直接変更せず、結果を `SessionEvent` として戻す

**検証**:
- `tests/unit/test_phase105_session_runtime.py` を追加した
- `mise exec -- uv run pytest -m unit tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m unit tests/unit/test_phase885_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase10_candidate_command_runner.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run ruff check server/session.py server/gateway/candidate_commands.py tests/unit/test_phase105_session_runtime.py`

### 2026-05-25 追記: 開始理由と priority policy

上の Phase 10.5 実装結果に続き、開始理由と priority policy も `TomoroSession` 内に寄せた。

- `TomoroRuntimeState.last_start_reason` を追加した
  - `wake_word` / `followup` / `initiative` / `arrival` / `resume_unspoken` を共通語彙にする
  - `resume_unspoken` は現時点では予約語で、発話経路本体はまだ追加しない
- human transcript の `called` / `invited` は runtime 上では `wake_word` / `followup` に正規化する
  - conversation session の `start_reason` も `wake_word` / `followup` を使う
- initiative / arrival の fetch / start / mark command payload に `start_reason` を追加した
- priority policy は既存実装と追加 test で固定した
  - hard interrupt は active playback echo より優先する
  - withdrawn 中は follow-up / initiative を抑制する
  - human transcript 後に遅れて届いた initiative result は `not_speakable` で抑制する
  - stale candidate result は request id で捨てる
  - same session context は既存 Phase 8.5 / 8.8 の context test で優先済み

**検証**:
- `mise exec -- uv run pytest -m unit tests/unit/test_phase105_session_runtime.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase85_conversation_sessions.py tests/unit/test_phase885_session_runtime.py`

これらは M4 のインフラ安定化で、複数 node / 複数 process の必要が明確になった時に検討する。

---

## Phase 10.5.1: 接続状態と output target snapshot

上の Phase 10.5 は `TomoroSession` 内部の event queue / stale result / start reason を固めた。
複数クライアント同時対応では、次に「今音声を出せる接続があるか」を state として扱う必要がある。

ただし `TomoroSession` が WebSocket object や接続一覧そのものを持つことは否定する。
接続管理は adapter / gateway 側に置き、Session には抽象化された output state だけを渡す。

- [x] `ClientConnection` DTO を追加する
  - `connection_id`
  - `device_id`
  - `role`: `browser` / `edge` / `monitor`
  - `can_receive_audio`
  - `can_receive_display`
  - `connected_at`
  - `last_seen_at`
- [x] `ConnectedOutputState` DTO を追加する
  - `active_device_id`
  - `audio_target_available`
  - `display_target_available`
  - `connected_device_count`
  - `connected_connection_count`
  - `playback_state_by_device`
  - `last_presence_at`
- [x] `ClientConnectionRegistry` を追加する
  - WebSocket object は保持しない
  - 接続 facts だけから `ConnectedOutputState` を返す
  - 複数 device / 複数 connection を集約する
- [x] `TomoroRuntimeState` に `output_state` を追加する
- [x] `TomoroSession` に `connected_output_state_changed` event を追加する
  - adapter から snapshot を渡す
  - state snapshot と emission で観測できるようにする
- [x] initiative / arrival の hard gate に `audio_target_available` を追加する
  - 接続がない場合、candidate があっても online 発話を開始しない
- [x] `/ws` / `/edge/ws` の接続時に output state を Session へ渡す
  - 中央 browser は `browser` role
  - remote edge は hello 後に `edge` role として登録する

**テスト観点**:
- 接続がない `TomoroSession` は `idle_timer_elapsed` で candidate fetch command を返さない
- 接続 snapshot が入ると `TomoroRuntimeState.output_state` に反映される
- registry は WebSocket object なしで connected count / active device / audio availability を返す
- audio を受けられない monitor だけの接続では `audio_target_available=False` になる

**完了条件**:
- 複数クライアント対応のための接続 state 境界ができている
- WebSocket object は `TomoroSession` に入っていない
- output target がない時に自発発話が始まらない
- `pytest -m unit` が通る

### 2026-05-25 実装結果

Phase 10.5.1 は、長寿命 central runtime へ進む前の最小足場として実装した。

- `server/shared/models.py` に `ClientConnection` / `ConnectedOutputState` を追加した
- `server/gateway/connections.py` に `ClientConnectionRegistry` を追加した
- `TomoroRuntimeState.output_state` と `TomoroSession.connected_output_state_changed` event を追加した
- `_can_start_candidate_reply()` は `audio_target_available` が true の時だけ通るようにした
- `/ws` では接続ごとに browser output state を Session へ渡す
- `/edge/ws` では `hello` 後に edge output state を Session へ反映する

---

## Phase 10.6: TomokoDesire / Speakability model

上の Phase 10 / 10.5 は、候補消費の入口と runtime priority policy を固定する足場として維持する。
ただし、現状の 45 秒 idle timer + highest priority candidate だけでは、
Tomoko が「話したいけれど今は遠慮する」「集中中でもたまに短く茶々を入れる」といった揺らぎを表現しにくい。

この Phase では、オンライン経路に重い推論を足すのではなく、
Tomoko 側の「話したい欲」と「今話してよい度合い」を決定的なモデルとして追加する。

**目標**: 自発発話を「候補があるから話す」から
「Tomoko の desire と現在状況がしきい値を超えたから話す」へ進める。

### Phase 10.6.0: モデル契約と DTO

- [x] `TomokoDesireState` DTO を追加する
  - `desire_1m`
  - `desire_5m`
  - `desire_30m`
  - `unspoken_pressure`
  - `curiosity_pressure`
  - `attachment_pressure`
  - `playful_pressure`
- [x] `SpeakabilityState` DTO を追加する
  - `presence_1m`
  - `presence_5m`
  - `activity_1m`
  - `activity_5m`
  - `conversation_heat_1m`
  - `conversation_heat_5m`
  - `focus_likelihood_5m`
  - `recent_rejection_score`
  - `recent_acceptance_score`
  - `intrusion_penalty`
- [x] `PersonalityDynamics` DTO を追加する
  - `talkativeness`
  - `restraint`
  - `curiosity`
  - `attachment`
  - `sensitivity`
  - `playfulness`
  - `mood_talkativeness_1h`
  - `mood_restraint_1h`
  - `mood_curiosity_1h`
- [x] `CandidateSpeakDecision` DTO を追加する
  - `decision`: `speak` / `wait` / `needs_llm_judge`
  - `score`
  - `threshold`
  - `reason`
  - `signals`

**テスト観点**:
- DTO は JSON round-trip できる
- schema version を持たせ、将来のフィールド追加時に旧 snapshot を読める
- 生 dict を runtime に持ち回らず、境界で DTO に変換する

### Phase 10.6.1: load average 的な更新器

- [x] `DesireLoadAverages` または同等の helper を追加する
  - event input から 1m / 5m / 30m の指数移動平均を更新する
  - 実時間依存は `now_factory` で差し替え可能にする
- [x] desire を上げる signal を固定する
  - text-ready candidate がある
  - urgent candidate がある
  - diary / resume_unspoken 由来 candidate がある
  - arrival / presence がある
  - しばらく会話がない
- [x] desire を下げる signal を固定する
  - Tomoko が直近で話した
  - 自発発話が無反応だった
  - 「静かにして」「今いい」「あとで」系 feedback があった
  - 深夜や長時間無反応が続く
- [x] `ambient_logs` がないことを「人がいない」と断定しない
  - `ambient_logs` は発話ログであり、無言 presence ではない
  - presence 判定では `presence_reports` / VAD activity / audio level / last_human_speech_age を合わせる

**テスト観点**:
- 同じ candidate signal でも、短期 desire は早く上がり、長期 desire はゆっくり上がる
- rejection feedback は `intrusion_penalty` を即時に上げる
- 時間経過で penalty が徐々に decay する

### Phase 10.6.2: PersonalityDynamics を desire に効かせる

- [x] `PersonalityDynamics` が desire gain / decay / threshold を補正する
  - `talkativeness` が高いと desire が溜まりやすい
  - `restraint` が高いと threshold が上がる
  - `curiosity` が高いと observation / question 系 candidate が強くなる
  - `attachment` が高いと presence への反応が強くなる
  - `sensitivity` が高いと rejection 後に強く引く
  - `playfulness` が高いと短い軽口 candidate が強くなる
- [x] ランダム性は毎回の乱数ではなく、低頻度で drift する mood として扱う
  - `mood_talkativeness_1h`
  - `mood_restraint_1h`
  - `mood_curiosity_1h`
- [x] hard gate は人格変動で破れない
  - `withdrawn`
  - playback 中
  - VAD listening / processing
  - stale result
  - hard interrupt 直後

**テスト観点**:
- 同じ candidate と同じ presence でも、talkativeness が高い方が `score` が高くなる
- rejection 後は sensitivity が高いほど score が強く下がる
- `withdrawn` 中は score が高くても `wait` になる

### Phase 10.6.3: CandidateSpeakPolicy

- [x] `CandidateSpeakPolicy` を純粋判定器として追加する
  - 入力: `TomoroRuntimeState` / `TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` / candidate metadata
  - 出力: `CandidateSpeakDecision`
  - DB / LLM / WebSocket I/O を持たない
- [x] `clear_speak_threshold` / `clear_wait_threshold` を固定する
  - 明確に高い score は `speak`
  - 明確に低い score は `wait`
  - 中間帯だけ `needs_llm_judge`
- [x] candidate metadata を評価に使う
  - `priority`
  - `urgency`
  - `intrusion_risk`
  - `source`
  - `context_tags`
  - `emotional_need`
  - `expires_at`
- [x] user feedback は source / topic / emotional_need ごとに重みを変える
  - 自発発話全体を一律に上げ下げしない

**テスト観点**:
- urgent candidate は score を押し上げるが hard gate は破れない
- intrusion_risk が高い candidate は focus_likelihood が高い時に wait へ倒れる
- diary 由来など source weight が高い候補は、同じ priority でも選ばれやすい

### Phase 10.6.4: LLM judge は境界ケースだけに使う

- [x] `CandidateSpeakPolicy` が `needs_llm_judge` を返した時だけ LLM judge command を出す
- [x] LLM judge prompt は自由文ではなく JSON schema を要求する
  - `decision`: `speak_now` / `wait` / `defer`
  - `confidence`
  - `reason`
  - `tone`
  - `max_length`
- [x] LLM judge result は `SessionEvent` として `TomoroSession` に戻す
- [x] result 到着時に現在 state と合わなければ stale / not_speakable として捨てる
- [x] LLM judge failure / malformed JSON は安全側に倒して `wait` にする

**テスト観点**:
- score が明確に高い/低い時は LLM judge command が出ない
- 境界 score だけ judge command が出る
- judge result が遅れて到着し、その間に人間が話した場合は発話しない
- malformed judge result は発話しない

### Phase 10.6.5: runtime 接続

- [x] 45 秒 idle timer は「poll 間隔」として残してよいが、発話判断は `CandidateSpeakPolicy` を通す
- [x] `CandidateCommandRunner` は active candidate 取得後、policy 判定に必要な snapshot を組み立てる
- [x] `TomoroSession` は最終 gate を維持する
  - `attention_mode == ambient`
  - `vad_state == idle`
  - `playback_state == idle`
  - `withdrawn` ではない
  - stale request ではない
- [x] `TomoroSession` に LLM 推論や重い DB read を直接入れない
- [x] decision log を残す
  - candidate id
  - score / threshold
  - desire / speakability summary
  - personality modifiers
  - decision reason
  - LLM judge を呼んだか

**完了条件**:
- 自発発話の実行理由が `score` と signal で説明できる
- 「静かにして」などの feedback で次回以降の自発発話頻度が下がる
- 話したがり / 黙りたがりの personality drift が desire の上がり方に影響する
- `TomoroSession` の state transition は決定的で、オンライン LLM 失敗に引きずられない
- `pytest -m unit` が通る

### 2026-05-25 実装結果

Phase 10.6 は、オンライン経路へ重い推論を増やさず、candidate consumption の前に決定的 policy を挟む形で実装した。

- `server/shared/models.py` に `TomokoDesireState` / `SpeakabilityState` / `PersonalityDynamics` /
  `CandidateSpeakMetadata` / `CandidateSpeakDecision` を追加した
  - すべて `schema_version` と JSON round-trip を持つ
  - runtime では生 dict ではなく DTO として扱う
- `server/gateway/initiative_policy.py` を追加した
  - `DesireLoadAverages` が 1m / 5m / 30m の指数移動平均を更新する
  - `SpeakabilityLoadAverages` が presence / activity / rejection / focus / intrusion penalty を更新する
  - `CandidateSpeakPolicy` が runtime hard gate、desire、speakability、personality、candidate metadata から
    `speak` / `wait` / `needs_llm_judge` を返す
  - LLM judge 用の JSON schema prompt builder と parser を追加し、malformed result は `wait` に倒す
- `CandidateCommandRunner` が active candidate 取得後に policy snapshot を組み立て、
  `policy_decision` を `TomoroSession` に返すようにした
- `TomoroSession` は引き続き final gate と stale request check を担当する
  - `wait` は発話しない
  - `needs_llm_judge` は `judge_initiative_candidate` command を返す
  - judge 未設定 / failure は安全側に `wait`
  - request id は judge 待ちの間保持し、遅延 result を stale として捨てられる
- `tests/unit/test_phase106_initiative_policy.py` を追加し、DTO round-trip、load average、rejection decay、
  personality 補正、hard gate、candidate metadata、LLM judge parser、runtime 接続を固定した

### 2026-05-25 追加実装結果

上の初段実装で残っていた、feedback 永続化と実 LLM judge 接続を追加した。

- `docker/postgres/init/011_initiative_feedback.sql` を追加した
  - `initiative_feedback_signals` に `source` / `topic` / `emotional_need` / `feedback_kind` / `score` を保存する
  - source / topic / emotional_need ごとの時系列 index を持つ
- `server/gateway/initiative_feedback.py` を追加した
  - `CandidateFeedbackSignal` / `CandidateFeedbackStore`
  - `InMemoryCandidateFeedbackStore`
  - `PostgresCandidateFeedbackStore`
  - transcript から rejection / defer / acceptance を分類する helper
  - candidate metadata から `source` / `topic:<name>` / `emotional_need` bucket の scope を作る helper
- `TomoroSession.start_precomputed_reply()` が initiative candidate の `feedback_scope` を受け取り、
  直後の人間 transcript から scoped feedback を保存するようにした
- `CandidateCommandRunner` が candidate ごとに recent feedback summary を読み、
  `feedback_penalty` / `feedback_boost` と speakability の rejection / acceptance signal に反映するようにした
- `InitiativeLLMJudge` を追加し、境界 score の `judge_initiative_candidate` command で
  `InferenceRouter.select("candidate_gen", "privacy")` を使った JSON judge を実行できるようにした
- `server/edge/main.py` で central `/ws` と gateway `/edge/ws` の candidate runner に
  feedback store と LLM judge を接続した
- `tests/integration/test_phase106_initiative_feedback_db.py` を追加し、PostgreSQL への feedback round-trip を固定した
- ローカル PostgreSQL に `011_initiative_feedback.sql` を適用済み

## Phase 10.7: candidate runtime gate の所有者を TomoroSession に集約する

上の Phase 10.6 で `CandidateSpeakPolicy` が `TomoroRuntimeState` を受け取り runtime hard gate も見る形にした判断は補正する。
desire / speakability / personality は「話したい強さ」と「邪魔になりにくさ」の soft score を作る層であり、
runtime の最終 gate ではない。

Phase 10.7 では、candidate 発話の authoritative gate を `TomoroSession` に集約する。
`CandidateSpeakPolicy` / `CandidateCommandRunner` / `server/edge/main.py` などの外側は、
候補取得、policy snapshot 作成、command 実行、event 変換に徹し、
`attention_mode` / VAD state / playback state / output availability / stale request による発話可否判断を持たない。

### Phase 10.7.0: gate 所有ルールを固定する

- [x] `TomoroSession._can_start_candidate_reply()` を candidate 発話の唯一の runtime hard gate として扱う
- [x] `TomoroSession` の gate reason を trace / emission payload に残せるようにする
  - `attention_not_ambient`
  - `vad_not_idle`
  - `playback_not_idle`
  - `audio_target_unavailable`
  - `stale_result`
- [x] stale result check は `TomoroSession` に残す
- [x] `CandidateSpeakPolicy` は runtime hard gate reason を返さない
- [x] runner / main 側にある発話可否 gate は、正しさのための判断としては削除する
  - 将来 DB fetch 削減の早期 return を入れる場合も、authoritative gate ではないことをコメントと test 名で明示する

### Phase 10.7.1: CandidateSpeakPolicy を soft decision に寄せる

- [x] `CandidateSpeakPolicy.evaluate()` から `TomoroRuntimeState` 依存を外す
- [x] policy の入力を以下に限定する
  - `TomokoDesireState`
  - `SpeakabilityState`
  - `PersonalityDynamics`
  - `CandidateSpeakMetadata`
  - `now`
- [x] policy が扱う candidate 自体の条件は metadata に閉じる
  - text readiness
  - expiry
  - feedback penalty / boost
  - urgency / intrusion risk / emotional need
- [x] policy が `speak` を返しても、実際に発話するかは必ず `TomoroSession` が再判定する
- [x] `needs_llm_judge` の結果も `SessionEvent` として戻し、`TomoroSession` の stale / final gate を通す

### Phase 10.7.2: runner / adapter を event converter に戻す

- [x] `CandidateCommandRunner` は active candidate fetch と policy decision 作成だけを担当する
- [x] `CandidateCommandRunner` は runtime hard gate を直接判断しない
- [x] `server/edge/main.py` / gateway adapter は command 実行と event post だけを担当する
- [x] 発話できない理由は runner ではなく `TomoroSession` の emission / log に出る

### Phase 10.7.3: regression tests

- [x] policy 単体 test から runtime hard gate 期待を削除する
- [x] `TomoroSession` unit test で final gate を固定する
  - policy が `speak` でも attention が ambient でなければ話さない
  - policy が `speak` でも VAD が idle でなければ話さない
  - policy が `speak` でも playback が idle でなければ話さない
  - policy が `speak` でも audio target がなければ話さない
  - policy / LLM judge result が遅れて戻ったら stale として捨てる
- [x] runner test は「policy decision を event payload に載せる」ことだけを検証する
- [x] `pytest -m unit tests/unit/test_phase106_initiative_policy.py tests/unit/test_phase10_session_contract.py tests/unit/test_phase105_session_runtime.py` が通る
- [x] `pytest -m unit` が通る

**完了条件**:
- runtime hard gate の正は `TomoroSession` にだけある
- policy は soft decision と LLM judge band の判定に閉じている
- runner / adapter は state を読んで発話可否を決めない
- log を読めば「policy は話したがったが Session final gate で止めた」ことが説明できる

### 2026-05-25 実装結果

Phase 10.7 は、candidate runtime hard gate を `TomoroSession` に戻す形で完了した。

- `CandidateSpeakPolicy.evaluate()` から `TomoroRuntimeState` 入力を削除した
- policy は desire / speakability / personality / candidate metadata / now だけで `speak` / `wait` / `needs_llm_judge` を返す
- candidate 自体の `text_ready` / `expires_at` / feedback は metadata 条件として policy 内に残した
- `CandidateCommandRunner` は `session.get_now_state()` を読まず、active candidate fetch と policy decision 作成、event post だけを担当する
- `TomoroSession` は stale request check と final gate を保持し、`gate_reason` を emission payload と log に残す
- final gate reason は `attention_not_ambient` / `vad_not_idle` / `playback_not_idle` / `audio_target_unavailable` を返す
- policy が `speak` を返しても、`initiative_candidate_loaded` 到着時点の Session final gate で再判定される

検証:
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## Phase 10.8: AudioTurnController を純粋な制御対象に寄せる

上の Phase 6.6.4 で「既存 private helper は既存テスト互換のため delegate として残した」判断は、
現在の `TomoroSession` / `AudioTurnController` 境界では補正する。
薄い delegate が増えると、情報フローが `Session -> AudioTurnController -> Session` と細かく折り返し、
LLM 実装者には「Session が audio turn の内側も所有している」ように見える。

Phase 10.8 では、`TomoroSession` を状態と振る舞いの司令塔に残し、
`AudioTurnController` は audio turn の機械的整合性を保つ制御対象に寄せる。

### Phase 10.8.0: 責務境界を固定する

- [x] `TomoroSession` の責務を以下に限定して明文化する
  - いつ話し始めるか
  - いつ止めるか
  - barge-in / interrupt をどう扱うか
  - WebSocket event / audio をどの順序で送るか
- [x] `AudioTurnController` の責務を以下に限定する
  - `turn_id` 発行
  - `audio_start` / `audio_end` / `audio_control stop` の idempotent reservation
  - audio chunk sequence 採番
  - playback telemetry から playback state / echo grace を更新
  - `recent_tomoko_text` / speaking elapsed の read-only snapshot 提供
- [x] `AudioTurnController` は WebSocket send / DB write / TTS 実行 / reply 生成を行わない
- [x] `AudioTurnController` は会話参加判断や candidate 発話判断を行わない

### Phase 10.8.1: pass-through helper を削る

- [x] `TomoroSession` の薄い delegate helper を削る
  - `_is_tomoko_speaking`
  - `_is_playback_echo_grace_active`
  - `_is_client_playback_active`
  - `_reserve_audio_chunk`
  - `_reserve_audio_start_event`
  - `_reserve_audio_end_event`
  - `_reserve_audio_stop_event`
- [x] 呼び出し側は `self.audio_turns.<public_api>()` を直接呼ぶ
- [x] `TomoroSession._mark_tomoko_speaking()` を削除する
- [x] `AudioTurnController._mark_tomoko_speaking()` を private のままにし、外部から直接呼べない設計にする
- [x] 既存 test が private helper を叩いている場合は、`AudioTurnController` の public API test へ移す

### Phase 10.8.2: audio output の情報フローを一本化する

- [x] reply generation 経路と precomputed reply 経路で同じ audio turn API を使う
- [x] audio start は `AudioTurnController.reserve_start_event()` が返した event を `TomoroSession` が送る
- [x] audio chunk は `AudioTurnController.reserve_audio_chunk()` が sequence と speaking state を更新し、`TomoroSession` が送る
- [x] audio end / stop は `AudioTurnController.reserve_end_event()` / `reserve_stop_event()` が返した event を `TomoroSession` が送る
- [x] `TomoroSession` は audio turn の内部 field を読まない
  - 必要な情報は public property / snapshot で読む

### Phase 10.8.3: test 境界を補正する

- [x] `tests/unit/test_audio_turn_controller.py` で audio turn の idempotency / sequence / playback telemetry を固定する
- [x] `tests/unit/test_session_concurrency.py` が `TomoroSession` private helper ではなく public behavior を検証するように補正する
- [x] reply generation と precomputed reply の両方で `audio_start -> audio chunk -> audio_end` の順序を検証する
- [x] hard interrupt で `audio_control stop` が一度だけ送られることを検証する
- [x] `pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py` が通る
- [x] `pytest -m unit` が通る

**完了条件**:
- `TomoroSession` は audio turn の意味判断と I/O 順序を持ち、audio turn 内部の機械的 state は持たない
- `AudioTurnController` は純粋な制御対象として、公開 API だけで turn state を進める
- `Session -> AudioTurnController -> Session -> send_event/send_audio` の流れは残るが、折り返しは public API と event/chunk result に限定される
- private method 直呼びや薄い delegate による責務のにじみがない

### 2026-05-25 実装結果

Phase 10.8 は、Phase 6.6.4 で互換のために残していた thin delegate を削り、
`TomoroSession` から `AudioTurnController` の public API を直接呼ぶ形へ補正した。

- `TomoroSession._is_tomoko_speaking()` / `_is_playback_echo_grace_active()` /
  `_is_client_playback_active()` / `_reserve_audio_chunk()` / `_reserve_audio_start_event()` /
  `_reserve_audio_end_event()` / `_reserve_audio_stop_event()` / `_mark_tomoko_speaking()` /
  `_begin_audio_turn()` を削除した
- reply generation 経路と precomputed reply 経路は、どちらも `audio_turns.begin_turn()`、
  `reserve_start_event()`、`reserve_audio_chunk()`、`reserve_end_event()` を使う
- hard interrupt の stop は `reserve_stop_event()` の戻り event だけを `TomoroSession` が送る
- `tests/unit/test_session_concurrency.py` を private helper 依存から public behavior 検証へ補正した
- `tests/unit/test_audio_turn_controller.py` は idempotency / sequence / playback telemetry の制御対象テストとして維持した
- `ARCHITECTURE.md` と `MEMORY.md` に、AudioTurnController は機械的 audio turn state だけを持ち、
  I/O と会話判断を持たない方針を追記した

検証:
- `mise exec -- uv run pytest -m unit tests/unit/test_audio_turn_controller.py tests/unit/test_session_concurrency.py tests/unit/test_streaming_tts_pipeline.py`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`

## Phase 10.9: online parallel stop-intent queue と固定 WAV 停止応答

Phase 6.6.0 の `BargeInDetector` による hard / soft interrupt ルールは否定しない。
明示的な「ストップ」「止めて」「待って」系は、引き続きルールで即時に止める。

ただし、自然発話では「その話いったん置いといて」「今は聞けない」「あとにして」のように、
明示キーワードではないが停止・保留を意味する表現が出る。
これを online 経路で研究・改善できるよう、ルール判定の後ろで embedding / LLM stop-intent classifier を
並行に走らせる。

この Phase では、classifier result は任意タイミングで `SessionEvent` として `TomoroSession` に戻す。
結果が現在 turn に間に合い、かつ信頼度が十分なら、TTS / reply 生成が進んでいても
`audio_control stop` と固定 WAV「はい、止めます」に差し替えて会話停止まで持っていく。
結果が遅れた場合は制御には使わず、PostgreSQL に観測結果として残す。

### Phase 10.9.0: PostgreSQL を stop-intent の source of truth にする

- [x] `stop_intent_observations` テーブルを追加する
  - `id UUID PRIMARY KEY`
  - `conversation_session_id UUID NULL`
  - `turn_id TEXT NULL`
  - `transcript_id TEXT NOT NULL`
  - `transcript_text TEXT NOT NULL`
  - `rule_kind TEXT NOT NULL`
  - `adopted_action TEXT NOT NULL`
  - `playback_state_json JSONB NOT NULL DEFAULT '{}'::jsonb`
  - `reply_state_json JSONB NOT NULL DEFAULT '{}'::jsonb`
  - `status TEXT NOT NULL DEFAULT 'pending'`
  - `attempts INTEGER NOT NULL DEFAULT 0`
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
  - `locked_at TIMESTAMPTZ`
  - `completed_at TIMESTAMPTZ`
  - `error TEXT`
- [x] `stop_intent_shadow_signals` テーブルを追加する
  - `id UUID PRIMARY KEY`
  - `observation_id UUID NOT NULL REFERENCES stop_intent_observations(id)`
  - `method TEXT NOT NULL`
    - `rule`
    - `embedding`
    - `llm`
  - `model TEXT`
  - `predicted_kind TEXT NOT NULL`
    - `hard_stop`
    - `soft_stop`
    - `withdraw`
    - `defer`
    - `accept`
    - `none`
  - `confidence DOUBLE PRECISION NOT NULL`
  - `latency_ms DOUBLE PRECISION NOT NULL`
  - `raw_reason_json JSONB NOT NULL DEFAULT '{}'::jsonb`
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- [x] pending / processing / completed / error を PostgreSQL の row state として管理する
- [x] `FOR UPDATE SKIP LOCKED` で最大1件ずつ処理できる store method を用意する
- [x] retry は `attempts` と `locked_at` で管理し、古い lock は回収できるようにする
- [x] SQL 分析しやすいよう、method / predicted_kind / confidence / latency の index を追加する

### Phase 10.9.1: hot path は observation insert だけにする

- [x] `BargeInDetector` / `_withdraw_decision` / `initiative_feedback.classify_feedback()` の既存ルール判定は維持する
- [x] transcript が stop / wait / withdraw / defer / accept の候補になりうる時、`TomoroSession` は observation を作る
- [x] `/ws` hot path では embedding / LLM を直接 await しない
- [x] `TomoroSession` は observation を PostgreSQL に保存する command だけを返す
- [x] DB insert が失敗しても、stop / interrupt の本体制御を失敗させない
- [x] observation には、あとで採否を分析できる state snapshot を残す
  - current attention mode
  - current VAD state
  - playback active / echo grace
  - current reply turn id
  - first reply text emitted か
  - first audio chunk emitted か

### Phase 10.9.2: online background worker は LLM 最大1同時にする

- [x] `StopIntentClassifierWorker` を追加する
- [x] worker は PostgreSQL から pending observation を古い順に取得する
- [x] embedding classifier と LLM classifier を observation ごとに実行する
- [x] LLM classifier は最大1同時に制限する
  - process 内では `asyncio.Semaphore(1)`
  - 複数 process 起動時は PostgreSQL lock により同じ observation を二重処理しない
- [x] embedding classifier は将来 concurrency を上げられる設計にするが、初期値は1でよい
- [x] classifier failure / timeout / malformed JSON は observation を壊さず signal または error として保存する
- [x] worker は `/ws` event drain や `TomoroSession` lock の中で動かさない
- [x] backlog が増えても会話本体を止めない

### Phase 10.9.3: advisory result を SessionEvent として戻す

- [x] classifier result を `SessionEvent(type="stop_intent_classified")` として `TomoroSession` に戻せる経路を追加する
- [x] event payload は stale check に必要な id を必ず持つ
  - `observation_id`
  - `turn_id`
  - `transcript_id`
  - `method`
  - `predicted_kind`
  - `confidence`
  - `latency_ms`
- [x] `TomoroSession` は event 到着時に現在 turn と照合する
- [x] すでに別 turn に進んでいる場合は `stale_stop_intent` としてログだけ残す
- [x] confidence が低い result は制御に使わず、observation として保存する
- [x] `hard_stop` / 高信頼 `soft_stop` / `withdraw` は、現在 turn に間に合う場合だけ会話停止 command へ変換する
- [x] LLM result が遅れても、過去 turn の音声や表示を変更しない

### Phase 10.9.4: 固定 WAV「はい、止めます」で会話停止を完了する

- [x] `assets/audio/stop_ack.wav` を追加する
  - 発話文は「はい、止めます」
  - 16kHz / mono / PCM WAV を基本にする
  - 生成方法と採用 voice を `_docs/latency.md` または実装コメントに残す
- [x] `StopAckAudioProvider` を追加し、固定 WAV を `AudioChunkOut` として返せるようにする
- [x] stop-intent 採用時、`TomoroSession` は以下を順に実行する command を返す
  - current reply task cancel
  - current TTS worker cancel
  - `audio_control stop`
  - fixed WAV `audio_start`
  - fixed WAV binary chunk
  - fixed WAV `audio_end`
- [x] 固定 WAV は通常の Tomoko 返答として `conversation_logs` に保存しない
- [x] 保存する場合は control event / interrupted turn として扱う
- [x] fixed WAV の再生 telemetry は既存 playback echo protection に乗せる
- [x] fixed WAV 自体の回り込み transcript は hard interrupt 以外 `echo` として扱う

### Phase 10.9.5: 分析と安全性

- [x] SQL で rule / embedding / LLM の一致率を見られる view または query example を追加する
- [x] `classification_arrived_before_first_reply_text` を分析できるようにする
- [x] `classification_arrived_before_first_audio` を分析できるようにする
- [x] `would_have_changed_action` を後から計算できるよう、adopted action と classifier result を両方残す
- [x] LLM classifier の prompt は JSON-only schema にし、自由文で session を操作しない
- [x] LLM classifier は `InferenceRouter.select("candidate_gen", "privacy")` など privacy-safe local backend を使う
- [x] LLM classifier の raw output は長文保存せず、schema 化した `raw_reason_json` と短い reason に限定する
- [x] queue depth / avg latency / p95 latency / error count を log に出す

### Phase 10.9.6: regression tests

- [x] rule hard stop は embedding / LLM を待たず即 `audio_control stop` になる
- [x] observation insert failure でも hard stop 本体は止まる
- [x] worker は pending observation を `FOR UPDATE SKIP LOCKED` で1件だけ処理する
- [x] LLM classifier は最大1同時で実行される
- [x] stale `stop_intent_classified` event は会話制御に影響しない
- [x] current turn に間に合った high-confidence stop は fixed WAV「はい、止めます」を送る
- [x] fixed WAV は通常 conversation reply として保存されない
- [x] fixed WAV playback 中の回り込みは echo protection に入る
- [x] `pytest -m unit tests/unit/test_stop_intent_queue.py tests/unit/test_stop_ack_audio.py tests/unit/test_phase105_session_runtime.py` が通る
- [x] `pytest -m integration tests/integration/test_stop_intent_db.py` が通る
- [x] `pytest -m unit` が通る

**完了条件**:
- 明示的な stop は従来どおりルールで即停止する
- ルールで拾えない stop / wait / withdraw 表現を embedding / LLM が online background で分類できる
- LLM 推論は最大1同時で、詰まっても `/ws` hot path と `TomoroSession` event drain を止めない
- classifier result は間に合えば `TomoroSession` の stale check / final gate を通って会話停止へ反映される
- TTS や reply が進んでいても、固定 WAV「はい、止めます」で制御応答を即返せる
- すべての observation / signal / 採用 action が PostgreSQL に残り、後から SQL で分析できる

### 2026-05-25 実装結果

Phase 10.9 は、PostgreSQL queue を source of truth にして online stop-intent classifier を background worker として接続する形で実装した。

- `docker/postgres/init/012_stop_intent.sql` を追加し、`stop_intent_observations` / `stop_intent_shadow_signals` / `stop_intent_shadow_analysis` view を作成した
- `PostgresStopIntentStore` / `InMemoryStopIntentStore` を追加し、`FOR UPDATE SKIP LOCKED` による pending observation claim、signal 保存、completed / error state 更新を実装した
- `StopIntentClassifierWorker` を追加し、rule / embedding / LLM classifier を observation ごとに実行するようにした
- LLM classifier は worker 内の `asyncio.Semaphore(1)` で最大1同時に制限し、複数 worker / process 側は PostgreSQL row lock で二重処理を避ける
- `TomoroSession` は transcript hot path で observation insert だけを行い、classifier result は `SessionEvent(type="stop_intent_classified")` として stale check 後に採用する
- 高信頼 `hard_stop` / `soft_stop` / `withdraw` は `current reply task cancel -> TTS worker cancel -> audio_control stop -> fixed WAV audio_start/chunk/audio_end` の control path で処理する
- 固定 WAV は `assets/audio/stop_ack.wav` として追加した。生成コマンドは `say -v Kyoko --data-format=LEI16@16000 -o assets/audio/stop_ack.wav 'はい、止めます'`
- 固定 WAV は通常の `conversation_logs` には保存しない。control response として送信だけ行い、回り込みは既存 playback telemetry / echo protection に乗せる
- `/ws` central session では stop-intent worker を connection lifetime に合わせて起動し、result callback は `session.apply_stop_intent_event()` に戻す
- `tests/unit/test_stop_intent_queue.py` / `tests/unit/test_stop_ack_audio.py` / `tests/integration/test_stop_intent_db.py` を追加した

検証:
- `mise exec -- uv run pytest -m unit tests/unit/test_stop_intent_queue.py tests/unit/test_stop_ack_audio.py tests/unit/test_phase105_session_runtime.py`
- `mise exec -- uv run pytest -m integration tests/integration/test_stop_intent_db.py`

### 2026-05-25 追記: stop_ack.wav の声を Supertonic F1 に補正

上の「固定 WAV は `say -v Kyoko` で生成した」という実装結果は否定する。
固定 WAV「はい、止めます」は Tomoko の default voice と揃えるため、`supertonic_coreml_f1`
（Supertonic-3 CoreML / voice style F1 / Japanese / CPU_AND_NE）で再生成して
`assets/audio/stop_ack.wav` に収めた。

生成コマンド:
```bash
mise exec -- uv run python _tools/bench_tts_backends.py \
  --targets supertonic_coreml_f1 \
  --text 'はい、止めます' \
  --output-dir logs/stop-ack-supertonic-f1
cp logs/stop-ack-supertonic-f1/supertonic_coreml_f1.wav assets/audio/stop_ack.wav
```

生成結果:
- backend: `supertonic_coreml_f1`
- warmup: 8867.0ms
- first / total: 2063.5ms
- chunks: 1
- bytes: 138,430
- audio: 1569.0ms
- file format: RIFF/WAVE PCM 16-bit mono 44.1kHz

### 2026-05-25 追記: stop_ack.wav は明瞭性優先で Kyoko + tail silence に戻す

上の「Supertonic F1 で声を揃える」方針は、短い固定応答では末尾「す」が弱く、
「はい、とめま」のように聞こえるため否定する。

`local_whisper_mlx_small` で確認したところ、Supertonic F1 版 `assets/audio/stop_ack.wav` は
`四四四` と誤認識され、`はい、止めます。` / `はい、止めまーす。` / F2-F5 などの候補も安定しなかった。
一方、macOS `say -v Kyoko` 版は `はい、止めます` と認識された。

この固定 WAV は通常会話ではなく control response なので、Tomoko default voice との一致より、
停止意図が聞き取れることを優先する。

生成コマンド:
```bash
say -v Kyoko --data-format=LEI16@16000 -o logs/stop-ack-kyoko-clear/stop_ack_raw.wav 'はい、止めます。'
sox logs/stop-ack-kyoko-clear/stop_ack_raw.wav assets/audio/stop_ack.wav pad 0 0.30
```

検証:
- `assets/audio/stop_ack.wav` は RIFF/WAVE PCM 16-bit mono 16kHz
- 音声長は 1414.9ms、末尾 300ms は無音
- `local_whisper_mlx_small` で 3 runs とも `はい、止めます`

### 2026-05-25 追記: stop_ack.wav は選定済み Supertonic F1「はい、止めますね」を採用する

上の「Kyoko + tail silence に戻す」方針は、人間の聞き取りでより自然な Supertonic F1 候補が見つかったため否定する。
短い Supertonic F1 音声は `local_whisper_mlx_small` の文字起こしが安定しないため、今回の固定アセット選定では
STT 結果ではなく人間の聞き取りを最終判断にする。

採用ファイル:
- source: `logs/stop-ack-supertonic-retry/phrase_tomemasu_ne.wav`
- text: `はい、止めますね。`
- target: `assets/audio/stop_ack.wav`
- file format: RIFF/WAVE PCM 16-bit mono 44.1kHz
- audio: 1756.8ms
- bytes: 154,996

`StopAckAudioProvider.text` は制御表示用に `はい、止めますね` とする。

---

## Phase 11: 事前生成（pre-generation）

- [x] `server/thinker/pregenerator.py`
  - priority > 0.8 → テキスト + TTS まで事前生成（maturity=2）
  - priority > 0.5 → テキストだけ（maturity=1）
- [x] gateway で maturity=2 を優先的に選ぶ

**完了条件**: 高優先度の自発発話が即再生される（10ms 以内）。

### 2026-05-24 追記: Phase 11 の実装粒度を補正する

上の Phase 11 は「pregenerator」と「gateway 優先選択」だけでは実装判断が残るため、その粒度のまま進める方針は否定する。
Phase 11 は以下の小 Phase に分け、DB row / DTO / background / online 消費の順に固定する。

#### Phase 11.0: maturity=2 の保存契約

- [x] `UtteranceCandidate.generated_audio` の保存形式を RIFF/WAVE bytes として明記する
- [x] `generated_audio` は TTS backend 出力そのものであり、音声原本ではなく再生成可能な cache として扱う
- [x] `maturity=2` は `generated_text` と `generated_audio` の両方がある candidate と定義する
- [x] `InMemoryCandidateStore` / `PostgresCandidateStore` の round-trip test で `generated_audio` を固定する

**完了条件**:
- `maturity=2` candidate を保存・取得して bytes が壊れない
- `pytest -m unit tests/unit/test_phase110_pregenerated_candidate.py` が通る

#### Phase 11.1: Pregenerator

- [x] `server/thinker/pregenerator.py` を追加する
- [x] active `maturity=1` candidate のうち `priority >= 0.8` を対象にする
- [x] `TTSBackend.synthesize(TTSInput(...))` を使い、最初の audio chunk を `generated_audio` に保存する
- [x] TTS 失敗時は candidate を壊さず log に閉じる
- [x] online `/ws` 経路から pregenerator を呼ばない

**完了条件**:
- 高優先度 text-ready candidate が background で `maturity=2` へ進む
- TTS failure が online 会話に影響しない
- `pytest -m unit tests/unit/test_phase111_pregenerator.py` が通る

#### Phase 11.2: thinker loop 接続

- [x] `ThinkerProcess.run_once()` に pregeneration step を追加する
- [x] `make thinker-once` で candidate generation → evaluation → pregeneration → arrival precompute の順に実行する
- [x] pregeneration count / error count / elapsed_ms を log に出す
- [x] `make thinker` の loop でも periodic に pregeneration を実行する

**完了条件**:
- `make thinker-once` で `maturity=2` candidate が作られる
- `pytest -m unit tests/unit/test_phase112_thinker_pregeneration.py` が通る

#### Phase 11.3: gateway maturity=2 消費

- [x] `CandidateCommandRunner` は `generated_audio` 付き candidate を優先する
- [x] `start_initiative_reply` payload に `generated_audio` を載せる
- [x] `TomoroSession.start_precomputed_reply()` は audio cache があれば TTS を呼ばずに送る
- [x] `generated_audio` を使った場合も `reply_text` / `audio_start` / binary / `audio_end` / `reply_done` の順序を守る

**完了条件**:
- 高優先度自発発話が TTS 推論なしに再生される
- `pytest -m unit tests/unit/test_phase113_pregenerated_audio_consumption.py` が通る

補足: 現行の既存音声経路に合わせ、実装済みの precomputed reply は
`reply_text` / `audio_start` / binary / `reply_done` / `audio_end` の順序で送る。
`audio_end` と `reply_done` の厳密な順序を変える場合は、既存 TTS 経路全体の互換性を確認してから行う。

#### Phase 11.3 追記: 2026-05-24 人間判断による順序補正

上の補足にある「既存音声経路に合わせて `reply_done` を `audio_end` より先に送る」方針は否定する。
Phase 11.3 以降は PLAN の経路を正とし、既存 TTS 経路も含めて
`reply_text` / `audio_start` / binary / `audio_end` / `reply_done` の順序へ寄せる。

#### Phase 11.4: multi-chunk 事前生成 audio の別テーブル

- [x] `generated_audio` は first RIFF/WAVE chunk cache として維持する
- [x] 完全な multi-chunk 事前生成は `generated_audio` カラムや JSONB manifest ではなく別テーブルに分離する
- [x] `pregenerated_audio_chunks` DDL を追加する
- [x] `PregeneratedAudioChunk` DTO を追加する
- [x] `InMemoryPregeneratedAudioChunkStore` / `PostgresPregeneratedAudioChunkStore` を追加する
- [x] chunk order / `is_last` / bytes round-trip を unit / integration test で固定する
- [ ] pregenerator が全 chunk を保存する実装へ拡張する
- [ ] gateway が multi-chunk cache を順序付きに送信する実装へ拡張する

**完了条件**:
- `generated_audio` は従来どおり最初の即再生 chunk として使える
- multi-chunk の保存先と読み出し順序が DB/DTO/store で固定されている
- `pytest -m unit tests/unit/test_phase110_pregenerated_candidate.py tests/unit/test_phase113_pregenerated_audio_consumption.py` が通る

---

## Phase 12: journalist（日記）

- [x] `diary` テーブル作成
- [x] `server/journalist/main.py`: 定期実行
  - conversation_logs + ambient_logs + dismissed 候補 + 感情ログを読む
  - LLM に日記を書かせる
  - dismissed_at の候補から「言えなかったこと」を自然に書かせる
- [x] `server/thinker/sources/diary.py`: `DiarySource`
  - 昨日の日記から utterance_candidates に候補を積む
- [ ] docker-compose に journalist サービス追加

**完了条件**:
- 日記が毎日書かれる
- 翌日に「昨日日記に書いたんだけど」と話しかけてくる
- 「言えなかったこと」が日記に記録されている

### 2026-05-24 追記: Phase 12 の実装粒度を補正する

上の Phase 12 は DB / prompt / scheduler / DiarySource が混ざっているため、そのまま実装する方針は否定する。
journalist は online 経路に入れず、閉じた session と dismissed candidate を読む background worker として分解する。

#### Phase 12.0: diary schema / DTO / store

- [x] `diary_entries` テーブルを作成する
  - `id`
  - `diary_date`
  - `body_text`
  - `source_session_ids`
  - `source_candidate_ids`
  - `mood`
  - `schema_version`
  - `created_at`
- [x] `server/shared/diary.py` に `DiaryEntry` / `DiaryStore` / `PostgresDiaryStore` を追加する
- [x] 同じ日付の再生成は insert duplicate ではなく version または overwrite 方針を明記してから実装する

**完了条件**:
- diary entry の DB round-trip ができる
- `pytest -m unit tests/unit/test_phase120_diary_store.py` が通る

#### Phase 12.1: Journalist input builder

- [x] `conversation_sessions` の completed summary を日付範囲で読む
- [x] `conversation_logs` の interrupted / completed turn を日付範囲で読む
- [x] `ambient_logs` は raw 全量ではなく count / 印象的な短い抜粋だけに絞る
- [x] dismissed / unspoken candidate を source として読む
- [x] `JournalistInputSnapshot` DTO にまとめ、生 DB row / dict を prompt 層へ渡さない

**完了条件**:
- 日記生成に渡す材料が DTO として固定される
- `pytest -m unit tests/unit/test_phase121_journalist_input.py` が通る

#### Phase 12.2: Diary writer

- [x] `server/journalist/main.py` を追加する
- [x] `InferenceRouter.select("diary", "privacy")` で日記本文を生成する
- [x] malformed / empty output は `error` log に閉じ、原本を変更しない
- [x] generated diary は `DiaryStore` に保存する

**完了条件**:
- fake backend で日記が 1 件保存される
- `pytest -m unit tests/unit/test_phase122_journalist_writer.py` が通る

#### Phase 12.3: DiarySource

- [x] `server/thinker/sources/diary.py` を追加する
- [x] 昨日または直近 diary から `CandidateSeed` を作る
- [x] seed は `dedupe:diary:<diary_id>` で重複生成を避ける
- [x] diary 本文全量ではなく短い話しかけ候補だけを seed にする

**完了条件**:
- 日記由来 candidate が thinker に積まれる
- `pytest -m unit tests/unit/test_phase123_diary_source.py` が通る

#### Phase 12.4: local process / Makefile

- [x] `background-process/run_journalist.py` を追加する
- [x] `make journalist-once` / `make journalist` を追加する
- [x] docker-compose service 化は app image 方針が決まるまで M4 に送る

**完了条件**:
- `make journalist-once` がローカルで実行できる
- `pytest -m unit` が通る

#### Phase 12 実装結果: 2026-05-24

Phase 12 の docker-compose service 追加は、Phase 9.4 thinker service と同じ理由で現時点では否定する。
Tomoko アプリ用 Docker image / Apple Silicon MLX / LM Studio runtime 方針が M4 で決まるまで、
local process entrypoint と Makefile target までを Phase 12 の完了範囲とする。

同日 diary 再生成は overwrite ではなく version 方式とする。
`diary_entries.diary_version` を追加し、同じ `diary_date` の追加生成は `1, 2, 3...` と版を積む。
原本は `conversation_logs` / `ambient_logs` / `conversation_sessions` / `utterance_candidates` に残し、
日記は再生成可能な解釈ログとして保存する。

実装済み:
- `JournalistInputBuilder` / `PostgresJournalistSourceReader`
- `DiaryWriter`
- `DiarySource`
- `background-process/run_journalist.py`
- `make journalist-once` / `make journalist`
- `InferenceRouter.select("diary", "privacy")`

検証:
- `pytest -m unit tests/unit/test_phase120_diary_store.py tests/unit/test_phase121_journalist_input.py tests/unit/test_phase122_journalist_writer.py tests/unit/test_phase123_diary_source.py tests/unit/test_phase124_journalist_process.py tests/unit/test_router.py`
- `pytest -m integration tests/integration/test_phase120_diary_db.py`
- `pytest -m unit`

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

### 2026-05-24 追記: Phase 13 の実装粒度を補正する

上の Phase 13 は cloud fallback まで一気に進める粒度であり、privacy 境界を誤りやすいため、そのまま実装する方針は否定する。
まず monitor / metrics / routing policy / cloud backend の順に分ける。

#### Phase 13.0: inference_metrics schema / DTO

- [x] `inference_metrics` テーブルを作成する
- [x] `InferenceMetricSample` DTO を追加する
- [x] backend name / task type / latency / error / measured_at を保存する
- [ ] unit test と integration smoke で保存・最新取得を固定する

#### Phase 13.1: BackendHealthMonitor

- [x] `server/shared/inference/monitor.py` を追加する
- [x] backend の `warm_up` または短い probe を使って latency を測る
- [x] probe failure は metric error として保存し、router 例外にしない
- [x] background で periodic probe できるが、online select では重い probe を実行しない

#### Phase 13.2: routing policy

- [ ] `InferenceRouter.select()` を実測 metric 参照に対応させる
- [ ] `priority="privacy"` では `privacy_allowed=False` backend を絶対に返さない
- [ ] fallback が privacy 不可なら local backend を返す
- [ ] task type ごとの backend / fallback を unit test で全パターン固定する

#### Phase 13.3: AnthropicBackend

- [ ] `AnthropicBackend` を追加する
- [ ] `privacy_allowed=False` を固定する
- [ ] conversation privacy では選ばれないことを test で保証する
- [ ] cloud を使う task は privacy 非依存 task に限定する

**完了条件**:
- latency fallback と privacy block が両方テストで保証される
- `pytest -m unit tests/unit/test_phase13_inference_router_hardening.py` が通る

---

## Phase 14: エッジ分離 + 回り込み除去
edgeと中央の通信プロトコルはwebsocketとする。
下記とする。
```
edge Python
-> gateway Python に WebSocket 接続
-> speech / presence / playback telemetry を JSON で送る
-> 切れたら再接続
-> 切断中の古い speech は基本捨てる

gateway Python
-> device_id ごとに接続管理
-> event_id / transcript_id で重複排除
-> observed_at が古すぎる event は捨てる
-> TomoroSession に渡すのは fresh な text event だけ
```
- heartbeat: 5秒ごとに ping 的な JSON を送る
- reconnect: 0.5s → 1s → 2s → 5s 上限で再接続
- event_id: UUID で重複処理を防ぐ
- observed_at: 古い発話を復旧後に処理しない

--- 

- [ ] `server/edge/main.py` をエッジ専用に整理
  - STT 結果をテキストで中央に送信（音声は外に出さない）
- [x] `presence` / `edge_status` テーブル作成
- [x] `server/gateway/resolver.py`: `DirectSpeakerResolver`
- [x] `server/gateway/dedup.py`: `DuplicateSpeechFilter`
- [x] `server/gateway/presence.py`: `PresenceManager`
- [x] `config/edge_kitchen.toml` 作成
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

### 2026-05-24 追記: Phase 14 の実装粒度を補正する

上の Phase 14 はプロセス分離、presence、dedupe、docker-compose が混ざっているため、そのまま実装する方針は否定する。
まず DB 契約と純粋判定器を固定し、音声を中央へ送らない境界を守る。

#### Phase 14.0: presence / edge_status schema

- [x] `presence_reports` / `edge_status` テーブルを作成する
- [x] `PresenceReport` / `EdgeStatus` DTO を追加する
- [x] 音声 bytes は保存しない
- [x] device_id / audio_level_db / observed_at / transcript_id を保存する

#### Phase 14.1: DirectSpeakerResolver

- [x] `server/gateway/resolver.py` を追加する
- [x] 同一時間窓の presence から正規 device を選ぶ
- [x] 初段は audio_level_db 最大 + recency で deterministic に決める
- [x] resolver は DB write を持たない純粋判定器にする

#### Phase 14.2: DuplicateSpeechFilter

- [x] `server/gateway/dedup.py` を追加する
- [x] 時間窓、device 差、文字列類似度で duplicate を判定する
- [x] embedding 類似度を主判定にしない
- [x] hard interrupt keyword は duplicate より優先する

#### Phase 14.3: edge / gateway process split

- [ ] edge は VAD / STT / TTS / presence report を担当する
- [ ] gateway は text event / DirectSpeakerResolver / TomoroSession を担当する
- [ ] WebSocket は各 edge と gateway 間でも 1 本の protocol を保つ
- [ ] 音声データは edge 外へ出さない

#### Phase 14.4: local multi-process smoke

- [x] `config/edge_kitchen.toml` / `config/central_realtime.toml` の責務差を固定する
- [x] `make edge-kitchen` / `make gateway` を追加する
- [x] docker-compose service 化は app image 方針が決まってから行う

**完了条件**:
- 二重 STT / 回り込みが duplicate として抑制される
- 音声 bytes が中央 DB / gateway に流れない
- `pytest -m unit tests/unit/test_phase14_edge_split.py` が通る

#### Phase 14.3 実装結果: 2026-05-25

Phase 14.3 は、ack / retry / durable queue / Redis を導入せず、plain WebSocket 1 本の text event protocol として実装した。

実装済み:
- `server/shared/edge_protocol.py` を追加し、edge -> gateway の `hello` / `presence` / `speech` /
  `playback_started` / `playback_ended` JSON protocol を固定した
- `speech` event は `device_id` / `event_id` / `transcript_id` / `transcript` / `audio_level_db` /
  `observed_at` / `sent_at` を持ち、音声 bytes は含めない
- 中央サーバーに `/edge/ws` を追加し、edge process からの text event を受ける
- 中央サーバーの既存 `/ws` と `/` client 配信は維持し、中央 PC 単体でもブラウザ client として使える
- `GatewayEdgeProtocolHandler` が presence report、primary edge 判定、duplicate 判定、stale / duplicate event discard を行い、
  fresh な `speech` だけを `TomoroSession.process_transcript()` に渡す
- `TomoroSession.process_transcript()` を追加し、ローカル STT 済み transcript と remote edge transcript の入口を共通化した
- edge role かつ `node.gateway_ws_url` がある場合、edge `/ws` はブラウザ音声を VAD/STT し、
  STT 後 `speech` event だけを `/edge/ws` へ送る
- edge は gateway から返る `reply_text` / `emotion` / `reply_done` をブラウザへ転送し、
  edge local TTS で audio chunk を生成してブラウザへ送る
- edge role の startup warm-up は `gateway_ws_url` がある場合 STT/TTS までに留め、中央 inference warm-up をスキップする

未実装:
- reconnect backoff / heartbeat / connection health UI
- ack / retry / durable queue
- 複数 edge 接続をまたいだ長時間 soak test
- 実 LAN 上の `/edge/ws` first reply / first audio latency 計測

検証:
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration tests/integration/test_phase14_presence_db.py`
- `make -n edge-kitchen gateway`
- `git diff --check`

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

### 2026-05-24 追記: Phase 15 の実装粒度を補正する

上の Phase 15 は config 追加と品質評価だけでは fallback 境界が曖昧なため、そのまま実装する方針は否定する。
edge LLM は「中央が詰まった時の短い返答 fallback」として、privacy と latency を守る形で分解する。

#### Phase 15.0: edge config contract

- [ ] `config/edge_kitchen.toml` を追加する
- [ ] `node.role="edge"` / `device_id="kitchen"` を固定する
- [ ] `conversation_backend="central_gateway"` / fallback `local_gemma` のように責務を明記する
- [ ] config load unit test を追加する

#### Phase 15.1: local_gemma backend

- [ ] edge 用 backend spec を `InferenceRouter` で読めるようにする
- [ ] `gemma3:2b` または MLX 相当の軽量 backend を追加する
- [ ] edge fallback は privacy_allowed=True の local backend のみにする

#### Phase 15.2: central down fallback smoke

- [ ] central backend failure を fake monitor / fake backend で再現する
- [ ] edge router が local_gemma を選ぶことを test で固定する
- [ ] privacy task が cloud に出ないことを再確認する

#### Phase 15.3: quality / latency memo

- [ ] edge local_gemma の応答品質差を `_docs/edge_llm.md` に記録する
- [ ] first token latency / first audio latency を `_docs/latency.md` に記録する
- [ ] 品質が低い場合は「短い安全な返答」用途に限定する

**完了条件**:
- 中央 unavailable 時に edge local LLM で短い返答ができる
- privacy task は edge local から外へ出ない
- `pytest -m unit tests/unit/test_phase15_edge_llm.py` が通る

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

### 2026-05-24 M4 完了条件確認

現時点では M4 は未達。
Phase 13.0 / 13.1 の monitor 初段は入ったが、以下が残っている。

- [ ] latency fallback を実機 metric store / periodic probe と接続する
- [ ] cloud backend を privacy 非依存 task 限定で追加する
- [ ] Phase 14 の edge / gateway 分離を実装する
- [ ] DirectSpeakerResolver / DuplicateSpeechFilter を実装する
- [ ] Phase 15 の edge local LLM fallback を実装する
- [ ] M4 完了条件の手動 smoke を実施して `_docs/latency.md` / `_docs/edge_llm.md` に記録する

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

---

## 2026-05-25 追記: Phase 18 外部観測 Markdown と Tomoko 解釈パイプライン

上の「将来の拡張アイデア」にある `NewsSource（RSS から話題を取る）` は、そのまま実装すると
外部 API / browser automation / parsing / 記憶化 / 自発発話 candidate が一体化しすぎる。
この方針は否定する。

Phase 18 では、外部情報の取得を Tomoko 本体の機能ではなく unreliable sensor として扱う。
Perplexity や Codex Computer Use は最外周の収集手段であり、Tomoko の記憶へ直接書き込まない。
生の外部情報は Markdown file として filesystem に残し、Tomoko がどう解釈したかだけを PostgreSQL に保存する。

設計原則:
- `/ws` / `TomoroSession` の hot path では外部情報取得、Markdown parse、LLM normalize を行わない
- Perplexity / Codex Computer Use / Markdown 出力は不安定であることを通常系として扱う
- ルールベースでニュース内容を理解しない。ルールは file layout、frontmatter、schema validation、archive / failed 移動に限定する
- raw Markdown は Tomoko が信じる事実ではなく、外部観測の原稿である
- DB に保存するのは raw document の checksum / metadata と、LLM が schema validation を通して作った解釈である
- Tomoko の persona / lexicon / user relation は「何を重要視するか」「どう覚えるか」に効かせる
- thinker / journalist は validated interpretation だけを読む。raw Markdown を直接 prompt に入れない
- public repo では `informations/work` / `archived` / `failed` の実データを git 管理しない。sample だけを置く

### Phase 18.0: informations directory contract

**目標**: 外部情報収集の raw artifact を、人間にも機械にも追える filesystem layout に隔離する。

- [x] `informations/` directory を追加する
  - `informations/work/`: 未取り込み、または取り込み待ちの raw Markdown
  - `informations/archived/`: 正常取り込み済み raw Markdown
  - `informations/failed/`: parse / validation / normalize 失敗 raw Markdown
  - `informations/prompts/`: Perplexity / Codex Computer Use に渡す収集 prompt
  - `informations/samples/`: public repo に置けるダミー artifact
- [x] `.gitignore` に実データ directory を追加する
  - `informations/work/`
  - `informations/archived/`
  - `informations/failed/`
- [x] `informations/README.md` を追加し、raw Markdown は source of truth ではなく external observation artifact であることを書く
- [x] `informations/prompts/daily_world_observation.md` を追加する
  - Perplexity に 1 万字程度の日本語 Markdown を出させる
  - news / economy / technology / culture / local life / AI / local inference などの topic を含める
  - machine-readable を目指すが、揺れる前提でよいと明記する
- [x] `informations/samples/` に架空内容の sample Markdown を置く

**完了条件**:
- real observation artifact が git に入らない
- sample artifact だけで validator / ingest test を書ける
- `git check-ignore` で work / archived / failed が ignore される
- `pytest -m unit` が通る

### Phase 18.1: raw Markdown artifact schema / validator

**目標**: Perplexity / Codex Computer Use の不安定な出力を、Tomoko に入れる前に raw artifact として検査する。

- [x] raw Markdown の frontmatter contract を定義する
  - `schema_version`
  - `kind = "world_observation_batch"`
  - `generated_by`
  - `observed_at`
  - `language`
  - `topics`
  - `source_policy`
  - `collection_prompt_version`
- [x] `server/shared/models.py` に DTO を追加する
  - `WorldObservationRawDocument`
  - `WorldObservationRawMetadata`
  - `WorldObservationParseIssue`
- [x] `server/world_observations/raw_markdown.py` を追加する
  - frontmatter を読む
  - body を raw text として保持する
  - missing / invalid metadata を issue として返す
  - 内容理解はしない
- [x] validator CLI を追加する
  - `_tools/validate_world_observation_md.py`
  - `--strict` では invalid artifact を non-zero exit にする
- [x] unit test を追加する
  - frontmatter が揺れても issue として返る
  - body は改変されない
  - invalid artifact は ingest されない

**完了条件**:
- Perplexity が多少崩れた Markdown を出しても、Tomoko 本体を壊さず failed に送れる
- raw text と parse issue が trace できる
- `pytest -m unit tests/unit/test_world_observation_raw_markdown.py` が通る

### Phase 18.2: world observation DB schema / store

**目標**: 生 Markdown と Tomoko の解釈を混ぜず、DB 上で再生成可能な派生情報として管理する。

- [x] `docker/postgres/init/013_world_observations.sql` を追加する
- [x] `world_observation_documents` テーブルを追加する
  - raw file path
  - sha256 checksum
  - generated_by
  - observed_at
  - imported_at
  - status: `pending` / `normalizing` / `completed` / `failed`
  - metadata_json
  - parse_issues_json
- [x] `world_observation_items` テーブルを追加する
  - document id
  - topic
  - title
  - summary
  - source_hint
  - freshness
  - confidence
  - item_json
  - raw_excerpt
- [x] `world_observation_interpretations` テーブルを追加する
  - item id
  - persona_state_version_id nullable
  - persona_lexicon_version_id nullable
  - relevance_to_user
  - tomoko_interest
  - emotional_tone
  - memory_value
  - speakability_hint
  - interpretation_text
  - reason_json
  - created_at
- [x] `PostgresWorldObservationStore` / `InMemoryWorldObservationStore` を追加する
- [x] checksum による idempotent import を実装する

**完了条件**:
- 同じ Markdown を二度 ingest しても document / item が重複しない
- raw document と interpretation を SQL で追跡できる
- `pytest -m integration tests/integration/test_phase180_world_observations_db.py` が通る

### Phase 18.3: noisy Markdown normalizer

**目標**: raw Markdown を信頼せず、LLM normalize + schema validation で `world_observation_items` に変換する。

- [x] `server/world_observations/normalizer.py` を追加する
  - raw Markdown body を入力にする
  - structured JSON を出力させる
  - item ごとに confidence / source_hint / freshness / parse_notes を持たせる
- [x] normalizer output DTO を追加する
  - `WorldObservationNormalizedBatch`
  - `WorldObservationNormalizedItem`
  - `WorldObservationNormalizeTrace`
- [x] JSON schema / pydantic validation を追加する
- [x] malformed JSON / timeout / low confidence を failed ではなく traceable error として扱う
- [x] normalize retry は最大 1 回までにする
- [x] low confidence item は DB に保存しても thinker / journalist の source にはしない
- [x] unit test を追加する
  - malformed output は rejected になる
  - required field missing は issue になる
  - low confidence item は candidate source に出ない

**完了条件**:
- 内容理解を rule parser に寄せていない
- LLM normalize の失敗が raw artifact と trace に残る
- `pytest -m unit tests/unit/test_world_observation_normalizer.py` が通る

### Phase 18.4: ingest Makefile job

**目標**: Codex が `informations/work` の Markdown を取り込み、成功なら archived、失敗なら failed へ移せる local job を作る。

- [x] `background-process/ingest_world_observations.py` を追加する
  - `--once`
  - `--dry-run`
  - `--path informations/work`
  - `--archive-root informations/archived`
  - `--failed-root informations/failed`
- [x] `make information-ingest-once` を追加する
- [x] `make information-ingest-dry-run` を追加する
- [x] ingest の流れを固定する
  - work file discovery
  - raw Markdown validation
  - checksum idempotency check
  - normalizer 実行
  - DB transaction で document / item 保存
  - 成功時 archive へ移動
  - 失敗時 failed へ移動し、理由 sidecar を保存
- [x] file movement は DB commit 後に行う
- [x] archive path は `YYYY-MM-DD/<original-file-name>` にする
- [x] failed sidecar は `<file>.error.json` とする
- [x] unit / integration test を追加する

**完了条件**:
- `make information-ingest-dry-run` で DB / file を変更せず plan が見える
- `make information-ingest-once` で sample artifact が archived へ移動する
- validation 失敗 artifact は failed へ移動し、理由が残る
- `pytest -m unit` と該当 integration test が通る

### Phase 18.5: Tomoko persona interpretation worker

**目標**: 同じ外部ニュースでも、Tomoko の人格・好み・ユーザーとの関係性に基づく「見え方」として解釈を保存する。

- [x] `server/world_observations/interpreter.py` を追加する
  - normalized item
  - latest `persona_state_versions`
  - latest `persona_lexicon_versions`
  - recent user interest summary
  - recent initiative feedback summary
  を入力にする
- [x] `WorldObservationInterpretation` DTO を追加する
- [x] interpretation prompt を追加する
  - 事実断定ではなく「Tomoko がどう受け取ったか」を書く
  - user relevance と Tomoko interest を分ける
  - 話題に出すべきかではなく、話題候補にできるかを判断する
- [x] worker を追加する
  - `background-process/interpret_world_observations.py`
  - `make information-interpret-once`
  - `make information-interpret`
- [x] interpretation は versioned snapshot id を保存する
- [x] interpretation failure は raw item を壊さず error として残す

**完了条件**:
- 同じ raw item から「一般要約」と「Tomoko の解釈」が別レコードとして追える
- persona / lexicon version が解釈 trace に残る
- online `/ws` 経路から interpreter が呼ばれていないことを test で保証する

### Phase 18.6: thinker / journalist source 接続

**目標**: 外部観測の解釈を、自発発話候補と日記素材に変換する。ただし raw Markdown を直接 conversation prompt に入れない。

- [x] `server/thinker/sources/world_observation.py` を追加する
  - high confidence interpretation だけを読む
  - `tomoko_interest` / `relevance_to_user` / `freshness` / feedback penalty で seed candidate を作る
  - source は `world_observation:<interpretation_id>` にする
- [x] `JournalistInputBuilder` に world observation source を追加する
  - raw full Markdown ではなく short excerpt / interpretation / reason だけを渡す
- [x] `utterance_candidates.metadata_json` に world observation trace を入れる
  - document id
  - item id
  - interpretation id
  - topic
  - freshness
  - reason
- [x] `CandidateSpeakPolicy` の `curiosity` / `intrusion_risk` と接続する
- [x] 「古いニュースを今さら話す」事故を避けるため、freshness と expires_at を必ず入れる
- [x] unit test を追加する
  - low confidence interpretation は candidate にならない
  - expired observation は candidate にならない
  - candidate metadata から元 document まで辿れる

**完了条件**:
- external observation 由来 candidate が thinker に積まれる
- journalist diary に「Tomoko が今日外界から何を見たか」が入る
- raw Markdown を prompt に直入れしていない
- `pytest -m unit tests/unit/test_phase18_world_observation_source.py` が通る

### Phase 18.7: Perplexity / Codex Computer Use collection recipe

**目標**: 外部情報取得の不安定さを Tomoko 本体から切り離し、半自動の operator workflow として扱う。

- [x] `informations/prompts/daily_world_observation.md` に Perplexity 用 prompt を書く
- [x] `informations/prompts/codex_collection_operator.md` を追加する
  - Codex Computer Use で Perplexity を開く
  - prompt を貼る
  - 1 万字程度の Markdown を得る
  - `informations/work/YYYY-MM-DD-world-observation.md` に保存する
  - `make information-ingest-dry-run`
  - 問題なければ `make information-ingest-once`
- [x] 取得 prompt には「完全な schema compliance は不要。後段 validator が落とす」と明記する
- [x] operator workflow は test 対象にしない
  - Browser / Computer Use / Perplexity UI は壊れる前提
  - 壊れたら prompt / 手順を直す
- [x] secrets / account / private page の内容を artifact に混ぜない注意を書く
- [x] public repo に real collected Markdown を置かないことを明記する

**完了条件**:
- 人間または Codex が手動に近い形で external observation Markdown を作れる
- 取得に失敗しても DB / TomoroSession / `/ws` に影響しない
- ingest dry-run で機械的に受け入れ可否を確認できる

### Phase 18.8: trace / analysis / safety hardening

**目標**: 「なぜ Tomoko がその外部情報を覚え、話題にしたか」を後から追えるようにする。

- [x] `world_observation_trace` view を追加する
  - document
  - item
  - interpretation
  - candidate
  - diary source
  - conversation log
  を辿れる
- [x] `_tools/inspect_world_observation_trace.py` を追加する
  - document path / candidate id / conversation log id から trace を表示する
- [x] feedback と接続する
  - 「それ今じゃない」
  - 「それ面白い」
  - 「その話あとで」
  を world observation topic / source に scoped feedback として残す
- [x] false / outdated / sensitive 情報の扱いを定義する
  - low confidence は話さない
  - source_hint が弱いものは断定口調にしない
  - user private data と混ざった artifact は failed に隔離できる
- [x] perf test を追加する
  - `ContextSnapshotBuilder` に world observation source を足す場合も online budget を破らない
  - reflective / background depth だけで重い search を行う

**完了条件**:
- 「なぜこの話をしたか」を document -> interpretation -> candidate -> conversation で追える
- feedback により次回以降の同 topic candidate が上がる / 下がる
- online conversation latency に影響しない
- `pytest -m unit` と該当 integration / perf test が通る

### 2026-05-25 実装結果

Phase 18 は、外部観測を Tomoko 本体から隔離した raw Markdown artifact として受け、
validated interpretation だけを thinker / journalist へ流す形で実装した。

- `informations/` directory contract、Perplexity 用 prompt、Codex operator recipe、sample artifact を追加した
- `informations/work` / `archived` / `failed` は `.gitignore` に入れ、real artifact が git に入らないようにした
- raw Markdown frontmatter validator と `_tools/validate_world_observation_md.py` を追加した
- `world_observation_documents` / `world_observation_items` / `world_observation_interpretations` と `world_observation_trace` view を追加した
- `PostgresWorldObservationStore` / `InMemoryWorldObservationStore` を追加し、checksum idempotent import を実装した
- LLM normalizer / interpreter は background worker とし、malformed / low confidence / failure を traceable issue として扱うようにした
- `background-process/ingest_world_observations.py` と `background-process/interpret_world_observations.py`、Makefile target を追加した
- `WorldObservationSource` を thinker に接続し、candidate source は `world_observation:<interpretation_id>`、trace は `context_tags` と `utterance_candidates.metadata_json` に保存する
- `JournalistInputBuilder` は raw Markdown ではなく interpretation / short summary / reason だけを日記 prompt に渡す
- world observation の feedback は既存 scoped feedback と同じく `source` / `topic` tag 経由で効く
- `ContextSnapshotBuilder` には world observation source を接続していないため、online 会話 path に外部観測 search は増えていない

補足:
- `normalizer` は pydantic 依存を増やさず、既存方針どおり dataclass DTO + 手動 schema validation にした
- `CandidateSpeakPolicy` には world observation 専用 branch を増やさず、既存の priority / urgency / freshness / scoped feedback 経由で接続した
- Perplexity / Computer Use の実 UI 操作は operator workflow として文書化し、test 対象にはしていない

検証:
- `mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/samples/sample-world-observation.md`
- `make information-ingest-dry-run`
- `git check-ignore informations/work/example.md informations/archived/example.md informations/failed/example.md`
- `mise exec -- uv run ruff check .`
- `mise exec -- uv run pytest -m unit`
- `mise exec -- uv run pytest -m integration`
- `mise exec -- uv run pytest -m perf --tb=short`
- `git diff --check`

### Phase 18 全体の完了条件

- Perplexity / Codex Computer Use が壊れても Tomoko 本体は壊れない
- raw Markdown は filesystem に残り、人間が読める
- Tomoko の解釈は DB に残り、SQL で追える
- raw document と interpretation は混ざっていない
- thinker / journalist は validated interpretation だけを読む
- public repo に real observation artifact が入らない
- `make information-ingest-once` / `make information-interpret-once` / `make thinker-once` / `make journalist-once` の順で、外部情報が候補と日記へ流れる
- `pytest -m unit` が通る

---

## 2026-05-26 追記: Phase 13.5 backend call JSONL trace

上の Phase 13 の `inference_metrics` は backend health の latest sample には有効だが、
実会話で「LM Studio が queue で詰まったのか」「background LLM が会話 backend を塞いだのか」
「TTS / STT / local MLX が同時に GPU を叩きすぎたのか」を request 単位で追うには粒度が足りない。

この不足は、従来の人間向け server log だけを増やすのではなく、機械解析しやすい JSONL trace として補う。

**目標**: LLM / TTS / STT / embedding の依頼開始、queue 待ち、first output、完了、失敗を
`request_id` と共通フィールドで追えるようにし、`jq` / `rg` で会話体験低下の原因を切り分ける。

- [x] `logs/backend-trace.jsonl` に backend call trace を JSONL で追記する
  - 各行に `trace="tomoko_backend_call"` を入れる
  - `ts` は timezone 付き ISO8601 にする
  - `event` は `start` / `queue_acquired` / `response_headers` / `first_delta` / `first_chunk` / `done` / `error` を基本にする
  - `kind` は `llm` / `tts` / `stt` / `embedding`
  - `role` は `conversation` / `session_summary` / `candidate_gen` / `diary` / `stop_intent` / `initiative_judge` /
    `world_observation_normalizer` / `world_observation_interpreter` / `tts` / `stt` / `embedding`
  - `backend` / `model` / `request_id` / `queue_key` / `wait_ms` / `elapsed_ms` / `total_ms` / `chunk_count` /
    `error` を必要に応じて持つ
- [x] LM Studio backend に request lifecycle trace を追加する
  - `start`
  - URL 単位 process-local semaphore の `queue_acquired` と `wait_ms`
  - HTTP headers 受領時の `response_headers`
  - 初回 SSE content の `first_delta`
  - 完了時の `done`
  - 例外時の `error`
- [x] local LLM backend に同じ語彙の trace を追加する
  - `GemmaMLXBackend`
  - `MLXLMBackend`
  - 必要なら `OllamaBackend`
- [x] TTS backend に同じ語彙の trace を追加する
  - `VoicevoxBackend` / `VoicevoxStreamBackend`
  - `KokoroMLXBackend`
  - `SayBackend`
  - first audio chunk と done を区別する
- [x] STT / embedding backend に request 単位 trace を追加する
  - `FasterWhisperSTT` / `MlxWhisperSTT` / `WhisperCoreMLSTT` / `WhisperKitServeSTT`
  - `SentenceTransformerEmbeddingBackend`
  - STT は audio ms / text length、embedding は text length / dimensions を出す
- [x] 呼び出し元 role を trace に渡す
  - online 会話は `conversation`
  - session summarizer は `session_summary`
  - thinker / evaluator / stop-intent / initiative はそれぞれ用途別 role
  - journalist は `diary`
  - world observation は normalizer / interpreter を分ける
- [x] unit test を追加する
  - JSONL が 1 行 1 JSON として parse できる
  - LM Studio が `queue_acquired` / `response_headers` / `first_delta` / `done` を出す
  - 例外時に `error` を出す
  - local TTS/LLM も `tomoko_backend_call` trace を出す

**完了条件**:
- `jq 'select(.trace=="tomoko_backend_call" and .role=="conversation")' logs/backend-trace.jsonl` で会話 LLM trace を抽出できる
- LM Studio の同一 URL に複数 role が投げられた時、Tomoko 側の `wait_ms` が見える
- `first_delta` が遅いのか、TTS `first_chunk` が遅いのか、STT が遅いのかを trace 上で分離できる
- JSONL trace は source of truth ではなく debug artifact として扱い、会話 hot path の制御判断には使わない
- `pytest -m unit` が通る

---

## 2026-05-26 追記: Phase 13.6 WhisperKit turbo 632MB CPU+ANE STT lane

上の WhisperKit serve large 採用は `large-v3-v20240930_626MB` の確認であり、
画像で示された `openai_whisper-large-v3-v20240930_turbo_632MB` 相当の turbo 632MB model を
明示的に active STT として使う構成ではなかった。

また、WhisperKit CLI の `serve` は `cpuAndNeuralEngine` を default にしているが、Tomoko の config からは
compute units を明示できなかったため、実験条件をログや config から読みにくかった。

**目標**: GPU を空けつつ STT を CoreML/ANE 側へ逃がす候補として、
`WhisperKit + large-v3-v20240930_turbo_632MB + cpuAndNeuralEngine` を設定で固定し、
MLX STT へすぐ戻せる比較 lane として扱う。

- [x] `WhisperKitServeSTT` が `--audio-encoder-compute-units` と `--text-decoder-compute-units` を渡せるようにする
- [x] `BackendSpec.compute_units` を STT backend factory から `WhisperKitServeSTT` へ渡す
- [x] `local_whisperkit_serve_large_turbo_632m_cpu_ne` backend を追加する
  - `url = "http://127.0.0.1:50062"`
  - `model = "large-v3-v20240930_turbo_632MB"`
  - `compute_units = "cpuAndNeuralEngine"`
- [x] central realtime の active `stt_backend` をこの backend に切り替える
- [x] config / factory / process 起動引数の unit test を追加する

**完了条件**:
- `whisperkit-cli serve` が turbo 632MB model と `cpuAndNeuralEngine` compute units で起動される
- `pytest -m unit tests/unit/test_stt_backends.py tests/unit/test_phase0_config.py` が通る
- 実ブラウザ比較では `logs/backend-trace.jsonl` の STT `total_ms` と GPU/ANE 使用状況を見る
