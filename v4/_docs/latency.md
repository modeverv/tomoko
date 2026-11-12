# 処理時間の記録

## 2026-11-13（Codex）: 1 回合成の注釈

- 対象: `bench.generate.tts.annotate_one_pass` のみ。会話の遅れや MaAI の精度の測定ではない。
- 入力: 16 kHz、25.2 秒の人工波形。振幅 0.1 の 3 秒区間が 7 個、0.7 秒のゼロ区間が 6 個。本人の録音・モデル・ネットワークは使っていない。
- `time.perf_counter()` による 1 回の計測: **1.389 ms**。voiced 7 個、pause 6 個を想定した境界で検出した。単発の参考値であり、実音声への精度や性能の保証ではない。
- 計測値と注釈: `work/one-pass-smoke-wp5xysol/annotation-timing.json`（Git 管理外）。
- 実際の `say` では T5.json の t5-01 だけを CLI の `--one-pass` で試した。0.423 秒で失敗し、サンドボックスの `/usr/bin` の権限エラー後に無音が返った。生成時間の有効な測定値ではなく、実音声の注釈も得られていない。詳細は同ディレクトリの `result.json`。
