# daily_world_observation prompt

あなたは外部観測レポートを作る係です。
以下の条件で、日本語 Markdown の成果物を 1 万字程度で作成してください。

成果物:
- title: `world_observation_2026-05-25`
- output format: Markdown
- code fence で囲まない
- 会話文の説明ではなく、保存可能な Markdown 文書だけを出力する
- Perplexity の成果物 / document として扱える場合は、この title の文書として作成する

目的:
- Tomoko が後で世界の様子をゆっくり解釈するための raw artifact を作る
- frontmatter は後段 validator が読むので、ここだけは厳密に守る
- 本文は後段 normalizer が読む raw text なので、多少の表現揺れは許容する
- 事実と推測を混ぜず、出典や手がかりが弱いものは弱いと書く

frontmatter は必ず次の形で始めてください。
開始 delimiter と終了 delimiter はどちらも `---` にしてください。
`***` や水平線で代用しないでください。

```yaml
---
schema_version: 1
kind: world_observation_batch
generated_by: perplexity
observed_at: 2026-05-25T09:00:00+09:00
language: ja
topics: [news, economy, technology, culture, local_life, ai, local_inference]
source_policy: public_web_summary_only
collection_prompt_version: daily_world_observation_v1
---
```

frontmatter の直後は、空行を 1 行入れてから本文を始めてください。

本文に含める topic:
- news: 世界と日本の主要ニュース
- economy: 市場、生活費、仕事への影響がありそうな動き
- technology: 開発者が気にしそうな技術ニュース
- culture: 本、音楽、映像、ネット文化
- local_life: 日常生活や季節感につながる話題
- ai: AI サービス、研究、規制
- local_inference: ローカル推論、Apple Silicon、MLX、音声モデル

本文の構成:
- `# 外界観測レポート 2026-05-25` から始める
- topic ごとに `## news` のような heading を置く
- 各 topic に 3〜5 個の観測項目を書く
- 各観測項目は次の形を基本にする
  - `事実:` 公開ソースで確認できること
  - `推測・含意:` その事実から考えられる影響。断定しすぎない
  - `source_hint:` Reuters、公式ブログ、GitHub、Hugging Face、法律事務所解説、行政資料など、出典の種類が分かる短い手がかり

注意:
- private page、アカウント内の情報、秘密情報は入れない
- 個人情報やログインが必要なページに依存しない
- 2026-05-25 時点で確認できないことを、確認済みの事実として書かない
- 古い情報や日付が曖昧な情報は、その弱さを明記する
- 各項目には source_hint を必ず添える
- 不確かな内容は断定しない
- 引用や参照番号はあってもよいが、本文だけ読んでも出典の種類が分かるように source_hint を残す

出力後の利用:
- Codex operator は Perplexity の `Markdown形式でダウンロード` を使って保存する
- copy button 由来の Markdown は frontmatter delimiter が崩れることがあるため、download 用の Markdown として成立する形を優先する
