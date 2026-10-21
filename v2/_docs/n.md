# 音響 / STT 前処理 signal path 棚卸し

作成日: 2026-06-12

目的:
- マイク入力から STT / transcript までの signal path を時系列に並べる。
- 各処理を `hot path` / `退役気味` / `実験用` に分ける。
- 削除や隔離の前に、バックアップ対象と runtime 影響を明確にする。

結論:
- 現在の production factory は `SttAudioFrontend(enabled_filters=())` を作っており、STT 前処理 filter chain は既定では無効。
- `SttAudioFrontend` には `signal_gate` / `speech_bandpass` / `short_segment_merge` / `spectral_subtraction` / `rnnoise` が同居しているが、実ランタイム既定では音響加工も reject gate も通らない。
- MEMORY には一時期 `speech_bandpass` / `signal_gate` を常時ONにする判断が残っているが、現行コードはそれを否定する方向へ drift している。
- 削るなら、まず production module から実験 filter を外し、`signal_gate` だけを残すか、完全に `TranscriptFilter` / turn-taking 側へ寄せるかを決める必要がある。

## 現行 signal path

### Central `/ws` path

| 時系列 | hot path | 退役気味 / 現在無効 | 実験用 / 隔離候補 | 主なコード |
|---:|---|---|---|---|
| 1 | Browser が float32 chunk を `/ws` へ送る | Chrome `echoCancellation` / `noiseSuppression` は過去に有効化されたが、サーバー側の authoritative 判定ではない | なし | `client/main.js` |
| 2 | `TomoroSession.process_audio_chunk()` が chunk を受ける | なし | なし | `server/session.py` |
| 3 | `VADProcessor.process_chunk()` が speech/listening/segment を決める | なし | なし | `server/edge/pipeline/vad.py` |
| 4 | idle 中 pre-roll を保持し、`idle -> listening` 時に segment 先頭へ連結する | なし | なし | `server/edge/pipeline/vad.py` |
| 5 | listening 中なら partial STT へ進む可能性がある | `SttAudioFrontend.should_process_partial_chunk()` は、`signal_gate` 無効なら常に `True` | partial chunk gate は現状 factory 既定では無効 | `server/session.py`, `server/edge/pipeline/stt_gate.py` |
| 6 | speech_end 後、`SttAudioFrontend.process_segment()` が呼ばれる | `enabled_filters=()` なので segment は加工も reject もされず accept | filter chain 実装はここに残っている | `server/session.py`, `server/edge/main.py`, `server/edge/pipeline/stt_gate.py` |
| 7 | `transcriber.transcribe(frontend_decision.segment)` を呼ぶ | なし | なし | `server/session.py` |
| 8 | `TranscriptFilter` が hallucination / short low audio などを落とす | audio frontend の代替として実質こちらが残っている | なし | `server/edge/pipeline/stt_filter.py`, `server/session.py` |
| 9 | participation / turn-taking / reply へ進む | なし | なし | `server/session.py`, `server/gateway/turn_taking/*` |

### Edge remote path

| 時系列 | hot path | 退役気味 / 現在無効 | 実験用 / 隔離候補 | 主なコード |
|---:|---|---|---|---|
| 1 | Edge browser が chunk を central ではなく edge session へ送る | なし | なし | `server/edge/main.py` |
| 2 | `EdgeRemoteAudioSession.process_audio_chunk()` が chunk を受ける | なし | なし | `server/edge/remote.py` |
| 3 | `VADProcessor.process_chunk()` が segment を作る | なし | なし | `server/edge/pipeline/vad.py` |
| 4 | partial STT へ進む前に `should_process_partial_chunk()` を見る | factory 既定 `enabled_filters=()` なら常に `True` | partial signal gate は現状無効 | `server/edge/remote.py`, `server/edge/pipeline/stt_gate.py` |
| 5 | segment 完了後 `SttAudioFrontend.process_segment()` を呼ぶ | factory 既定 `enabled_filters=()` なら accept | filter chain 実装は残存 | `server/edge/remote.py`, `server/edge/main.py` |
| 6 | `transcriber.transcribe()` を呼ぶ | なし | なし | `server/edge/remote.py` |
| 7 | `TranscriptFilter` を通し、accept なら gateway event にする | なし | なし | `server/edge/remote.py` |

## `SttAudioFrontend` 内の処理順

`SttAudioFrontend.process_segment()` の内部順序:

| 順序 | 処理 | 現在の既定状態 | 分類 | コメント |
|---:|---|---|---|---|
| 1 | `short_segment_merge` | OFF | 退役気味 | 短い segment を pending にして次 segment と merge する。現行 factory では使われない。発話境界を変えるので hot path 復帰は慎重にする。 |
| 2 | `speech_bandpass` | OFF | 退役気味 | 100Hz high-pass / 7.2kHz low-pass。過去判断では常時ONだったが、現行 factory は OFF。復帰するなら実録音比較が必要。 |
| 3 | `rnnoise` | OFF | 実験用 | ffmpeg `arnndn` 経由。model file がなければ素通り。常時ONは過去に否定済み。production module から外す候補。 |
| 4 | `spectral_subtraction` | OFF | 実験用 | noise profile がなければ素通り。過去実験では hallucination を悪化させた。production module から外す候補。 |
| 5 | `signal_gate` | OFF | 判断待ち | 低信号 reject。過去判断では有効だったが、現行 factory は OFF。残すなら production path の最小候補。 |
| 6 | metrics logging | ON | hot path | filter 無効でも `audio_signal_metrics()` は process_segment 内で計算され、latency log に出る。 |

## 残存コードの分類

### Hot path として残す

| 対象 | 理由 | ファイル |
|---|---|---|
| `VADProcessor` | 発話区間確定の中核。pre-roll も wake word 冒頭欠落対策として現役。 | `server/edge/pipeline/vad.py` |
| `SttAudioFrontendDecision` | `TomoroSession` / edge remote が既にこの DTO を前提にログと accept/reject 分岐を書いている。 | `server/edge/pipeline/stt_gate.py` |
| `audio_signal_metrics()` | frontend 無効時も STT 投入前の観測値として使われる。turn-taking metrics にも入る。 | `server/edge/pipeline/stt_gate.py` |
| `TranscriptFilter` | STT 後段の hallucination / short text filter として現役。 | `server/edge/pipeline/stt_filter.py` |

### 残すか決める必要がある

| 対象 | 現状 | 判断 |
|---|---|---|
| `SttSignalGate` | unit test あり。runtime factory 既定では OFF。 | production に戻すなら `enabled_filters=("signal_gate",)` を明示し、実機で `今何時` / 低音量短文 / wake word を再確認する。戻さないなら `_tools` 側へ退避候補。 |
| `speech_bandpass()` | unit test あり。runtime factory 既定では OFF。 | DAW 的には自然だが、Whisper 入力を加工する。採用判断が揺れているので、今は production hot path から外れている事実を優先する。 |
| `short_segment_merge` | unit test あり。runtime factory 既定では OFF。 | 発話境界を変えるため、turn-taking / stale cancel / follow-up に影響しやすい。現時点では退役扱い。 |

### 実験用へ隔離する候補

| 対象 | 理由 | 推奨移動先 |
|---|---|---|
| `NoiseProfile` | spectral subtraction 専用。runtime 既定で使われていない。 | `_tools/audio_experiments/` または `experiments/audio_frontend/` |
| `build_noise_profile()` | 同上。 | `_tools/audio_experiments/` |
| `spectral_subtract()` | 過去実験で低信号 hallucination を改善せず、むしろ悪化。 | `_tools/audio_experiments/` |
| `rnnoise_denoise()` | ffmpeg subprocess を production import path に持ち込む。常時ONは否定済み。 | `_tools/bench_rnnoise_filter.py` 側へ内包 |
| `_read_wav()` / `_write_temp_wav()` | RNNoise helper 専用。 | `_tools/bench_rnnoise_filter.py` 側へ内包 |
| `_frame_audio()` | spectral subtraction helper。 | `_tools/audio_experiments/` |

## 削除 / 隔離案

### Phase A: source を消さない整理

1. `n.md` の内容を元に `PLAN.md` に cleanup Phase を追加する。
2. `SttAudioFrontend` の docstring に「現行 runtime factory は filters OFF」と明記する。
3. `tests/unit/test_stt_signal_gate.py` を production と experiment に分ける。
4. `_tools/bench_audio_filters.py` / `_tools/bench_rnnoise_filter.py` の README 的な注記を追加する。

この Phase は runtime behavior を変えない。

### Phase B: backup を残して隔離

推奨 backup:
- `archive/audio_frontend_experiments/2026-06-12/`

移動候補:
- `spectral_subtract`
- `NoiseProfile`
- `build_noise_profile`
- `rnnoise_denoise`
- RNNoise 用 WAV helper
- spectral / RNNoise unit tests

backup に残すべきもの:
- 元の `server/edge/pipeline/stt_gate.py`
- `tests/unit/test_stt_signal_gate.py`
- `_tools/bench_audio_filters.py`
- `_tools/bench_rnnoise_filter.py`
- 関連 MEMORY 抜粋

### Phase C: production module を薄くする

目標形:
- `server/edge/pipeline/stt_gate.py`
  - `AudioSignalMetrics`
  - `SttGateDecision`
  - `SttAudioFrontendDecision`
  - `SttAudioFrontend`
  - `SttSignalGate`
  - `audio_signal_metrics`
- 実験 filter は import されない。
- `SttAudioFrontend.process_segment()` は、当面 `signal_gate` だけ扱うか、完全 pass-through + metrics にする。

判断待ち:
- `signal_gate` を runtime default に戻すか。
- `speech_bandpass` を削除するか、bench 用だけにするか。
- `short_segment_merge` を完全撤去するか、turn-taking 実験として残すか。

## 削除前の確認項目

- `enabled_filters=()` が central / edge の唯一の production factory であることを `rg` で再確認する。
- 実機ログで `filters=none` が出ていることを確認する。
- `pytest -m unit` が通ること。
- source backup を `archive/audio_frontend_experiments/2026-06-12/` に残すこと。
- `MEMORY.md` に「音響加工 filter は production hot path から退役」と追記すること。

## 現時点での推奨

今すぐソースから消してよい可能性が高い順:

1. `rnnoise_denoise()` と関連 helper を production module から外す。
2. `spectral_subtract()` / `NoiseProfile` / `build_noise_profile()` を production module から外す。
3. `short_segment_merge` を production module から外す。
4. `speech_bandpass()` は最後に判断する。
5. `SttSignalGate` はまだ消さない。復帰候補として最小の production gate になり得る。

この順なら、音声会話 hot path の挙動を変えずに、production に見える実験コードだけを減らせる。
