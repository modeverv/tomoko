# MaAI の実時間センサー

2026-11-13、作成者 Codex。P3 の部品単体の実装。`ws.py` / `baseline.py` への接続と
`EngineConfig` の設定追加は含まない。実モデルの読み込み・推論は未検証。

## 公開 API

```python
MaaiSensor(
    model_factory, *, frame_samples=1280, user_channel=0,
    max_pending_frames=4, on_frame=None, loop=None,
)
sensor.feed_mic(samples: np.ndarray) -> int
sensor.add_tomoko_audio(samples_24k: np.ndarray, at_mic_s: float) -> None
sensor.stop_tomoko(at_mic_s: float) -> None
sensor.latest() -> MaaiFrame | None
sensor.dropped_frames: int
sensor.error: Exception | None
sensor.close(timeout: float = 5.0) -> None

make_maai_multiple(
    modes=("vap", "bc", "bc_det"), *, lang="jp", frame_rate=12.5,
    context_len_sec=20, device="cpu", torch_threads=1,
) -> MaaiMultiple
```

`model_factory` は引数なしの callable。センサー専用の新しいモデルを返し、
`process(ch1, ch2)`、`result_dict_queue`、`reset_runtime_state()`、`stop(wait=True)` を
備えること。モデルの構築・同期推論・終了は同じ専用スレッドで行う。MaAI 自身の
`start()`、入力キュー、`get_result()` は使わない。Multiple 内部のサブモデル用 executor は使う。

工場関数だけが MaAI と torch を import する。両チャンネルに `MaaiInput.Chunk()` を
渡して stereo を保ち、共有 Mimi (`normal-ver2`) を使う。torch のプロセス全体の演算
スレッド数を `torch_threads`、ONNX の intra を同数、inter を1にする。MaAI のキャッシュに
なければ重みを取得する関数なので、今回のテストでは実関数の import 先を偽物に置換する。
`frame_samples` はモデルの `round(16000 / frame_rate)` と一致させる。

## 入力と時計

- マイクは 16 kHz・float32・mono。任意長で届く音声をフレームに組み直す。
  `feed_mic` は推論や結果を待たず、今回破棄した待機フレーム数を返す。
- Tomoko は 24 kHz・mono。`at_mic_s` は受け取ったマイクのサンプル数に基づく予定再生時刻。
  マイクの1サンプル単位へ丸め、すでに予約した声と重なる指定はその後ろにつなぐ。
- 24 kHz の位置を保持し、マイクのフレームを切り出すときに16 kHzへ線形補間する。
  各 TTS 断片の出力長を独立に丸めないので、短い断片でも時刻の丸め誤差を累積させない。
- その時点で既知の予定音声を入力にする。隣接断片が届いていれば境界も補間し、
  まだ届いていなければ最後のサンプルを右端まで保持する。後から届いた音で、すでに
  推論へ渡した入力を再計算しない。未来の追加音声を知れないため、遅着した場合の境界の
  半サンプルは、全音声を先に渡した場合と異なり得る。予定範囲の外は無音。
- `stop_tomoko` は指定時刻以後の予約音声と、推論待ちフレーム中の Tomoko 音声を消す。
  推論中・推論済みのフレームは変更しない。停止後は新しい声を予約できる。

`user_channel` は**モデル入力**のユーザー側 index（0/1）。既定は ch1=ユーザー、ch2=Tomoko。
1ならモデルへ渡す音声を逆にし、DTO はどちらでもユーザー・Tomoko の順へ戻す。
これは BC の向きを比較する入口であり、BC の System/User の意味を確定した設定ではない。
MaAI 内部の BC 系の swap に追加のモード別 swap は行わない。

## 出力・遅れ・終了

`MaaiFrame` は `engine/dto.py` の frozen / slots DTO。

| フィールド | 値 |
|---|---|
| `at_s` | 投入フレームの終端のマイク時刻。MaAI の壁時計 `t` は使わない |
| `p_now`, `p_future` | `vap` の値を `(ユーザー, Tomoko)` の2要素 tuple にしたもの |
| `p_bc` | `bc.p_bc`。相づち時刻の予測 |
| `p_bc_det_user` | `bc_det.p_bc_det[user_channel]`。現在のユーザーの相づち検出 |
| `vad_user`, `vad_tomoko` | `vap.vad` または `vad.vad` の現在の活動確率 |

ない出力はすべて `None`。欠測を0として扱わない。mono の値を stereo と解釈しない。
Multiple 0.2.27 のトップは `t`, `x1`, `x2`, `vap: {...}`, `bc: {...}`, `bc_det: {...}`。
変換は `maai.py` の `_to_frame` に集約し、`bc.p_bc_detect` を `bc_det` と混同しない。
`p_now/p_future` は話者間の相対的な活動予測であり、発話終了確率ではない。

同期 `process` のあと結果キューから1つ `get_nowait()` する。Mimi の初回など、結果が
ないときは次の入力へ進む。`latest` は初回出力まで `None`、以後は最後の予測を返す。
遅れやリセット中には古い予測が残るため、利用側は `at_s` で鮮度を判断する。

待機フレームが `max_pending_frames` を超えると古いものから破棄する。
`feed_mic` の返り値・累計 `dropped_frames`・warning ログで観測できる。入力の時刻が
飛んだらモデルの encoder / KV cache をリセットし、欠けた前後を連続した会話とみなさない。
リセット後も Mimi の初回出力が出ない場合がある。

`on_frame` は同期 callable。新しい予測を `loop.call_soon_threadsafe` で asyncio の
ループへ渡す。loop を省略したときは、構築時の実行中ループを使う。終了後の未実行通知は捨てる。
`close` は待機フレームを破棄しスレッド終了を待つ。実行中の同期処理を強制停止はしない。
期限を超えた場合は `TimeoutError`。処理が戻ってから `close` を再度呼べる。
構築・推論・終了の最初の例外は `error` に残し、後続の終了例外もログに残す。

## 検証範囲

合成波形と偽モデルの単体テストのみ。MaaiMultiple の辞書・起動・終了の形は指定された
0.2.27 のコードの静的確認に基づく。Mimi / BC / BC-Det の重み取得・推論、CPU 使用率、
実時間達成、相づちの向きや精度、会話の遅れは確認していない。
