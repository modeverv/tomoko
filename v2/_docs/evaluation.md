# Tomoko Evaluation Design

Tomoko の会話体験品質を、将来の最適化フェーズで定量評価できるようにするための設計メモ。

この文書の目的は、自然な会話相手らしさを「なんとなく良い/悪い」だけで扱わず、
人間評価と機械メトリクスの対応関係として記録し、後から重みづけ・回帰分析・設定最適化に使える形へ落とすことである。

---

## 基本方針

「キラキラした数式一発」で自然会話を解くのではなく、会話体験を複数の評価軸へ分解する。

各ターンまたは各セッションについて、人間が体験品質を評価し、同じ単位で latency、VAD、STT、LLM、TTS、
記憶検索、attention、barge-in、echo 抑制などの観測値を保存する。

その後、人間評価をゴールドラベルとして、どの観測値が体験品質に効いているかを分析する。
最終的には、設定値や実装優先度を感覚ではなく実測で調整できる状態を目指す。

---

## 会話体験スコアの分解

初期の総合スコアは、次のような重み付き和として扱う。

```text
conversation_quality_score =
  w1 * responsiveness
+ w2 * attended_feeling
+ w3 * turn_taking_naturalness
+ w4 * interruption_robustness
+ w5 * memory_naturalness
+ w6 * persona_consistency
+ w7 * recovery_quality
- w8 * awkward_delay
- w9 * false_participation
- w10 * echo_reaction
- w11 * context_mismatch
```

重み `w1...w11` は固定値として決め打ちしない。
初期は人間の直感で仮置きし、評価ログが溜まったら回帰分析や特徴量重要度で見直す。

---

## 評価軸

### responsiveness

Tomoko が遅すぎず返ってくるか。

観測候補:

- `speech_end_to_first_text_ms`
- `speech_end_to_first_audio_ms`
- `first_audio_chunk_to_playback_start_ms`
- `turn_total_latency_ms`
- `ContextSnapshotBuilder.elapsed_ms`
- LLM first token latency
- TTS first chunk latency

### attended_feeling

Tomoko が「今聞いている」と感じられるか。

観測候補:

- `attention_mode`
- attended 発話への応答率
- engaged 中の無視率
- cooldown 中の自然な follow-up 率
- low-confidence observer 発話の抑止率
- `ambient_logs.attended`
- `ambient_logs.participation_mode`

### turn_taking_naturalness

人間の発話終了、間、相槌、言い淀みの扱いが自然か。

観測候補:

- VAD silence threshold
- speech segment duration
- STT transcript length
- 1ターン内の不自然な分割数
- short transcript observer 判定数
- 相槌への過剰応答率
- 「えーっと」「あの」などの途中発話を切った回数

### interruption_robustness

Tomoko 発話中の割り込みを適切に扱えるか。

観測候補:

- hard interrupt true positive rate
- hard interrupt false positive rate
- soft interrupt 判定数
- backchannel 判定数
- `audio_control stop` 発行 latency
- interrupted turn 保存数
- stopped playback chunk count

### memory_naturalness

記憶を思い出すタイミングと内容が自然か。

観測候補:

- memory retrieval precision@k
- memory hit relevance score
- session summary hit relevance score
- retrieved memory age
- prompt に採用された memory count
- 人間評価による memory usefulness score
- 不自然な記憶参照の回数

### persona_consistency

Tomoko の人格、口調、関係性が一貫しているか。

観測候補:

- persona violation count
- style drift score
- emotion consistency score
- lexicon term usage accuracy
- persona snapshot version
- prompt に採用された persona slice count

### recovery_quality

誤認識、割り込み、遅延、文脈ズレから自然に復帰できるか。

観測候補:

- error turn count
- cancelled turn count
- interrupted 後の次ターン満足度
- stale result discard count
- degraded context response count
- user repair utterance count

---

## 人間評価

機械メトリクスだけで自然さを定義しない。
最初は人間評価を正とし、機械メトリクスはその近似として扱う。

各会話ターンに最低限以下を付ける。

```json
{
  "turn_id": "uuid",
  "session_id": "uuid",
  "overall_quality": 4,
  "responsiveness": 5,
  "attended_feeling": 4,
  "turn_taking_naturalness": 3,
  "interruption_robustness": null,
  "memory_naturalness": 4,
  "persona_consistency": 5,
  "recovery_quality": null,
  "notes": "記憶の出し方は自然。少し返答開始が遅い。"
}
```

スコアは 1 から 5。

- 1: 体験を壊す
- 2: 不自然さが目立つ
- 3: 許容範囲
- 4: 自然
- 5: かなり良い

`null` は、そのターンでは評価対象にならない軸を表す。

---

## 機械ログ

将来 `logs/evals/*.jsonl` のような形式で、ターン単位の観測値を保存する。

初期案:

```json
{
  "turn_id": "uuid",
  "session_id": "uuid",
  "created_at": "2026-05-24T12:34:56+09:00",
  "attention_mode_before": "engaged",
  "attention_mode_after": "cooldown",
  "vad": {
    "speech_ms": 1840,
    "silence_threshold_ms": 800,
    "audio_level_db": -22.4
  },
  "stt": {
    "elapsed_ms": 312.5,
    "transcript_chars": 24,
    "low_confidence": false
  },
  "context": {
    "depth": "normal",
    "elapsed_ms": 34.2,
    "timed_out": false,
    "included_counts": {
      "recent_turns": 8,
      "session_summaries": 2,
      "memory_hits": 0,
      "lexicon_terms": 3
    },
    "skipped_sources": []
  },
  "llm": {
    "first_text_ms": 180.3,
    "total_ms": 540.7
  },
  "tts": {
    "first_audio_ms": 107.0,
    "total_ms": 206.9,
    "chunk_count": 2
  },
  "playback": {
    "barge_in_class": null,
    "echo_suppressed": false,
    "audio_stop_sent": false
  },
  "memory": {
    "retrieved_count": 2,
    "adopted_count": 1
  },
  "result": {
    "status": "completed",
    "stale_results_discarded": 0,
    "degraded_context": false
  }
}
```

既存の `_docs/latency.md`、`TomoroSession` latency log、`ContextBuildTrace` はこの評価ログの前段として扱える。

---

## 分析の進め方

1. 会話セッションを録る
2. ターン単位で機械ログを保存する
3. 人間がターン単位またはセッション単位で評価する
4. 人間評価と機械ログを `turn_id` / `session_id` で join する
5. 相関、回帰、特徴量重要度を見る
6. 重みと設定値を更新する
7. 再度会話して評価する

初期分析では Pearson 相関だけに頼らない。
Tomoko の体験品質にはトレードオフと交互作用があるため、以下も見る。

- Spearman 相関
- 線形回帰
- Lasso / Ridge
- 決定木系モデルの feature importance
- SHAP 値
- latency bucket ごとの平均評価
- false positive / false negative の事例分析

---

## 注意点

単純な相関係数だけでは判断しない。

例:

- VAD silence threshold を短くすると応答は速くなるが、発話分割が増える
- memory retrieval を増やすと気が利く場合もあるが、ズレた記憶を出す危険も増える
- context depth を深くすると文脈は増えるが、latency と混入リスクが上がる
- hard interrupt 判定を強くすると止まりやすくなるが、相槌でも止まる

したがって、各メトリクスは単独で最適化せず、総合体験スコアと失敗事例を一緒に見る。

---

## 将来の最適化対象

評価ログが溜まったら、次の設定や実装を調整対象にする。

- VAD silence threshold
- follow-up 低信頼判定の条件
- playback ended grace window
- barge-in detector の閾値
- echo suppression window
- `ContextBuildPolicy.max_build_ms`
- context depth 選択ルール
- memory top-K
- session summary top-K
- lexicon term top-K
- TTS sentence split size
- TTS backend selection
- LLM backend selection

最終的には、設定変更ごとに評価ログを比較し、体験品質が改善したかを確認する。

---

## 完了判定の考え方

最適化フェーズでは、次のように完了条件を定義する。

```text
baseline と比較して:
  overall_quality 平均が上がる
  P95 speech_end_to_first_audio_ms が悪化しない
  false_participation が増えない
  echo_reaction が増えない
  memory_naturalness が下がらない
```

単一指標の改善だけでは完了にしない。
Tomoko の目的はベンチマーク最適化ではなく、人間から自然な会話相手として見られる体験品質の改善である。

---

## Phase 10.10 自発発話ログ評価

自発発話は「候補を読んだか」ではなく「会話の入口になったか」を見る。
評価 artifact は debug / tuning 用であり、DB の source of truth にはしない。

### ログ抽出

```bash
rg -n \
  "arrival candidate fetched|initiative candidate fetched|policy_decision|start_initiative_reply|start_arrival_reply|attention changed from ambient to engaged|conversation session started reason=followup|ThinkFastMode llm_prompt|TomoroSession reply_text delta" \
  logs/server-debug.log
```

確認する流れ:

- `arrival candidate fetched` / `initiative candidate fetched`
- `policy_decision` と score / threshold / reason
- `start_initiative_reply` または `start_arrival_reply`
- 直後の `attention changed from ambient to engaged`
- 人間の返答後の `conversation session started reason=followup`
- 直後 3 turn の transcript / `ThinkFastMode llm_prompt` / `reply_text`

### DB 確認

```sql
SELECT
  source,
  count(*) FILTER (WHERE spoken_at IS NULL AND dismissed_at IS NULL AND expires_at > now()) AS active,
  count(*) FILTER (WHERE maturity >= 1 AND generated_text IS NOT NULL) AS text_ready,
  count(*) FILTER (WHERE maturity >= 2 AND generated_audio IS NOT NULL) AS audio_ready,
  count(*) FILTER (WHERE spoken_at IS NOT NULL) AS spoken,
  count(*) FILTER (WHERE dismissed_at IS NOT NULL) AS dismissed
FROM utterance_candidates
GROUP BY source
ORDER BY active DESC, spoken DESC;

SELECT id, source, generated_text, spoken_at, priority, urgent, context_tags
FROM utterance_candidates
WHERE spoken_at IS NOT NULL
ORDER BY spoken_at DESC
LIMIT 20;

SELECT id, started_at, start_reason, ended_at, end_reason
FROM conversation_sessions
ORDER BY started_at DESC
LIMIT 20;
```

### 手動評価観点

- `starts_conversation`: 人間が返したくなるか
- `not_abrupt`: 直前文脈から見て唐突すぎないか
- `self_contained`: 何の話か一発でわかるか
- `recoverable`: ユーザーが聞き返した時に Tomoko が話題を保持できるか
- `low_intrusion`: 今話しかけてよい温度か

最低 2 ケースを LOG に残す:

- 成功: 自然に返答され、会話が 2 turn 以上続いた
- 要改善: 唐突、撤回、主語欠け、話しすぎ、または文脈衝突
