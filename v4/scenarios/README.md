# シナリオ

- `small/`: 変更のたびに回す組。各軸 10 件（T5 は長いので 5 件）。台本は Claude Code（Fable 5.2）が書いた。
- `small-v2/`: small の第 2 版（2026-11-13）。T5 と C2 の途中の無音を、話が続く形のあとに置いた。id・引き金・答えは small と同じ。台本は agy（Gemini 4 Argon High）が書き、Claude Code（Opus 5.5）が確かめた。違いは `small-v2/README.md`。
- 全の組（各軸 50 件ほど）は、まだない。小の組と台本を重ねずに作る。
- C3（調べものの QA）の台本は、まだない。検索しないと答えられず、かつ答えを確かめた質問が要るので、P4 で作る。

書き方は `bench/scenario.py` の `Step` を見る。`[pause 1.2]` は 1.2 秒の無音。
