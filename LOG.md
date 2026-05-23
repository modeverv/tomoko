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
- `docs/latency.md` に Phase 6b の perf 再測定を追記した

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
- `docs/latency.md` に Phase 6.5 の perf 再測定結果を追記した

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
- `docs/latency.md` と `MEMORY.md` に AEC だけでは不十分だった判断と Phase 6.6.0 初期実装を追記した

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
- `docs/latency.md` に Phase 6.6.1 の検証結果を追記した

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
