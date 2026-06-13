# v2-alpha: Initiative Motivation Offline Sandbox

作成: 2026-06-14

## 目的

自発発話や横槍の runtime 実装に入る前に、既存ログと DB candidate だけを使って
「Tomoko がいつ話したくなったか」「その時 prompt に何が乗るはずか」「閾値を変えるとどこで fire するか」を
オフラインで可視化する。

このフェーズでは `/ws`、`TomoroSession`、本番 candidate final gate、TTS、conversation lifecycle を変更しない。
声を出して会話しながら調整するコストを減らすため、実会話ログを何度も replay し、
UI 上でパラメータと発火候補を比較できる状態を作る。

## 前提

- v2 shadow lane は partial STT / VAP / fusion / advisory の時間軸を既に持つ。
- `initiative_policy.py` には `DesireLoadAverages` / `SpeakabilityLoadAverages` /
  `CandidateSpeakPolicy` の骨格がある。
- `utterance_candidates` には active / expired / spoken / dismissed を含む候補履歴が残る。
- `conversation_logs` と `logs/turn-taking-main.jsonl` から実会話の時系列を復元できる。

## 守ること

- 本番会話の挙動は変えない。
- LLM dry-run は UI で明示実行した時だけ行う。
- candidate DB は read-only とし、spoken / dismissed / expired を更新しない。
- まずは hardcoded gain / threshold でよい。正しさより波形と比較可能性を優先する。
- source of truth は既存ログと DB snapshot。UI 側で編集した候補は simulation artifact として扱う。

## 1. Candidate Snapshot Export

現時点の DB から、死んでいる candidate も含めて simulation 用 JSON を作る。

対象:

- `utterance_candidates`
  - active
  - expired
  - spoken
  - dismissed
  - `maturity=0/1/2`
- `arrival_candidates`
  - fresh
  - expired
  - used
- 必要に応じて `world_observation_interpretations` の `candidate_seed_text`

出力例:

```json
{
  "exported_at": "2026-06-14T00:00:00+09:00",
  "utterance_candidates": [
    {
      "id": "...",
      "source": "world_observation",
      "seed": "同じ操作を繰り返していることが少し気になる。",
      "generated_text": "さっきから同じところ直してるね。",
      "priority": 0.7,
      "urgent": false,
      "maturity": 1,
      "created_at": "...",
      "expires_at": "...",
      "spoken_at": null,
      "dismissed_at": null,
      "context_tags": ["motive:teasing", "topic:screen"],
      "metadata_json": {
        "motive": "teasing",
        "motive_strength": 0.72,
        "intrusion_risk": 0.35,
        "tone_hint": "light_tease"
      },
      "lifecycle": "expired"
    }
  ]
}
```

UI ではこの JSON を textarea に表示し、コピペ・手編集できるようにする。
最初は DB から直接 UI に繋がず、exported artifact を読み込むだけにする。

実装候補:

- `server/tools/export_initiative_candidates.py`
- 出力先: `reports/initiative-motivation/candidates-YYYYMMDD-HHMMSS.json`
- unit test:
  - lifecycle 分類
  - timestamp の ISO 化
  - `metadata_json` / `context_tags` の欠損に強いこと

## 2. Conversation Log Replay

既存の会話ログから、1 秒刻みの simulation timeline を作る。

入力:

- `logs/turn-taking-main.jsonl`
- `logs/turn-taking-v2-shadow.jsonl`
- `logs/backend-trace.jsonl` optional
- `conversation_logs` optional
- candidate export JSON

復元する state:

- user speaking / silence
- Tomoko speaking / playback
- floor available
- recent user transcript
- recent Tomoko reply
- attention mode
- VAD state
- v2 `p_yielding`
- v2 `fusion_score`
- active candidate pool at timestamp

出力 snapshot:

```json
{
  "ts_ms": 1710000000000,
  "relative_sec": 42.0,
  "user_speaking": false,
  "tomoko_speaking": false,
  "floor_available": true,
  "silence_sec": 7.2,
  "candidate_count": 4,
  "top_candidate_id": "...",
  "curiosity_pressure": 0.52,
  "teasing_pressure": 0.18,
  "attachment_pressure": 0.31,
  "unspoken_pressure": 0.12,
  "speak_score": 0.64,
  "dominant_motive": "curiosity",
  "would_fire": {
    "0.55": true,
    "0.65": false,
    "0.75": false
  }
}
```

実装候補:

- `server/tools/simulate_initiative_motivation.py`
- 出力先:
  - `reports/initiative-motivation/session-YYYYMMDD-HHMMSS.json`
  - `reports/initiative-motivation/session-YYYYMMDD-HHMMSS.html`

## 3. Motivation Model v0

最初は hardcoded deterministic model とする。

```python
MOTIVE_GAINS = {
    "curiosity": 0.35,
    "teasing": 0.28,
    "attachment": 0.22,
    "unspoken": 0.30,
}

DECAY_SEC = {
    "short": 60.0,
    "mid": 300.0,
    "long": 1800.0,
}

THRESHOLDS = [0.55, 0.65, 0.75]
```

pressure update:

```text
candidate present
  -> motive_pressure += motive_strength * motive_gain

silence while user present
  -> attachment_pressure += silence_gain

same candidate remains unspoken
  -> unspoken_pressure += unspoken_gain

user speaking or Tomoko speaking
  -> floor_available = false

recent rejection / stop
  -> restraint penalty
```

score:

```text
speak_score =
  curiosity_pressure
  + teasing_pressure
  + attachment_pressure
  + unspoken_pressure
  + floor_weight * floor_available
  + freshness_weight * candidate_freshness
  - intrusion_weight * candidate_intrusion_risk
  - user_speaking_penalty
  - tomoko_speaking_penalty
  - rejection_penalty
```

この model は本番 policy ではなく、UI で人間が挙動を見るための sandbox model である。
後で `CandidateSpeakPolicy` に移植するかどうかは別 Phase で決める。

## 4. Slider UI

HTML 1 枚でよい。最初は server を立てずに `file://` で開ける構成にする。

表示:

- 会話タイムライン
  - user speech 区間
  - Tomoko speech 区間
  - silence / floor available
  - transcript / reply text
  - v2 advisory marker
- motivation chart
  - curiosity pressure
  - teasing pressure
  - attachment pressure
  - unspoken pressure
  - speak score
  - threshold line
  - would-fire marker
- parameter panel
  - curiosity gain
  - teasing gain
  - attachment gain
  - unspoken gain
  - floor weight
  - intrusion penalty
  - threshold
  - decay values
- candidate editor
  - exported candidate JSON textarea
  - selected fire marker の top candidate metadata

操作:

- slider を動かすとブラウザ側で score と fire marker を再計算する。
- threshold を変えると「この値ならここで横槍を入れようとする」が縦線で見える。
- fire marker をクリックすると prompt preview を開く。

## 5. Prompt Preview

fire marker 時点で、実際に自発発話 LLM に渡るはずの prompt を近似構築して表示する。

構成:

- base persona summary
- recent conversation window
- current floor context
- candidate / interest context
- motivation snapshot
- rule-generated motive directive
- output contract

rule-generated motive directive 例:

```text
dominant_motive=teasing:
Tomoko は今、少しだけ「ちょっとちょっかいをかけたい」気持ちがある。
ただし相手の作業を邪魔しすぎないように、短く軽い一言にする。

dominant_motive=curiosity:
Tomoko は今、画面で起きていることが気になっている。
詰問ではなく、ふと気になった感じで短く聞く。

dominant_motive=attachment:
Tomoko は少し構ってほしい気持ちがある。
重くならず、相手の反応を待てる短い声かけにする。
```

preview の目的は生成文の完全予測ではない。
見るべきものは以下:

- この時刻に話す根拠が自然か
- prompt に乗る興味や気になりごとが文脈とズレていないか
- 横槍として十分短い制約になっているか
- `teasing` などの内心が prompt 上で過剰に強くなっていないか

## 6. Offline LLM Dry-run

UI から選択した fire marker だけ、実際の発話 LLM へ dry-run できるようにする。

最初は batch CLI でもよい:

```bash
uv run python server/tools/run_initiative_prompt_dryrun.py \
  --simulation reports/initiative-motivation/session.json \
  --marker-id marker-42 \
  --config config/central_realtime.toml \
  --output reports/initiative-motivation/dryrun-marker-42.json
```

dry-run は以下を保存する:

- prompt
- selected backend
- first token latency
- total latency
- generated text
- emotion line / format validation
- human-readable comparison note

UI では dry-run result を marker に紐づけて表示する。
本番会話には送信しない。TTS も鳴らさない。

## 7. 無音時シミュレーション

会話ログがない時間帯も、candidate と silence だけで motivation がどう溜まるかを見る。

入力:

- start time
- duration
- candidate export JSON
- optional initial pressures
- user presence flag

出力:

- 1 秒刻み snapshot
- threshold ごとの first fire time
- dominant motive の推移
- candidate が expired した時の score drop

用途:

- 「何も起きていない時に attachment だけで話しすぎないか」
- 「world observation candidate が入ったら何秒で fire するか」
- 「dead candidate を復活候補として見るとどの程度圧が残るか」

## 8. 実装 Phase

### v2-alpha.0: static design artifact

- [x] `_docs/v2-alpha.md` を追加する。

### v2-alpha.1: candidate export

- [x] `server/tools/export_initiative_candidates.py` を追加する。
- [x] active / expired / spoken / dismissed / used を lifecycle として JSON に落とす。
- [x] unit test で lifecycle classification と JSON shape を固定する。

### v2-alpha.2: log replay model

- [x] `server/tools/simulate_initiative_motivation.py` を追加する。
- [x] `turn-taking-main.jsonl` と `turn-taking-v2-shadow.jsonl` を読み、1 秒刻み snapshot を生成する。
- [x] candidate export を読み込み、timestamp ごとの candidate pool を近似する。
- [x] unit test で speech interval / silence / floor_available / score の最小ケースを固定する。

### v2-alpha.3: HTML sandbox

- [x] simulation JSON から standalone HTML を生成する。
- [x] `make initiative-sim` では直近 session ID を最大100件集め、HTML 上でセッションを選べるようにする。
- [x] sliders で score / threshold / fire marker をブラウザ側再計算する。
- [x] candidate JSON textarea を表示し、候補状態をコピペできるようにする。
- [ ] UI 上で手編集した candidate JSON を再計算へ反映する。
- [x] fire marker click で motivation snapshot と nearby conversation を表示する。

### v2-alpha.4: prompt preview

- [x] fire marker から prompt preview を組み立てる。
- [x] motive directive を hardcoded rule で生成する。
- [x] recent conversation / candidate / motivation / output contract を UI に表示する。
- [x] snapshot test で prompt sections を固定する。

### v2-alpha.5: offline LLM dry-run

- [x] marker 単位で prompt を実 LLM に投げる CLI を追加する。
- [x] selected backend / latency / generated text / format validation を JSON に保存する。
- [ ] UI が dry-run result JSON を読み込んで marker に表示できるようにする。
- [x] dry-run は明示実行のみで、simulation HTML を開いただけでは LLM を呼ばない。

### v2-alpha.6: 無音 simulation mode

- [x] 会話ログなしで duration / candidate / initial pressure から snapshot を生成する。
- [x] threshold ごとの first fire time を summary に出す。
- [x] HTML で silent timeline と candidate expiry / pressure decay を可視化する。

## 完了条件

- 既存会話ログを読み、1 秒刻みの motivation snapshot と fire marker を HTML で見られる。
- slider で `curiosity` / `teasing` / `attachment` / threshold を変えた時、発火位置が即座に変わる。
- fire marker をクリックすると、その時点で LLM に渡す prompt preview が見える。
- 選択した marker だけ実 LLM dry-run でき、結果が UI で比較できる。
- 無音時の candidate pressure / attachment pressure の蓄積と first fire time を見られる。
- 本番 runtime、WebSocket、TomoroSession final gate、TTS、DB lifecycle は変更しない。
