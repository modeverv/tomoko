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
