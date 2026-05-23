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

- Python 3.11+
- PostgreSQL 16+（pgvector + PGroonga）
- Ollama または mlx-lm（Apple Silicon 推奨）
- irodori-tts（別途起動）

### 手順

```bash
uv sync
make db-up
ollama pull qwen2.5:7b
# Apple Silicon の場合は MLX が 2〜3 倍速い
# pip install mlx-lm
make server
```

ブラウザで `http://localhost:8000` を開く。

開発中にコード変更を自動反映したい場合は `make server-reload` を使う。
サーバーログは `make server` / `make server-reload` を実行しているターミナルに出る。
ファイルにも `logs/server.log` として出力される。

詳細なデバッグログをファイルに残したい場合は次を使う。

```bash
make server-debug
```

`make server-debug` は DEBUG レベルで起動し、stdout/stderr も `logs/server-debug.log` に追記する。

## 開発状況

実装はマイルストーンに沿って段階的に進める。詳細は `PLAN.md` を参照。

| マイルストーン | 内容 | 状態 |
|---|---|---|
| M1 | 話せるTomoko | 🚧 実装中 |
| M2 | 記憶があるTomoko | 未着手 |
| M3 | 自分から話すTomoko | 未着手 |
| M4 | インフラが安定したTomoko | 未着手 |
| M5 | 家族のTomoko | 未着手 |

## reference/ について

過去の実装経験から得た参考コードを置いている。
今回の設計が「何を解決しようとしているか」を理解するための資料。

- `reference/unity/MyAIRoomScript.cs` — 音量閾値VAD・OGGエンコード・REST一括APIの旧実装
- `reference/server/api.py` — OGG→MP3変換・Base64・REST一括返却の旧サーバー実装

今回はこれらの「逆」を実装する。

## ライセンス

MIT License — 著作権表示を残せば、商用利用を含め自由に使用・改変・再配布できます。

Copyright (c) 2026 modeverv

## Made with llm

this project is made with llm and worked through [llm orchestrator](https://github.com/modeverv/llm-orchestrator)
