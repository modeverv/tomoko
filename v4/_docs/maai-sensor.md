# MaAI の実時間センサー

2026-11-13、作成者 Codex。P3 の実時間センサーと、CPC vap を併用する接続の実装。
実モデルの読み込み・推論は今回の単体テストでは行わない。

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

make_maai_single(
    mode="vap", *, lang="jp", frame_rate=10, context_len_sec=5,
    model_type="normal", device="cpu", torch_threads=1,
) -> Maai

CompositeMaai(primary: MaaiSensor, vap: MaaiSensor)
# feed_mic / add_tomoko_audio / stop_tomoko / latest / dropped_frames / error / close
# は MaaiSensor と同じ形。
```

`model_factory` は引数なしの callable。センサー専用のモデル、または `SharedMaai.lease` を返し、
`process(ch1, ch2)`、`result_dict_queue`、`reset_runtime_state()`、`stop(wait=True)` を
備えること。工場の呼び出し・同期推論・終了は同じ専用スレッドで行う。共有する実モデルは
起動時に構築し、接続ごとに貸し出して文脈をリセットする。MaAI 自身の
`start()`、入力キュー、`get_result()` は使わない。Multiple 内部のサブモデル用 executor は使う。

工場関数だけが MaAI と torch を import する。両チャンネルに `MaaiInput.Chunk()` を
渡して stereo を保つ。`make_maai_multiple` は共有 Mimi (`normal-ver2`) を使い、
torch のプロセス全体の演算スレッド数を `torch_threads`、ONNX の intra を同数、inter を1にする。
`make_maai_single` は既定で CPC (`normal`) を使い、torch のスレッド数を設定する。MaAI のキャッシュに
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
単体 Maai は mode のネストを持たず、`p_now` / `p_future` / `vad` などをトップへ返す。
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

## CPC vap と Mimi の併用

`EngineConfig.maai_vap_cpc=False` が既定。`maai=True` と併せて True にしたときだけ、
`Models.start` は `maai_modes` から `vap` を除いて Mimi を作り、CPC vap を別の
`SharedMaai` として起動する。両工場には同じ `maai_threads` を渡す。
環境変数は `TOMOKO_MAAI_VAP_CPC`。False なら従来の modes とユーザーチャンネルを使う。

接続ごとに Mimi は1280サンプル、CPC は1600サンプルの `MaaiSensor` を作る。
CPC 有効時は両方とも ch1=ユーザー（`user_channel=0`）、通知先は両方 `session.on_maai`。
`CompositeMaai` を `session.attach_maai` に渡すので、`baseline.py` の判断処理は変わらない。
`maai_modes="vap"` の場合は空の Mimi を作らず、CPC のセンサーだけを直接つなぐ。

`CompositeMaai.latest()` は主の `p_bc` / `p_bc_det_user`、副の `p_now` / `p_future` /
`vad_user` / `vad_tomoko` を結合し、鮮度判定用の `at_s` は古い方を使う。
片方のフレームがまだなければある側だけを返し、両方なければ None。
両方が出力済みでも、個々の欠測値を別モデルの値で埋めない。

入力・Tomoko 音声の予約・停止を両方へ渡し、破棄数の返り値と累計は合計する。
`error` は主の失敗を優先し、なければ副の失敗を返す。`close(timeout=5.0)` は片方が
失敗しても両方の終了を試し、最初の例外を送出する。二つ目の終了例外もログに残す。

## 検証範囲

合成波形と偽モデルの単体テストのみ。単体 Maai / MaaiMultiple の辞書・起動・終了の形は
指定された0.2.27 のコードの静的確認に基づく。統合・欠測・委譲・終了失敗・工場の引数・
設定の既定と環境変数・起動と接続の配線も偽物で検証する。CPC / Mimi / BC / BC-Det の重み取得・推論、CPU 使用率、
実時間達成、相づちの向きや精度、会話の遅れは確認していない。
