# LOG.md

実装セッションの時系列ログ。セッションをまたいだ引き継ぎのために書く。

---

## テンプレート

```
## YYYY-MM-DD セッションN

### やったこと
-

### 詰まったこと・解決したこと
-

### 次のセッションでやること
-
```

---

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
- `docs/latency.md` にローカル `/ws` echo round trip の実測値を追記した

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
- `docs/latency.md` に 300 / 400 / 500ms の無音閾値検出タイミングを追記した

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
- Phase 3 の参加判断・DB 書き込みレイテンシーを `docs/latency.md` に追記した

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
- Ollama `qwen2.5:7b` の初回 text delta レイテンシーを測定し、`docs/latency.md` に記録した

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
- Phase 5 の unit test と perf test を追加し、`docs/latency.md` に実測値を追記した

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
- Phase 6a の unit test を追加し、`docs/latency.md` に perf 再測定を追記した

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
