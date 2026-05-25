# daily_world_observation prompt

あなたは外部観測レポートを作る係です。
以下の条件で、日本語 Markdown を 1 万字程度で作成してください。

目的:
- Tomoko が後で世界の様子をゆっくり解釈するための raw artifact を作る
- 完全な schema compliance は不要。後段 validator / normalizer が落とす前提でよい
- 事実と推測を混ぜず、出典や手がかりが弱いものは弱いと書く

frontmatter は可能なら次の形にしてください。

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

本文に含める topic:
- news: 世界と日本の主要ニュース
- economy: 市場、生活費、仕事への影響がありそうな動き
- technology: 開発者が気にしそうな技術ニュース
- culture: 本、音楽、映像、ネット文化
- local_life: 日常生活や季節感につながる話題
- ai: AI サービス、研究、規制
- local_inference: ローカル推論、Apple Silicon、MLX、音声モデル

注意:
- private page、アカウント内の情報、秘密情報は入れない
- Markdown は多少崩れてもよい
- 各項目には、できるだけ source hint を添える
- 不確かな内容は断定しない
