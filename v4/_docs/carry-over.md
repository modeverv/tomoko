# v1・v2 から持ち込む候補

作成: 2026-11-10 / Claude Code（Fable 5.2）。調査はサブエージェントが行い、作成者は個々の行番号を読み直していない。
持ち込むときは、ここに書いた理由とテストを一緒に読む。まだ何も持ち込んでいない。

| # | 部品 | 場所 | テスト | そう書かれた理由 | 使う段階 |
|---|---|---|---|---|---|
| 1 | MaAI の 160 サンプルへの組み直し、`bc_2type` と `vap` の並走、結果キューの timeout 付き読み出し | `v2/server/hot_path/backchannel.py`（旧版は `v1/server/gateway/maai_backchannel.py`） | `v2/tests/unit/test_v2_hot_path_backchannel.py` | ブラウザは 128 サンプルで届くが MaAI は 160 を期待する。そのまま渡すと結果が出ず、モデルの問題に見えた。blocking の読み出しは終了時にスレッドが残った。`bc_2type` は相づちの確率しか返さないので `vap` を足し、`p_future[1]` を譲る見込みとした | P3 |
| 2 | VAD の先読み（500 ms を保持）と無音 400 ms | `v2/server/audio/vad.py`。Silero の包みは `v1/server/edge/pipeline/vad.py` | `v1/tests/unit/test_vad.py` | Silero は検出が遅く、発話の頭が欠けた。512 サンプルの刻みでは無音の閾値が 32 ms 単位に丸まる | P2 |
| 3 | Apple Speech の常駐プロセス | `v2/scripts/apple_speech_stt/AppleSpeechSTT.swift`、Python 側は `v2/server/audio/stt.py` | `v2/tests/unit/test_v2_audio_tomoko_prompt.py` | 起動直後の最初の発話で途中経過が出なかったので、無音で温める。`swiftc` と `codesign` で組み立てる | P2 |
| 4 | MLX Whisper の包みと、一定間隔の再認識 | `v1/server/edge/pipeline/stt.py` | `v1/tests/unit/test_stt_backends.py` | 設定の理由は記録に見つからなかった | P2 |
| 5 | Kokoro の日本語の設定（`JAG2P(version="pyopenjtalk")`） | `v1/server/shared/inference/tts/kokoro_mlx.py` | `v1/tests/unit/test_kokoro_mlx_tts.py` | `unidic` が辞書なしで入り、文字は出るのに音が出なかった | P2 |
| 6 | VOICEVOX のストリーミングと、取り消し | `v2/server/hot_path/model_executor.py`、取り消しは `v1/server/shared/inference/tts/voicevox.py` | `v1/tests/unit/test_voicevox_tts.py` | PR1823 は 1 本の `audio/wav` を流す。v2 には取り消しがなく、古い断片を捨てるだけだった | P2 |
| 7 | LLM のストリーミングと、Gemma の thinking の止め方 | `v2/server/llm/chat.py` | `v1/tests/unit/test_lm_studio_backend.py` | `think=false` は受け付けられるが無視された。`chat_template_kwargs={"enable_thinking": false}` だけが効いた | P2 |
| 8 | 世代の番号で古い音を捨てる | `v2/server/hot_path/speech_executor.py` | `v2/tests/unit/test_v2_generation_owner.py` | 停止が ACK を返すだけで、予約済みの音が鳴り続けた | P3 |
| 9 | ブラウザのマイクと再生（AudioWorklet、ゲイン 0 のノード） | `v2/client/audio-worklet.js`、`v2/client/main.js` | なし | ゲイン 0 のノードがないと、ブラウザが worklet を呼ばなくなることがある | P2 |

## v1・v2 にないもの

- **実際に聞こえた範囲の記録**: どちらにもない。`_docs/knowlaedge.md` でも未解決とされている。v4 では、クライアントの再生の報告から作る（ARCHITECTURE 4.7）。
- **サーバーからの音声の形**: v2 は断片ごとに完結した WAV を送っていた。v4 は、世代と通し番号を頭に付けた生の PCM にする（ARCHITECTURE 4.9）。
