# MaAI のオフライン評価

生成済みの入力だけで、MaAI の信号を調べる。対話エンジンへの組み込み、応答遅延、実際に相づちを打ったときの体験は別の検証になる。

```sh
rtk proxy .venv/bin/python scripts/maai_offline.py work/inputs/small \
  --modes vap,bc,bc_det --out _docs/data/maai-offline-v1inputs.json

rtk proxy .venv/bin/python scripts/maai_offline.py work/inputs/small-v2 \
  --modes vap,bc_2type,bc_det --out _docs/data/maai-offline-v2inputs.json
```

`--axes T2,T5` のように対象を絞れる。既定のモードは従来どおり `vap`。
各モデルは1本ずつ動かし、torch は2スレッド。CPC (`model_type=normal`)、10 Hz、文脈5秒が既定。
MaAI の worker と同じ `process()` を同期で1フレームずつ呼び、結果を回収してから次へ進む。
入力キューへの蓄積はなく、結果が欠ける／余るとエラーにする。時刻は消費サンプル数で決める。
モデルはモード内で使い回し、クリップごとに音声・encoder・KV の状態と結果キューをリセットする。
最後の端数フレームは評価せず、そのサンプル数を JSON の `timing` に残す。

## 入力と区間

- `*/scenario.json` と注釈の wav を読む。各シナリオの先頭の発話を選び、T3/T4 は最後の発話も独立したクリップとして選ぶ。C2 は最初だけ。
- wav は16 kHz。前1秒、後2秒に無音を足し、注釈も1秒ずらす。Tomoko 側は全区間で無音。
- `pauses` を hold、最後の `voiced` の終端を shift とする。台本の `[pause]` 直前の句読点から comma / period / other を割り当てる。
- 生成器の `voiced` は合成した文字列全体の区間。合成音声の自然な小休止が中に含まれることがあり、連続して声があることを保証しない。`pauses` にない自然な無音を、新たな hold として検出しない。
- 区間は先頭を含み末尾を含まない `[start, end)`。境界の浮動小数点誤差を吸収する。

## 集計の読み方

| モード | 集計 | 正例・比較の単位 |
|---|---|---|
| vap | 各軸・0.1/0.2/0.3/0.4秒・p_now/p_future の分布と AUC | shift が正例、hold が負例。hold は遅れ時点でまだ無音のものだけ |
| bc / bc_2type | T5 の pause_comma / pause_period / pause_other / speech / end の件数・平均・10%点・中央値・90%点 | フレーム単位。end は shift 後0.4秒。speech は注釈の voiced の区間 |
| bc_det | T3/T4 の最後の発話におけるフレーム分布、発話ごとの最大値の分布と AUC | T3 の相づちが正例。最後の発話の voiced と終了後0.4秒を対象にし、1発話を1標本にする |

AUC は正例の値が負例より大きい確率で、同点を0.5とする。片方の標本がなければ JSON は `null`、表は `—`。
分布も標本がなければ `null`。1件なら、その値が各分位点になる。
vap は問い合わせ時刻を超えない直前の予測を使い、最後の予測より先の遅れは欠測にする。

BC の予測ターゲットには先読みがあるため、無音中の値だけで機会の正誤を確定しない。
bc_2type の reactive / emotional は別々に集計し、足し合わせない。
bc_det の AUC は最大値による発話単位の順位。発話が長いほど最大値を取る機会も増えるので、フレーム分布と一緒に見る。
また、Tomoko が話している音を与えていない。実際の同時発話で相づちと割り込みを分けられるかは未測定。

## チャンネルの根拠

パッケージの `models/vap_bc.py` / `vap_bc_2type.py` の `encode_audio()` と `batch.py` の `SWAP_CHANNEL_MODES` を確認した。
`bc_2type` の一部の docstring は実際の swap と食い違うため、実行される処理に合わせた。

| モード | 公開入力 ch1, ch2 | 採点に使う出力 |
|---|---|---|
| vap | user, system（無音） | p_now[1], p_future[1] |
| bc / bc_2type | system（無音）, user | scalar p_bc / p_bc_react / p_bc_emo。内部で swap |
| bc_det | user, system（無音） | p_bc_det[0]。swap なし |

無音も `MaaiInput.Chunk` で渡す。`Zero` や ch2 省略では mono モデルが選ばれ、意味が変わる。
VAP の値は両話者の活動の相対的な割合で、絶対的な言い終わり確率ではない。
`bc` 内の `p_bc_detect` と、`bc_det` モードの `p_bc_det` は別のヘッドなので混同しない。

## JSON と互換性

生成済み入力、または複数モードの JSON は `schema_version=2`。
`settings` にモード・文脈・チャンネル・無音の長さ・パッケージ版、`timing` にロード時間・推論時間・フレーム数・実際に選ばれたモデル variant を保存する。
`summary` に集計、`results` に集計元の評価行を置く。BC は該当フレーム、BC-Det は発話単位の行と選ばれたフレームを残す。

従来の `work/runs/<run>` も受け付ける。VAD で無音を取り、各 step の終端を shift にする従来の方法を維持する。
旧 run を vap だけで読む場合は、既定の軸 T1,T2,C2,T5 と、JSON の行リスト形式（id/kind/t/gap/d0.1〜d0.4）も維持する。
旧 run の途中の無音は句読点の注釈がないので、BC では pause_other とする。

## 2026-11-13 の検証状況

集計・入力の選択・チャンネル順・結果の逐次回収・旧 CLI の互換性を、偽の予測で単体テストした。MaAI 自体は単体テストで import しない。
small の実測に必要な重みは VAP と CPC が既存キャッシュにある。BC / BC-Det はなかった。
BC の重みを取得しようとしたが、`huggingface.co` の名前解決に失敗した（`Errno 8` → `LocalEntryNotFoundError`）。
依頼に従って実測を止めた。推論モデルは走らせず、`_docs/data/maai-offline-v1inputs.json` は作っていない。
