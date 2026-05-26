# Tomoko

ローカル推論で動く音声対話型の人格シミュレーション。

## これは何か

Tomoko（トモコ）は、ブラウザのマイクに向かって話すと声で返事をしてくれる対話AIです。
ただし普通のチャットボットとは違います。

- **記憶を持つ** — 会話を PostgreSQL に蓄積し、関連する話題が来たら思い出として引き出す
- **人格を持つ** — 基本性格プロンプトに加えて、会話を重ねるごとに少しずつ変化する
- **感情を表現する** — 静止画の切り替えと声のトーン変化で今の気分を伝える
- **自分から話しかけてくる** — 沈黙を破って自発的に話し始める
- **日記を書く** — その日あったことを振り返り、言えなかったことも書き留める
- **完全にローカルで動く** — LLM・STT・TTS すべて手元のマシンで推論する

## 思想

- 一人用。商用ではない。レイテンシーと体験の質に全振りする
- **「内面の時間が流れている存在」としての Tomoko を作る**
- 話しかけなくてもそこにいる感
- 音声データはエッジの外に出ない。家族の会話が外に流れない
- クライアント（ブラウザ）はただの入出力装置。状態はサーバーが全部持つ
- シンプルに保つ: ノード分散 + PostgreSQL、pub/sub なし

## なぜ OSS か

企業製品では実現できない設計思想がここにあります。

```
ambient_logs に全発話をローカルで保持する
dismissed_at（言えなかったこと）から日記を書く
arrival_candidates で入室前から一言を事前計算する
utterance_candidates という内面のプールが常に流れている
```

セットアップが複雑すぎて商品にはなりません。
また、プライバシー問題があるため、企業が提供するサービスとしても成立が困難です。
でも**個人エンジニアが自分のために作るからこそできる設計**です。
その設計思想をコードと ARCHITECTURE.md に残すことに意味があると考えています。

## 構成

```
エッジ（各部屋）
  VAD + STT + TTS（+ 軽量LLM）
  音声データはここから外に出ない

中央リアルタイムノード
  TomoroSession（状態機械）
  InferenceRouter（LLM選択・フォールバック）
  DirectSpeakerResolver（回り込み除去）

中央バックグラウンドノード
  thinker（候補生成、常駐）
  journalist（日記、定期）

PostgreSQL（全ノードが共有する唯一の真実）
  conversation_logs / ambient_logs
  utterance_candidates / arrival_candidates
  diary / persona_state
```

詳細は `ARCHITECTURE.md` を参照。

## セットアップ

### 必要なもの

- mise
- Docker / Docker Compose（PostgreSQL 用）
- Apple Silicon Mac 推奨（MLX 系 STT / TTS / fallback LLM を使うため）
- LM Studio（現行 default の会話 LLM 用）

`mise.toml` で Python 3.11 と uv を管理しているため、Python / uv は `mise` 経由で揃える。
PostgreSQL は `make db-up` で Docker 上に起動する。

現行の default 設定は次の構成：

- 会話 LLM: LM Studio OpenAI 互換 API（`gemma-4-26b-a4b-it-mlx`）
- 会話 LLM fallback: MLX VLM（`mlx-community/gemma-4-e2b-it-4bit`）
- STT: MLX Whisper small
- TTS: 起動済み VOICEVOX Engine（春日部つむぎ）
- embedding: BGE-M3

初回起動時は Whisper / Supertonic / Gemma / embedding モデルのダウンロードや warm-up に時間がかかる。
LM Studio を使わずに動かす場合は `config/central_realtime.toml` の
`conversation_backend` を `local_gemma4_e2b_mlx` などに変更する。

### 手順

```bash
make deps
make db-up
make download-models
make server
```

ブラウザで `http://localhost:8000` を開く。

`make download-models` は MIT / Apache-2.0 などの permissive license のモデルだけを事前取得する。
LFM や Supertonic のような custom / OpenRAIL 系モデルは、ライセンスを確認したうえで明示的に取得する。

```bash
make download-optional-models
```

現在の `conversation_backend` は LM Studio の Gemma 4 E4B、`tts_backend` は `voicevox_tsumugi` なので、
VOICEVOX アプリを起動し、Engine が `http://127.0.0.1:50021` で応答する状態にしておく。
`voicevox_tsumugi` は通常の `/audio_query` / `/synthesis` を使う。
`voicevox_tsumugi_stream` は比較用に残しているが、`/cancellable_synthesis` は first binary 到着を速めなかったため、
普段の体感確認では通常 backend を使う。
VOICEVOX を使わず custom license を避けたい場合は、`config/central_realtime.toml` の
`conversation_backend` を `local_gemma4_e2b_mlx` に、`tts_backend` を `kokoro_mlx` に変更する。

LM Studio を使う場合は、`config/central_realtime.toml` の
`[backends.lmstudio_gemma4_26b_a4b]` に書かれた URL で LM Studio の OpenAI 互換 API を起動し、
`gemma-4-26b-a4b-it-mlx` をロードしておく。
E4B は `lmstudio_gemma4_e4b` として残しているため、速度優先へ戻す場合は
`conversation_backend` を戻すだけで比較できる。

開発中にコード変更を自動反映したい場合は `make server-reload` を使う。
サーバーログは `make server` / `make server-reload` を実行しているターミナルに出る。
ファイルにも `logs/server.log` として出力される。

詳細なデバッグログをファイルに残したい場合は次を使う。

```bash
make server-debug
```

`make server-debug` は DEBUG レベルで起動し、stdout/stderr も `logs/server-debug.log` に追記する。

### STT 負荷ベンチ

Ctrl-C で止めるまで STT を生成し続ける負荷ベンチは次で実行する。

```bash
make soak-stt
```

結果は `logs/stt-soak.jsonl` に sample / error / summary として追記される。
TTS や会話推論を横で走らせた状態の STT レイテンシーを見る場合は、次のように load backend を指定する。

```bash
mise exec -- uv run python _tools/soak_stt_backends.py \
  --backends local_whisper_mlx_small,local_whisperkit_serve_small \
  --load-tts-backend supertonic_coreml_f1 \
  --load-conversation-backend local_lfm25_12b_jp_mlx
```

MLX STT lane と CoreML STT lane を、Supertonic CoreML TTS + LFM MLX 会話推論の同じ横負荷で比べる場合は次を使う。

```bash
make soak-voice-stack
```

このベンチも Ctrl-C で止めるまで継続し、`logs/voice-stack-soak.jsonl` に結果を追記する。
default は次の2シナリオを交互に測る。

- `local_whisper_mlx_small` + `supertonic_coreml_f1` + `local_lfm25_12b_jp_mlx`
- `local_whisperkit_serve_small` + `supertonic_coreml_f1` + `local_lfm25_12b_jp_mlx`

default の横負荷は、各 STT 測定ごとに Supertonic TTS 2 回、LFM 会話推論 6 回を連続実行する。
さらに詰める場合は repeats / workers を増やす。

```bash
mise exec -- uv run python _tools/soak_voice_stack_scenarios.py \
  --load-conversation-repeats 12 \
  --load-tts-repeats 4
```

## 開発状況

実装はマイルストーンに沿って段階的に進める。詳細は `PLAN.md` を参照。

| マイルストーン | 内容 | 状態 |
|---|---|---|
| M1 | 話せるTomoko | 🚧 実装中 |
| M2 | 記憶があるTomoko | 未着手 |
| M3 | 自分から話すTomoko | 未着手 |
| M4 | インフラが安定したTomoko | 未着手 |
| M5 | 家族のTomoko | 未着手 |

## _reference/ について

過去の実装経験から得た参考コードを置いている。
今回の設計が「何を解決しようとしているか」を理解するための資料。

- `_reference/unity/MyAIRoomScript.cs` — 音量閾値VAD・OGGエンコード・REST一括APIの旧実装
- `_reference/server/api.py` — OGG→MP3変換・Base64・REST一括返却の旧サーバー実装

今回はこれらの「逆」を実装する。

## ライセンス

MIT License — 著作権表示を残せば、商用利用を含め自由に使用・改変・再配布できます。

### モデルと依存ライブラリ

Tomoko 本体のコードは MIT License だが、利用するモデル重みと外部ライブラリはそれぞれ別のライセンスに従う。
モデル重みは repository に同梱せず、Hugging Face cache へユーザー操作で取得する。

- Whisper / WhisperKit / faster-whisper: MIT
- Kokoro-82M: Apache-2.0
- BGE-M3: MIT
- Irodori-TTS v3: MIT
- Qwen / Gemma 系の現在の設定対象: Apache-2.0
- LFM2.5: `lfm1.0` custom model license
- Supertonic-3 CoreML: OpenRAIL-family license
- VOICEVOX Engine: LGPL v3 / 別ライセンス。Tomoko は起動済み Engine へ HTTP で接続するだけで、
  VOICEVOX 本体・音声ライブラリ・生成音声はそれぞれの利用規約に従う
- psycopg: LGPL-3.0-only dependency

LGPL の psycopg は通常の Python dependency として import して使う範囲では Tomoko 本体の MIT License を
LGPL に変えるものではない。ただし psycopg 自体を改変して再配布する場合や、配布物に wheel / binary を同梱する場合は、
LGPL のライセンス文と該当 component の入手・差し替え可能性を保つ必要がある。

Copyright (c) 2026 modeverv

## Made with llm

this project is made with llm and worked through [llm orchestrator](https://github.com/modeverv/llm-orchestrator)
