# Codex collection operator recipe

この手順は Tomoko 本体ではなく、外部観測 Markdown を作るための operator workflow です。
Browser / Computer Use / Perplexity UI は壊れる前提で扱い、壊れたら手順や prompt を直します。

1. Perplexity を開く。
2. `informations/prompts/daily_world_observation.md` の prompt を貼る。
3. 1 万字程度の日本語 Markdown を得る。
4. private page、account 情報、secret、個人情報が混ざっていないことを目視する。
5. `informations/work/YYYY-MM-DD-world-observation.md` に保存する。
6. `make information-ingest-dry-run` を実行する。
7. 問題なければ `make information-ingest-once` を実行する。
8. `make information-interpret-once` を実行する。
9. 必要なら `make thinker-once` / `make journalist-once` を実行する。

取得 prompt には「完全な schema compliance は不要。後段 validator が落とす」と明記してある。
Perplexity の出力が揺れても Tomoko の DB や `/ws` に直接触れない。

## Codex への外部観測収集指示

Perplexity を Computer Use で使うときは、長い日本語 prompt を `type_text` で直接入力しない。
入力が崩れる場合があるため、clipboard / set_value / paste 経由で投入する。

Perplexity の回答は copy button ではなく、成果物パネルの
`ダウンロード` -> `Markdown形式でダウンロード` を優先する。
copy button 由来の Markdown は frontmatter delimiter が `***` になることがある。

保存後は必ず次を実行する。

```bash
mise exec -- uv run python _tools/validate_world_observation_md.py --strict informations/work/YYYY-MM-DD-world-observation.md
make information-ingest-dry-run
```

validator が失敗した場合は、本文を勝手に要約・改変せず、まず frontmatter delimiter / required fields / observed_at / topics だけを確認する。
実データ artifact は `informations/work/` に置き、git に入れない。
LOG.md には作業開始・保存結果・validator 結果・dry-run 結果を追記する。
MEMORY.md は新しい設計判断が発生した場合だけ追記する。


