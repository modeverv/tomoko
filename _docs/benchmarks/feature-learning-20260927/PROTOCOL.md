# 苦手例追加と現行文字特徴の学習可能性（事前条件）

2026-09-27。新しい教師ラベル・学生結果・blind本文/参照を親/runnerが見る前に固定する。

## 問いと範囲

現行の文字1–4 gramをhash8192へ写しL2正規化し、長さ/疑問末尾/final/抑制接頭辞/逆接末尾の5特徴を加えるRidgeで、追加の対比例から応答可/待つの違いを学べるか。音声終了、実会話品質、runtime採用はこの実験の対象外。実会話やJDDは使わない。

## データ

- base: 前回のCodex採点160 train。同じ入力/ラベルをそのまま使う。
- development: 前回eval80。前回結果を弱点探索に使ったため未見評価とは呼ばない。
- train-additions: 新規人工640例。苦手例targeted320(160group)と一般例general320(160group)。各armでカテゴリとready/waitが均衡するgroup順を事前固定する。期待値とarmメタデータはreference側に保存し、教師にはid/textだけを送る。
- blind: 別担当が前回予測/新trainを見ず独立に作る新規240例(120groupを目安)。入力とAI参照をモデル評価前に固定。曖昧な例は二値集計から除外し件数を示す。
- train/dev/blindのID/group/空白・句読点等を正規化したtextの重複を拒否する。近似重複を検査して結果を保存し、明白なコピーがあれば採点/評価前に解消する。対比pair内の共通文脈は同じgroupとして扱う。
- 人工期待値は人間goldではない。同系統AIの生成・判定バイアスと対比構造由来の偏りが残る。

## 教師と固定条件

前回と同じcollectorでCodex gpt-6-astra / medium、20件batch。採点者にreference/カテゴリ/arm/未来の続きを渡さない。新train640のみを採点し、元の期待値と不一致でも教師スコアを無断修正しない。

学生は8192 / ngram1–4 / ridge_lambda0.01 / threshold0.75 / 全件is_final=True固定。追加例の期待ラベルを学習目標に使用しない。runtimeコード・既定モデルは変更しない。

## 比較条件

1. baseline: base160
2. targeted80: base160+苦手80
3. targeted160: base160+苦手160
4. targeted320: base160+苦手320
5. general320: base160+一般320
6. combined640: base160+苦手320+一般320
7. baseline_char_only: baselineと同じhash文字特徴・正規化、追加5特徴のみゼロ
8. combined640_char_only: combined640と同条件で追加5特徴のみゼロ

subsetはカテゴリ巡回のgroup作成順でnestedにする。全条件を事前に固定し、blindに合わせてハイパーパラメータを選ばない。

## 計測と分析

- 各fit3回、条件順を反転して交互計測。学習内教師MAE/閾値一致とAI参照一致を分離する。
- fixed0.75のdevelopmentとblind一致率、誤開始(false-ready)/待ちすぎ(false-wait)、カテゴリ別、ready/waitのpair順位を保存。
- 副診断としてdevelopmentだけで閾値0.05..0.95(0.01刻み)の一致率最大を選び、同率なら0.75に近い方、さらに同率なら高い方を選ぶ。blindで選ばず、固定閾値の主結果と別表示し誤開始の変化を示す。
- 常駐予測warmup100/repeats1000、同じblind入力を巡回しmean/p50/p95を記録。raw scorer単体でshort-ack補正やruntime gateを含まない。
- featureの完全一致衝突、pairのcosine、手作り5特徴と文字部分の寄与、旧取りこぼしの変化を診断。単語/構文の理解能力全体や実会話の正確性はこの小規模実験から断定しない。
- 条件間の差は同一groupで比較。関連pairを独立な会話として扱わない。統計区間を出す場合はgroup単位でbootstrapする。

## 完了条件

新データ/教師raw/学習条件/全予測/集計/fit・推論時間を保存。先行テストとunit/ruff/diff checkを実行。結果が改善しない場合も含め報告し、runtime変更は別判断とする。
