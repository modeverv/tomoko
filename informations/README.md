# informations

`informations/` は、Perplexity や Codex Computer Use などの外部観測から得た
raw Markdown artifact を Tomoko 本体から隔離して置く場所です。

raw Markdown は Tomoko が信じる事実ではありません。人間が読める外部観測の原稿であり、
Tomoko の記憶や自発発話へ入る前に validator、LLM normalizer、DB schema validation を通します。

## directory contract

- `work/`: 未取り込み、または取り込み待ちの raw Markdown
- `archived/`: 正常取り込み済み raw Markdown
- `failed/`: parse / validation / normalize に失敗した raw Markdown と error sidecar
- `prompts/`: Perplexity / Codex Computer Use に渡す収集 prompt
- `samples/`: public repo に置ける架空 artifact

`work/`、`archived/`、`failed/` は実データを含むため git 管理しません。
public repo に置いてよいのは `samples/` の架空 artifact だけです。

## ingest

```bash
make information-ingest-dry-run
make information-ingest-once
make information-interpret-once
```

取り込みは `/ws` / `TomoroSession` の hot path では実行しません。
外部観測は background / local job で検査し、validated interpretation だけを
thinker / journalist が読みます。
