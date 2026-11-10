# V0-2 RTX 3060 に Gemma と STT を置く — 所見

作成: 2026-11-10 / Claude Code（Opus 5.5）
スクリプト: `scripts/v0/gemma_remote_probe.py`、`scripts/v0/stt_remote_probe.py`
生データ: `_docs/v0/data/gemma-remote-*.json`、`_docs/v0/data/stt-remote-*.json`

## 結論

- **3060 の Gemma から推論なしの返事を、逐次ストリームで得られた**（llama.cpp の llama-server、`--reasoning-budget 0`）。
  短い日本語の雑談 20 回で 20/20 が推論を含まない返事（完了条件を満たす）。最初のトークンまで p50 55 ms / p95 115 ms。
- **3060 に STT を同居させられる。** whisper.cpp（large-v3-turbo q5_0）で 1.5〜4 秒の発話の書き起こしが p50 186 ms。
  ただし同じ GPU で Gemma が生成している最中は p50 484 ms に伸び、Gemma 側の速さも約半分になる。
- LM Studio のままでは、推論なし（自前テンプレートの `/v1/completions`）と逐次ストリームを両立できなかった。

## 3060 側に入れたもの（ユーザー許可済み、2026-11-10）

| もの | 場所 | 起動 |
|---|---|---|
| llama.cpp b12233（Windows CUDA 13.5 x64 + cudart） | `C:\Users\modev\v3tools\llama.cpp\` | タスク `v3-llama-e4b`（`run-llama-e4b.cmd`）。127.0.0.1:8090 |
| whisper.cpp b5454（Windows CUDA 12.4 x64）+ `ggml-large-v3-turbo-q5_0.bin` | `C:\Users\modev\v3tools\whisper.cpp\` | タスク `v3-whisper`（`run-whisper.cmd`）。127.0.0.1:8091 |

- モデルは LM Studio が持っている `gemma-4-E4B-it-Q4_K_M.gguf` をそのまま読んだ（複製していない）。
- SSH のセッションから起動した子プロセスは SSH を切ると止まったので、タスクスケジューラ（ONCE、手動実行）で起動した。ログオン中だけ動く。
- 両サーバーとも 127.0.0.1 で待ち受け、Mac からは SSH のポート転送（18090 / 18091）で使った。Windows のファイアウォール設定は変えていない。
- VRAM を空けるため **LM Studio の E4B・E2B を `lms unload --all` で外した**。戻すときは LM Studio の画面か `lms load google/gemma-4-e4b`。

## 測定

### Gemma（短い雑談 5 種 × 4 巡、max_tokens 60、warm-up 1 回は集計外）

| サーバー | 経路 | 推論なしの返事 | 最初のトークンまで p50 / p95 / 最大 | 終了まで p50 / p95 | 逐次か |
|---|---|---|---|---|---|
| LM Studio `/v1/completions`（会話形式を自前で組む） | LAN 直 | 20/20 | 1,165 / 1,292 / 1,294 ms | 1,167 / 1,296 ms | **否**（全チャンクが最後に届く） |
| llama-server `/v1/chat/completions`、`--reasoning-budget 0` | SSH トンネル | 20/20 | **55 / 115 / 119 ms** | 227 / 796 ms | 可（毎秒 約 74 チャンク） |

起点 → 終点: HTTP リクエスト送信直前 → 最初の非空テキストチャンク受信（Mac 側の時計）。
LAN の往復: ping 平均 0.6 ms（前セッション）。TCP 接続の確立は LAN 直で p50 0.5 ms。トンネル経由の値はローカル端点までなので LAN の値ではない。
LM Studio の値が大きいのは、生成全体を待ってから返しているため（LM Studio 側の速さではない）。

### STT（work/audio の合成音声 10 文 × 3 巡、16 kHz に変換して POST）

| 条件 | p50 | p95 | 最大 | 備考 |
|---|---|---|---|---|
| 単独 | 186 ms | 215 ms | 314 ms | 10 文とも句読点・表記ゆれ（晩御飯）以外は正しい |
| 同じ 3060 で llama-server が生成中 | 484 ms | 599 ms | 608 ms | 生成側は毎秒 74 → 36〜51 トークン |

起点 → 終点: 発話 1 個分の WAV を POST する直前 → 書き起こし受信。VAD の待ちは含まない。合成音声であり、実マイクの精度ではない。

## 残ること

- 3060 側のサーバーは手動起動。常用するなら Windows 起動時の自動起動を決める（ユーザーの判断）。
- 実マイク音声での STT 精度。
- llama-server の同時要求（Gemma への要求と STT が重なる頻度）を実会話で見る。
