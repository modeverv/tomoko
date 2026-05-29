# ARCHITECTURE.md

このドキュメントは Tomoko の全体設計と、なぜその設計にしたのかの理由を残すものです。

## 設計の一言サマリ

TomoroSession is the final owner of conversational state.
Gateway converts physical protocol events into session signals.
Reducer decides state transitions.
Effects execute commands emitted by reducers/session.
Audio bytes stay on primitive hot path.
Do not add wrappers unless they clarify ownership or ordering.

**「内面の時間が流れている存在」としての Tomoko を、ノード分散 + PostgreSQL 中央集約で実現する。**

コアロジックは起動後に変わらない。環境（バックエンド・ノード構成）だけが変わる。

## Session closed-loop architecture

TomoroSession の内部は、外部入力と内部副作用の結果を区別しすぎず、同じ閉じたループへ戻す状態機械として読む。

```
input
  -> changer
     <info>
  -> state
     <demand>
  -> watcher
  -> output
  -> new input
```

### 用語

- `input`: gateway / client / timer / backend / output result から来る事実。ユーザー音声、semantic signal、playback telemetry、LLM 結果、TTS 結果、DB 結果、worker 結果などを含む。
- `changer`: input を解釈し、state に情報を書き込む責務。LLM / TTS / DB / WebSocket send などの外部副作用を直接実行しない。
- `<info>`: changer から state へ書かれる状態更新情報。これは demand ではない。
- `state`: 現在の事実と、未実現の output need / demand を保持する場所。state 自体は外部副作用を実行しない。
- `<demand>`: state に現れた「外へ何かを実現してほしい」という必要性。watcher はこれを読んで output を起こす。
- `watcher`: state / demand を見て output intent を実現する責務。会話判断の中心になる state mutation は changer 側に寄せる。
- `output`: 外部副作用。client JSON signal、audio chunk、DB read/write、LLM call、TTS call、worker request、candidate store update、log/trace などを含む。
- `new input`: output の結果として戻ってくる事実。LLM token / final response、TTS audio chunk、DB read result、DB write completion、playback result、worker result などは再び input として扱う。

### 原則

1. changer は state を変えるだけで、外部副作用を直接実行しない。
2. state は現在の事実と demand を持つだけで、外へ何かを送らない。
3. watcher / output が外部副作用を担当し、その結果は必ず input として戻す。
4. gateway 由来の input と、session 内 output 由来の input を構造上は同じ loop に戻す。
5. 将来、温度・湿度・presence・vision・mechanics などの入力が増えても、特別な例外 path を増やさず input 種別を足す。
6. 非同期処理は例外 path ではなく、`output -> new input` の一種として扱う。
7. watcher は賢くしすぎず、判断の中心は changer/state 側に置く。
8. input は同じ loop に戻すが、origin / causation / correlation id / turn id などの由来と対応関係は失わない。

現行コードの `SessionCommand` は、この設計では demand / output intent に近い。
`StateEmission` / `SessionOutputSignal` / audio chunk は output path の表現である。
今後の package split では、既存の public API を壊さず、TomoroSession を final owner としたまま、内部責務をこの loop に寄せていく。

## LLMによる推論結果の取得

技術的に可能な場合、徹底して構造化出力機能を利用すること。

---

## ノード構成の全体像

```
[エッジA キッチン]    [エッジB リビング]    [エッジC 寝室]
  VAD                   VAD                   VAD
  STT（whisper）        STT（whisper）        STT（whisper）
  TTS（irodori）        TTS（irodori）        TTS（irodori）
  軽量LLM（任意）       軽量LLM（任意）       軽量LLM（任意）
  話者識別              話者識別              話者識別
  音量計測              音量計測              音量計測
       │                     │                     │
       │ PresenceReport            AmbientLog
       │ テキストのみ（音声は外に出さない）
       └─────────────────────┬───────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ 中央リアルタイムノード          │
              │  DirectSpeakerResolver        │
              │  DuplicateSpeechFilter        │
              │  InferenceRouter              │
              │  ContextSnapshotBuilder       │
              │  gateway / TomoroSession      │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ PostgreSQL（唯一の真実）       │
              │  presence        誰が今どこに  │
              │  edge_status     エッジ状態    │
              │  conversation_sessions         │
              │  conversation_logs            │
              │  ambient_logs                 │
              │  utterance_candidates         │
              │  arrival_candidates           │
              │  emotion_log                  │
              │  persona_state                │
              │  persona_lexicon_versions     │
              │  persona_state_versions       │
              │  diary                        │
              │  inference_metrics            │
              └──────────────┬───────────────┘
                             │
              ┌──────────────┴───────────────┐
              │                              │
┌─────────────▼──────────┐  ┌───────────────▼──────┐
│ 中央バックグラウンドノード │  │ クラウド（オフロード） │
│  thinker               │  │  Anthropic API 等     │
│  journalist            │  │  プライバシー非依存の  │
│  session_summarizer     │  │  タスクのみ           │
│  persona_update        │  └──────────────────────┘
│  lexicon_update        │
│  embedder              │
└────────────────────────┘
```

---

## ノードの責務

### エッジノード（各部屋）

```
責務:
  VAD（発話区間検出）
  STT（音声→テキスト）
  TTS（テキスト→音声、スピーカーへ）
  話者識別（pyannote）
  音量計測
  PresenceReport を中央DBに書く
  AmbientLog を中央DBに書く

持たないもの:
  会話の文脈
  Tomoroの状態
  utterance_candidates

起動設定例:
  CONFIG=config/edge_kitchen.toml python -m server.edge.main
```

音声データは外に出ない。テキストだけが中央に流れる。

### 中央リアルタイムノード

```
責務:
  正規発話元の判定（DirectSpeakerResolver）
  回り込み除去（DuplicateSpeechFilter）
  TomoroSession（状態機械）
  ContextSnapshotBuilder（LLM に渡す文脈の組み立て）
  InferenceRouter（LLM選択・切り替え）
  utterance_candidates からの取り出し
  返答テキストを正規エッジに送信

起動設定例:
  CONFIG=config/central_realtime.toml python -m server.gateway.main
```

### 中央バックグラウンドノード

```
責務:
  thinker（候補生成、常駐）
  journalist（日記、定期）
  session_summarizer（会話セッション要約と embedding、追いかけ処理）
  persona_update（会話後）
  lexicon_update（用語集・関係性フレーズの versioned JSONB snapshot 生成）
  embedder（embedding生成）
  arrival事前計算（3分ごと）

起動設定例:
  CONFIG=config/central_background.toml python -m server.thinker.main
  CONFIG=config/central_background.toml python -m server.journalist.main
```

---

## 自発発話の欲求と発話可能性モデル

Phase 10 の初段では、45秒ごとの idle timer で候補を見に行き、
`TomoroSession` が `ambient` / `idle` / playback idle かどうかを決定的に判定している。
これは候補消費の足場としては正しいが、Tomoko らしい自発発話には「話したい欲」と
「今邪魔しそうか」を別の連続値として扱う必要がある。

重要な方針は、**状態機械に推論の余地を増やさないこと**である。
LLM は候補の意味づけや境界ケースの状況判断に使うが、
`TomoroSession` は最終 gate を決定的に実行する。

```
thinker / journalist
  -> 候補生成
  -> 話す価値、話したい理由、intrusion_risk、urgency を推論して保存

SpeakabilityScorer
  -> presence / activity / focus / rejection を load average 的に更新

InitiativePolicy
  -> desire + speakability + penalty + candidate metadata を決定的に採点
  -> 明確な speak / wait は LLM を呼ばずに決める
  -> 境界帯だけ LLM judge に構造化判断を依頼する

TomoroSession
  -> withdrawn / playback / VAD / stale result / priority policy の最終 gate
  -> SessionCommand を返すだけで、LLM 判断や DB I/O は直接実行しない
```

### TomokoDesireState

Tomoko 側の「話したい欲」を state として扱う。
これは発話候補そのものではなく、候補や状況が Tomoko の内面に与える圧力である。

```
TomokoDesireState:
  desire_1m
  desire_5m
  desire_30m
  unspoken_pressure
  curiosity_pressure
  attachment_pressure
  playful_pressure
```

OS の load average と同じように、短期・中期・長期の指数移動平均で更新する。
発話候補がある、日記由来で伝えたいことがある、言えなかったことがある、
人間の気配がある、しばらく会話がない、といった signal で上がる。
Tomoko が話した、自発発話が無反応だった、「静かにして」と言われた、
深夜や長時間無反応が続いた、といった signal で下がる。

### SpeakabilityState

`SpeakabilityState` は「今話してよい状況か」を表す。
presence が弱い、集中していそう、直近で拒否された、などを連続値で持つ。

```
SpeakabilityState:
  presence_1m
  presence_5m
  activity_1m
  activity_5m
  conversation_heat_1m
  conversation_heat_5m
  focus_likelihood_5m
  recent_rejection_score
  recent_acceptance_score
  intrusion_penalty
```

`ambient_logs` がないことは「人がいない」と断定しない。
`ambient_logs` は STT まで到達した発話のログであり、無言で PC の前にいる状態とは区別できない。
presence signal としては `presence_reports` / audio level / VAD activity / 最終発話時刻を合わせて使う。

### PersonalityDynamics

Tomoko の基本性格は返答文体だけでなく、desire の増え方・減り方・発話 threshold に効かせる。
ランダム性は毎回のサイコロではなく、ゆっくり drift する内部状態として扱う。

```
PersonalityDynamics:
  talkativeness
  restraint
  curiosity
  attachment
  sensitivity
  playfulness
  mood_talkativeness_1h
  mood_restraint_1h
  mood_curiosity_1h
```

同じ候補でも、話したがり寄りの日は desire が早く溜まり、
黙りたがり寄りの日は desire がゆっくり溜まる。
ただし `withdrawn`、VAD listening、playback 中、stale result などの hard gate は
人格変動では破れない。

### フィードバックによる重み更新

ユーザーの反応は、自発発話全体ではなく source / topic / emotional_need ごとに重みを更新する。

```
feedback examples:
  「静かにして」 -> intrusion_penalty と recent_rejection_score を上げる
  「うん、なに？」 -> recent_acceptance_score を上げる
  「それ今じゃない」 -> 該当 source / topic の penalty を上げる
  「そういうのは言って」 -> 該当 source / topic の boost を上げる
```

候補側には、少なくとも次の metadata を持たせる。

```
candidate:
  generated_text
  source
  context_tags
  priority
  urgency
  expires_at
  intrusion_risk
  emotional_need
  expected_response_type
  reason
```

### LLM judge の位置

## Turn-taking judge の位置

Phase 10.11 以降、「新しい入力で pending reply を消すか」は VAD state だけで決めない。
確定 transcript を `TurnTakingInput` DTO に包み、`TurnTakingJudge` が
`ignore_as_noise` / `continue_current_reply` / `defer_output` /
`restart_with_new_input` / `stop_speaking` の enum で返す。

この judge は rule-first とする。
空 transcript、低信号、明確な stop word、訂正、相槌、実質 follow-up は deterministic rule で判定し、
会話生成用の Gemma 4 26B queue を待たない。
曖昧な `defer_output` だけ、別プロセスの `turn-taking-worker` が小型 local MLX model で補助判定する。
worker timeout / unavailable / parse error は rule fallback に戻る。

`TomoroSession` は judge の実装詳細を知らず、判定 enum を最終制御へ反映する。
session lifecycle、reply cancellation、playback stop、stop-intent observation の最終所有者は
引き続き `TomoroSession` である。

LLM は常時の発話可否判定器にしない。
`InitiativePolicy` が明確に speak / wait を決められる場合は、オンライン LLM を呼ばない。
スコアが境界帯にある時だけ、候補文、候補理由、recent feedback、presence/activity signal、
desire level を渡して構造化判断を返させる。

```json
{
  "decision": "speak_now",
  "confidence": 0.72,
  "reason": "recent interaction was warm and candidate is short",
  "tone": "soft",
  "max_length": "short"
}
```

この返却も `TomoroSession` の state を直接変更しない。
adapter / command runner が `SessionEvent` として戻し、
`TomoroSession` が現在 state と照合して stale / not_speakable を決定的に捨てる。

### 接続状態と output target

複数ブラウザ / 複数 edge / monitor UI を扱う場合でも、`TomoroSession` は WebSocket object を所有しない。
接続管理は gateway / adapter 側の `ClientConnectionRegistry` が担当し、
`TomoroSession` には `ConnectedOutputState` という抽象 snapshot だけを渡す。

```
ClientConnectionRegistry:
  connection_id
  device_id
  role: browser / edge / monitor
  can_receive_audio
  can_receive_display
  connected_at
  last_seen_at
  playback_state_by_device

ConnectedOutputState:
  active_device_id
  audio_target_available
  display_target_available
  connected_device_count
  connected_connection_count
  last_presence_at
```

`TomoroSession` が知ってよいのは「音声を出せる target があるか」「表示 target があるか」
「今どの device が active か」までである。
WebSocket の送信先リスト、再接続処理、connection id の lifecycle は adapter に閉じ込める。

この state は自発発話の hard gate に使う。
`audio_target_available=False` の時は、candidate が存在しても initiative / arrival の発話を開始しない。
候補は background 側で作り続けてよいが、出力先がない状態で online runtime が話し始めてはいけない。

---

## 設定ベースの責務切り替え

**コアロジックは一切変わらない。設定ファイルと起動スクリプトだけで責務が変わる。**

```toml
# config/edge_kitchen.toml
[node]
role = "edge"
device_id = "kitchen"

[inference]
conversation_backend = "local_gemma_mlx"  # MLX推奨
stt_backend = "local_whisper"
tts_backend = "local_irodori"
conversation_fallback = "central_qwen"    # 詰まったら中央へ

[backends.local_gemma_mlx]
type = "mlx"
model = "mlx-community/gemma-3-2b-it-4bit"
max_latency_ms = 200  # MLXなので低めに設定できる

[backends.central_qwen]
type = "ollama"
url = "http://mainmachine:11434"
model = "qwen2.5:7b"
max_latency_ms = 800
```

```toml
# config/central_realtime.toml
[node]
role = "central_realtime"

[inference]
conversation_backend = "local_qwen_mlx"   # MLX推奨
tts_backend = "central_irodori"
conversation_fallback = "cloud_anthropic"

[backends.local_qwen_mlx]
type = "mlx"
model = "mlx-community/Qwen2.5-7B-Instruct-4bit"
max_latency_ms = 300  # Ollamaより閾値を下げられる

[backends.cloud_anthropic]
type = "anthropic"
model = "claude-haiku-4-5"
max_latency_ms = 2000
privacy_allowed = false
```

---

## InferenceRouter（コアは変わらない）

```python
class InferenceRouter:
    """
    実測値に基づいてバックエンドを選ぶ。
    コアロジックはこのクラスしか知らない。
    gemmaか、qwen7bか、Claudeかはコアの関心外。
    """
    def __init__(self, config: NodeConfig):
        self.backends = {
            name: BackendFactory.create(spec)
            for name, spec in config.backends.items()
        }
        self.routing = config.inference

    async def select(
        self,
        task_type: Literal[
            "conversation",   # リアルタイム必須
            "candidate_gen",  # バックグラウンドでいい
            "diary",          # 深夜でいい
            "session_summary", # 会話終了後に別プロセスでいい
            "embedding",      # バックグラウンドでいい
        ],
        priority: Literal["latency", "privacy", "cost"]
    ) -> InferenceBackend:

        backend_name = self.routing[task_type + "_backend"]
        backend = self.backends[backend_name]

        # 実測で閾値超えたらフォールバック
        metrics = await self.monitor.latest(backend.name)
        if metrics.latency_ms > backend.max_latency_ms:
            fallback_name = self.routing.get(task_type + "_fallback")
            if fallback_name:
                fallback = self.backends[fallback_name]
                # プライバシー非許可のバックエンドには会話内容を出さない
                if priority == "privacy" and not fallback.privacy_allowed:
                    return backend  # 詰まっても待つ
                return fallback

        return backend
```

コアはこれだけ知っている：

```python
# gateway のどこかで
backend = await router.select("conversation", "privacy")
result = await backend.generate(prompt)
# どのノードで動いているか、何のモデルかは一切知らない
```

---

## MLX バックエンド（Apple Silicon 専用）

### なぜ MLX が速いか

```
Ollama（Metal バックエンド）:
  PyTorch → Metal に変換して動かす
  変換コストがある
  メモリ帯域の使い方が最適ではない

MLX:
  Apple Silicon のユニファイドメモリ専用設計
  CPU / GPU / Neural Engine が同じメモリを共有
  変換コストなし、行列演算が Apple Silicon に最適化済み
```

実測ベースの速度差（目安）：

| モデル | Ollama | MLX | 差 |
|---|---|---|---|
| Qwen2.5 7B | 15〜25 tok/s | 40〜60 tok/s | 2〜3倍 |
| Gemma3 2B | 30〜50 tok/s | 80〜120 tok/s | 2〜3倍 |
| 14B 量子化 | 8〜12 tok/s | 25〜40 tok/s | 3倍前後 |

### デバイス別の推奨モデル

```
M4 Max（中央リアルタイムノード）:
  mlx-community/Qwen2.5-14B-Instruct-4bit
  大きいモデルが実用速度になる

M1 Pro / M2（中央または中規模エッジ）:
  mlx-community/Qwen2.5-7B-Instruct-4bit

M2 iPad Air / 小型エッジ:
  mlx-community/gemma-3-2b-it-4bit
  TTS + 軽量LLM をエッジで完結できる
```

モデルは Hugging Face の `mlx-community` にほぼ全主要モデルの量子化済みが揃っている。

### MLXBackend の実装

```python
# server/shared/inference/backends/mlx.py

class MLXBackend(InferenceBackend):
    """
    mlx-lm を直接叩くバックエンド。
    Ollama を介さないので起動が速い。
    Apple Silicon 専用。
    """
    privacy_allowed: bool = True  # ローカル推論なので常に True

    def __init__(self, model_path: str):
        from mlx_lm import load
        self.model, self.tokenizer = load(model_path)

    async def generate(
        self, prompt: str
    ) -> AsyncGenerator[str, None]:
        from mlx_lm import stream_generate
        for token in stream_generate(
            self.model,
            self.tokenizer,
            prompt=prompt,
            max_tokens=512,
        ):
            yield token

    async def ping(self) -> float:
        start = time.perf_counter()
        # 軽いプローブ（1トークンだけ生成）
        async for _ in self.generate("ping"):
            break
        return (time.perf_counter() - start) * 1000

    async def queue_depth(self) -> int:
        return 0  # インプロセスなのでキューなし
```

### OllamaBackend との使い分け

| | MLXBackend | OllamaBackend |
|---|---|---|
| 速度 | 2〜3倍速い | 普通 |
| 対応環境 | Apple Silicon のみ | クロスプラットフォーム |
| モデル管理 | 自分で管理 | `ollama pull` だけ |
| プロセス | インプロセス | 別プロセス HTTP |
| メモリ | 直接消費 | Ollama プロセスが管理 |
| 起動時間 | モデルロードに数秒 | Ollama が常駐していれば即時 |

**M1以降の Apple Silicon では MLX を基本とし、
非 Apple 環境へのフォールバックとして Ollama を残す**のが推奨。

### BackendFactory での切り替え

```python
# server/shared/inference/factory.py

class BackendFactory:
    @staticmethod
    def create(spec: BackendSpec) -> InferenceBackend:
        match spec.type:
            case "mlx":
                return MLXBackend(model_path=spec.model)
            case "ollama":
                return OllamaBackend(url=spec.url, model=spec.model)
            case "anthropic":
                return AnthropicBackend(model=spec.model)
            case _:
                raise ValueError(f"Unknown backend type: {spec.type}")
```

設定ファイルの `type = "mlx"` を `type = "ollama"` に変えるだけで切り替わる。

### M1完了後の移行手順

```bash
# 1. mlx-lm をインストール
pip install mlx-lm

# 2. モデルをダウンロード
python -c "from mlx_lm import load; load('mlx-community/Qwen2.5-7B-Instruct-4bit')"

# 3. 設定を切り替える
# config/central_realtime.toml の conversation_backend を
# "local_qwen7b"（ollama）→ "local_qwen_mlx"（mlx）に変更

# 4. テストで比較
pytest -m perf --tb=short  # latency.md に実測値が追記される
```

**InferenceRouter があるので A/B テストが設定ファイルの切り替えだけでできる。**
`_docs/latency.md` に Ollama と MLX の実測値を並べて判断する。

---

## TTS バックエンド

TTS も `TTSBackend` 抽象を介して差し替え可能にする。
**M1フェーズは `say`、完了後に `kokoro-mlx` に切り替える。**
ダメなら VOICEVOX に切り替え可能。抽象があるのでコアは変わらない。

### TTSBackend 抽象

```python
# server/shared/inference/tts/base.py

class TTSBackend(ABC):
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        style: str = "neutral",
    ) -> AsyncGenerator[bytes, None]: ...
```

### SayBackend（M1フェーズ採用）

```python
# server/shared/inference/tts/say.py

class SayBackend(TTSBackend):
    """
    macOS say コマンドを使うバックエンド。
    Apple の Neural Engine に直結、初回チャンクまで 10ms 以下。
    CPU 負荷ほぼゼロ。M1 フェーズで最速を実現するための選択。
    感情スタイルは rate で簡易表現。
    """
    STYLE_TO_RATE = {
        "neutral":   175,
        "happy":     190,
        "excited":   200,
        "sad":       155,
        "thinking":  165,
        "gentle":    160,
    }

    def __init__(self, voice: str = "Kyoko"):
        self.voice = voice  # Kyoko（女性）/ Otoya（男性）

    async def synthesize(
        self, text: str, style: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        rate = self.STYLE_TO_RATE.get(style, 175)
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", self.voice,
            "-r", str(rate),
            "-o", "/tmp/tomoko_say.aiff",
            text,
        )
        await proc.wait()
        with open("/tmp/tomoko_say.aiff", "rb") as f:
            yield f.read()
```

| 特性 | 内容 |
|---|---|
| 初回チャンクまで | 10ms 以下 |
| CPU 負荷 | ほぼゼロ |
| 感情表現 | rate のみ（簡易） |
| 対応環境 | macOS 専用 |
| キャラクター感 | なし（Kyoko は Kyoko） |

### KokoroMLXBackend（本採用予定）

```python
# server/shared/inference/tts/kokoro_mlx.py

class KokoroMLXBackend(TTSBackend):
    """
    kokoro-mlx を使う TTS バックエンド。
    Apple Silicon 専用。Gapless streaming 対応。
    PyTorch / transformers 依存なし。
    日本語は misaki[ja] が必要。
    """
    STYLE_TO_VOICE = {
        "neutral":   "jf_alpha",
        "happy":     "jf_alpha",
        "excited":   "jf_alpha",
        "sad":       "jf_beta",
        "thinking":  "jf_beta",
        "gentle":    "jf_beta",
    }

    def __init__(self, voice: str = "jf_alpha"):
        from kokoro_mlx import KokoroMLX
        self.model = KokoroMLX()
        self.default_voice = voice

    async def synthesize(
        self, text: str, style: str = "neutral"
    ) -> AsyncGenerator[bytes, None]:
        voice = self.STYLE_TO_VOICE.get(style, self.default_voice)
        loop = asyncio.get_event_loop()
        for chunk in await loop.run_in_executor(
            None,
            lambda: self.model.stream(text, voice=voice)
        ):
            yield chunk
```

| 特性 | 内容 |
|---|---|
| 初回チャンクまで | 10〜30ms（MLX） |
| CPU 負荷 | 低い（Neural Engine） |
| 感情表現 | ボイス切り替えで表現 |
| 対応環境 | Apple Silicon 専用 |
| ライセンス | Apache 2.0 |
| 日本語 | `pip install misaki[ja]` が必要 |

### 移行手順（M1完了後）

```bash
# 1. インストール
pip install kokoro-mlx misaki[ja]

# 2. 設定を切り替えるだけ
# config/central_realtime.toml の tts_backend を
# "say" → "kokoro_mlx" に変更

# 3. 日本語品質を確認
pytest -m perf --tb=short
# _docs/latency.md に say vs kokoro-mlx の実測値が並ぶ

# 4. 品質が厳しければ VOICEVOX に切り替え（同じ抽象なので差し替え可能）
```

### 設定ファイルでの切り替え

```toml
# config/central_realtime.toml

# M1フェーズ（say で動かす）
[inference]
tts_backend = "say"

[backends.say]
type = "say"
voice = "Kyoko"

# M1完了後（kokoro-mlx に切り替え）
[inference]
tts_backend = "kokoro_mlx"

[backends.kokoro_mlx]
type = "kokoro_mlx"
voice = "jf_alpha"
max_latency_ms = 50
```

### TTS 選択の判断フロー

```
M1フェーズ:
  say → 最速、まず動かす

M1完了後:
  kokoro-mlx → 日本語品質を確認
    OK → そのまま採用
    NG → VOICEVOX に切り替え（同じ TTSBackend 抽象）
```

**「ダメなら切り替え可能」が TTSBackend 抽象の存在意義。**

---

## 層間 DTO と オーバーヘッドの設計方針

### 基本原則

各層の境界は `dataclass` で包む。ただし**ホットループ内はプリミティブのまま**。

```
VAD ホットループ（32ms ごと）: プリミティブ → プリミティブ
発話終了イベント境界:          np.ndarray → SpeechSegment（DTO）
STT 完了境界:                  SpeechSegment → Transcript（DTO）
参加判断境界:                  Transcript → ParticipationDecision（DTO）
LLM トークン境界:              str → ThinkingEvent（DTO、slots=True）
TTS 入力境界:                  ThinkingEvent → TTSInput（DTO）
WebSocket 出力境界:            bytes → AudioChunkOut（DTO）
```

### DTO 定義（server/shared/models.py に集約）

```python
# server/shared/models.py

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
import numpy as np


# VAD → STT（発話終了時のみ生成。数秒に1回）
@dataclass
class SpeechSegment:
    audio: np.ndarray      # float32, 16kHz
    started_at: datetime
    ended_at: datetime
    device_id: str
    vad_confidence: float


# STT → ParticipationJudge / ambient_logs
@dataclass
class Transcript:
    text: str
    device_id: str
    speaker: str | None
    audio_level_db: float
    recorded_at: datetime
    is_final: bool


# ParticipationJudge → session
@dataclass
class ParticipationDecision:
    should_participate: bool
    mode: Literal["called", "invited", "observer", "withdraw"]
    reason: str


# session → ThinkingMode
@dataclass
class ThinkingInput:
    text: str
    speaker: str | None
    context: list[ConversationTurn]
    emotion: str
    device_id: str


# ThinkingMode → session（トークンごとに生成。slots=True で軽量化）
@dataclass(slots=True)
class ThinkingEvent:
    type: Literal["emotion", "text_delta", "done"]
    value: str


# session → TTSBackend
@dataclass
class TTSInput:
    text: str
    style: str
    voice: str | None = None


# TTSBackend → WebSocket
@dataclass(slots=True)
class AudioChunkOut:
    data: bytes
    sequence: int     # 順序保証
    is_last: bool


# 会話ターン（コンテキストとして ThinkingInput に含まれる）
@dataclass
class ConversationTurn:
    speaker: Literal["user", "tomoko"]
    text: str
    timestamp: datetime
    emotion: str | None = None
```

### なぜ VAD ホットループはプリミティブのままか

```python
# NG: 32ms ごとに DTO を生成する
while True:
    chunk = mic.read(512)
    audio_chunk = AudioChunk(          # 31回/秒 生成
        data=chunk,
        sample_rate=16000,
        device_id=self.device_id,
        timestamp=datetime.now(),      # さらに datetime.now() も毎回
    )
    vad.process(audio_chunk)

# OK: VAD 内部はプリミティブで処理、発話終了時だけ DTO に包む
class SileroVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        """プリミティブin、プリミティブout。DTOなし。"""
        return self.model(torch.from_numpy(chunk), 16000).item()

class VADProcessor:
    def on_speech_end(self, buffer: list[np.ndarray]) -> SpeechSegment:
        """発話終了時のみ DTO を生成（数秒に1回）。"""
        return SpeechSegment(
            audio=np.concatenate(buffer),
            started_at=self.speech_started_at,
            ended_at=datetime.now(),   # ここで1回だけ呼ぶ
            device_id=self.device_id,
            vad_confidence=self.last_prob,
        )
```

### slots=True のメリット

```python
import sys

@dataclass
class Normal:
    type: str
    value: str

@dataclass(slots=True)
class Slotted:
    type: str
    value: str

sys.getsizeof(Normal("text_delta", "うん"))   # 56 bytes
sys.getsizeof(Slotted("text_delta", "うん"))  # 48 bytes
# 生成速度も slots=True の方が速い
```

トークンレベルで大量生成される `ThinkingEvent` と `AudioChunkOut` は
`slots=True` を使う。それ以外は通常の `dataclass` で十分。

### ルールまとめ

```
Rule 1: VAD ホットループ（32ms ごと）内は
        プリミティブのまま処理する。DTO を作らない。

Rule 2: 発話終了 / STT完了 / 参加判断 / LLM完了 などの
        「イベント境界」でだけ DTO に包む。

Rule 3: トークン単位で大量生成される DTO は
        slots=True を使う（ThinkingEvent, AudioChunkOut）。

Rule 4: datetime.now() はホットループ内で呼ばない。
        タイムスタンプが必要なら境界でだけ取る。

Rule 5: 層をまたぐ時は必ず DTO を経由する。
        str / bytes / np.ndarray をそのまま層間で渡してはいけない。
        （VAD 内部のホットパスは例外）
```

---

```sql
CREATE TABLE presence (
    speaker      TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence   FLOAT NOT NULL,   -- 音量・話者識別確信度から算出
    PRIMARY KEY (speaker, device_id)
);

CREATE TABLE edge_status (
    device_id    TEXT PRIMARY KEY,
    last_seen    TIMESTAMPTZ NOT NULL,
    is_active    BOOLEAN DEFAULT TRUE,
    tts_ready    BOOLEAN DEFAULT FALSE,
    llm_backend  TEXT   -- このエッジが持つLLMの種類（なければNULL）
);
```

### DirectSpeakerResolver（正規発話元の判定）

同じ発話が複数エッジから報告された時、音量最大のエッジを正規とする：

```python
class DirectSpeakerResolver:
    async def resolve(
        self,
        reports: list[PresenceReport]
    ) -> PresenceReport:
        # 同時刻に複数エッジから来た場合、音量最大が正規
        return max(reports, key=lambda r: r.audio_level_db)
```

### DuplicateSpeechFilter（回り込み除去）

```python
DEDUP_WINDOW_MS = 500

class DuplicateSpeechFilter:
    async def is_duplicate(
        self,
        transcript: str,
        device_id: str,
        timestamp: datetime,
    ) -> bool:
        recent = await db.fetch_recent_ambient_logs(
            within_ms=DEDUP_WINDOW_MS,
            exclude_device=device_id,
        )
        for log in recent:
            if text_similarity(transcript, log.transcript) > 0.8:
                return True  # 回り込みと判定
        return False
```

キッチンの音声がリビングに回り込んでも、音量差と時刻差で弾かれる。

### PresenceManager（人間の部屋移動）

```python
class PresenceManager:
    async def get_primary_location(self, speaker: str) -> str | None:
        """話者が今どの部屋にいるか"""
        recent = await db.fetch_recent_presence(
            speaker=speaker,
            within_seconds=30,
        )
        if not recent:
            return None
        return max(recent, key=lambda p: p.confidence).device_id

    async def has_moved(self, speaker: str) -> tuple[bool, str | None]:
        current = await self.get_primary_location(speaker)
        previous = await db.fetch_previous_location(speaker)
        return (current != previous, previous)
```

Tomoroはこれを使って：

```
「あ、リビングに移動してきたんだね」
「キッチンにいた時の話だけど」
```

が自然に言えるようになる。
返答テキストも常に正規エッジ（今話者がいる部屋）のTTSに送られる。

---

## 実測ベースのフォールバック

### inference_metrics テーブル

```sql
CREATE TABLE inference_metrics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    backend     TEXT NOT NULL,
    latency_ms  FLOAT NOT NULL,
    tps         FLOAT,
    queue_depth INTEGER
);
```

### BackendHealthMonitor

```python
class BackendHealthMonitor:
    async def measure(self, backend: InferenceBackend) -> InferenceMetrics:
        start = time.perf_counter()
        await backend.ping()
        latency = (time.perf_counter() - start) * 1000
        metrics = InferenceMetrics(
            backend=backend.name,
            latency_ms=latency,
            queue_depth=await backend.queue_depth(),
        )
        await db.insert_inference_metrics(metrics)
        return metrics
```

実測値が蓄積されることで：

- 過去5分の中央値が閾値超え → クラウドに切り替え
- テストで「この構成は成立するか」を再現可能な形で検証できる
- `_docs/latency.md` への自動追記でトレンドが見える

---

## 常時STT と ParticipationJudge

### なぜ常時STTか

ローカル推論なのでプライバシー問題なし。全発話をDBに溜めることで
Tomoko の「聞いていた記憶」が生まれる。

```
float32 chunks（常時）
  → Silero VAD
  → faster-whisper → ambient_logs に全部書く
  → DuplicateSpeechFilter（回り込み除去）
  → ParticipationJudge.judge()
       ↓ should_participate=True の時だけ
  → 中央リアルタイムノードへテキスト送信
  → LLM → 返答テキスト → 正規エッジのTTS → スピーカー
```

### ambient_logs テーブル

```sql
CREATE TABLE ambient_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    device_id           TEXT NOT NULL,
    speaker             TEXT,
    transcript          TEXT NOT NULL,
    tomoko_participated BOOLEAN DEFAULT FALSE
);
```

### ParticipationJudge（差し替え可能）

```python
class ParticipationJudge(ABC):
    @abstractmethod
    async def judge(self, ctx: ParticipationContext) -> ParticipationDecision: ...

# 最初はこれだけ
class WakeWordJudge(ParticipationJudge):
    WAKE_WORDS = ["トモコ", "ともこ", "Tomoko"]
    async def judge(self, ctx):
        called = any(w in ctx.transcript for w in self.WAKE_WORDS)
        return ParticipationDecision(
            should_participate=called,
            mode="called" if called else "observer",
        )

# 後から差し替え
class LLMJudge(ParticipationJudge): ...
class HybridJudge(ParticipationJudge): ...
```

---

## utterance_candidates テーブル（中央プール）

```sql
CREATE TABLE utterance_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seed            TEXT NOT NULL,
    generated_text  TEXT,
    generated_audio BYTEA,
    priority        FLOAT NOT NULL DEFAULT 0.5,
    urgent          BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    spoken_at       TIMESTAMPTZ,
    dismissed_at    TIMESTAMPTZ,
    maturity        INTEGER DEFAULT 0,
    source          TEXT NOT NULL,
    context_tags    TEXT[] DEFAULT '{}'
);
```

maturity:
- 0: seed のみ（LLM + TTS 必要、500ms〜）
- 1: テキスト生成済み（TTS のみ、200ms〜）
- 2: 音声まで生成済み（即発話、10ms〜）

spoken_at / dismissed_at:
- spoken_at IS NOT NULL → 話せた
- dismissed_at IS NOT NULL → 話したかったけど期限切れ（日記に使う）

### SelectionStrategy（取り出し戦略）

プールは中央一発。取り出し方だけ差し替えられる：

```python
class SelectionStrategy(ABC):
    @abstractmethod
    def select(
        self,
        candidates: list[UtteranceCandidate],
        context: SessionContext,
    ) -> UtteranceCandidate | None: ...

class HighestPriority(SelectionStrategy): ...
class MostRelevantToTopic(SelectionStrategy): ...
class LightweightFiller(SelectionStrategy): ...
```

---

## arrival_candidates テーブル（入室用プール）

```sql
CREATE TABLE arrival_candidates (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until      TIMESTAMPTZ NOT NULL,
    context_snapshot JSONB NOT NULL,
    behavior         TEXT NOT NULL,
    -- "speak_first" / "wait_silent" / "subtle_react"
    utterance_text   TEXT,
    utterance_audio  BYTEA,
    used_at          TIMESTAMPTZ
);
```

thinker が3分ごとに作り直す使い捨て前提。
context_snapshot に時刻・感情・urgent候補・device_id を含める。

事前計算プロンプト：

```
あなたは今から数分以内に話しかけられる可能性があります。
その時に最初に発する一言を今考えておいてください。

現在の状況:
- 時刻: {hour}時{minute}分
- 前回の会話からの経過: {time_since_last}
- 今のあなたの感情: {current_emotion}
- 言いたかったけど言えていないこと: {urgent_seeds}
- 今日の会話回数: {session_count_today}回
- 部屋: {device_id}

一言だけ答えてください。自然な日本語で。
挨拶でも、独り言でも、沈黙（空文字）でも構いません。
```

---

## journalist の役割

```python
async def write_diary(date: date):
    logs       = await db.fetch_conversation_logs(date)
    ambient    = await db.fetch_ambient_logs(date)  # 聞いていた会話も
    candidates = await db.fetch_candidates_for_date(date)  # dismissed も含む
    emotions   = await db.fetch_emotion_log(date)
    persona    = await db.fetch_current_persona()
```

日記が記憶になり、記憶が発話を生む：

```
journalist → diary テーブルに書く
  ↓
DiarySource が diary を読む
  ↓
utterance_candidates に「続きを話したい」候補が積まれる
  ↓
Tomoko が自発的に話し始める
```

---

## テスト構造

### 3層に分類

```python
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "unit: 外部依存なし、常に実行",
    "integration: 実際のミドルウェアが必要",
    "perf: レイテンシー計測、手元でのみ",
]
```

### Layer 1: ユニットテスト

```python
# tests/unit/test_router.py

async def test_router_chooses_cloud_when_local_slow():
    router = InferenceRouter(
        config=load_config("config/central_realtime.toml"),
        monitor=MockMonitor({"local_qwen7b": InferenceMetrics(latency_ms=600)})
    )
    backend = await router.select("conversation", "latency")
    assert backend.name == "cloud_anthropic"

async def test_privacy_task_never_leaves_local():
    router = InferenceRouter(
        config=load_config("config/central_realtime.toml"),
        monitor=MockMonitor({"local_qwen7b": InferenceMetrics(latency_ms=600)})
    )
    backend = await router.select("conversation", "privacy")
    assert backend.privacy_allowed == True

async def test_edge_config_uses_local_gemma():
    config = load_config("config/edge_kitchen.toml")
    router = InferenceRouter(config, monitor=MockMonitor())
    backend = await router.select("conversation", "latency")
    assert backend.model == "gemma3:2b"

async def test_duplicate_speech_detected():
    filter = DuplicateSpeechFilter(db=MockDB(
        recent_logs=[AmbientLog(transcript="今日いい天気だね", device_id="living")]
    ))
    is_dup = await filter.is_duplicate(
        transcript="今日いい天気だね",
        device_id="kitchen",  # 別エッジから同じ発話
        timestamp=datetime.now(),
    )
    assert is_dup == True

async def test_direct_speaker_resolver_picks_loudest():
    resolver = DirectSpeakerResolver()
    reports = [
        PresenceReport(device_id="kitchen", audio_level_db=-20),  # 小さい
        PresenceReport(device_id="living",  audio_level_db=-10),  # 大きい
    ]
    primary = await resolver.resolve(reports)
    assert primary.device_id == "living"
```

### Layer 2: 統合テスト

```python
# tests/integration/test_backends.py

@pytest.mark.integration
async def test_local_backend_latency():
    backend = OllamaBackend(url="http://localhost:11434")
    metrics = await HealthMonitor().measure(backend)
    assert metrics.latency_ms < 1000

@pytest.mark.integration
async def test_fallback_to_cloud_when_local_dead():
    router = InferenceRouter(
        config=load_config("config/central_realtime.toml"),
        local=DeadBackend(),
        cloud=MockCloudBackend(),
    )
    backend = await router.select("candidate_gen", "latency")
    assert backend.name == "cloud_anthropic"
```

### Layer 3: パフォーマンステスト

```python
# tests/perf/test_latency.py

@pytest.mark.perf
async def test_e2e_conversation_latency():
    """VAD終了 → 最初の音声チャンク まで 800ms 以内"""
    session = TomoroSession()
    audio = load_test_audio("test_utterance_ja.wav")
    start = time.perf_counter()
    first_chunk = asyncio.Event()

    async def on_chunk(chunk: bytes):
        if not first_chunk.is_set():
            first_chunk.set()
            elapsed = (time.perf_counter() - start) * 1000
            assert elapsed < 800, f"E2E latency: {elapsed}ms"

    session.on_audio_chunk = on_chunk
    await session.process_audio(audio)
    await first_chunk.wait()

@pytest.mark.perf
async def test_arrival_candidate_freshness():
    candidate = await db.fetch_latest_fresh_arrival_candidate()
    assert candidate is not None
    age = datetime.now() - candidate.computed_at
    assert age.total_seconds() < 300
```

実行：

```bash
pytest -m unit                        # CI で常時
pytest -m "unit or integration"       # 手元フル検証
pytest -m perf --tb=short            # レイテンシー計測
```

---

## イベントプロトコル（エッジ ↔ 中央）

### エッジ → 中央（テキストのみ、音声は外に出ない）

```jsonc
// 発話報告
{
  "type": "speech",
  "device_id": "kitchen",
  "speaker": "お父さん",
  "transcript": "トモコ、今日の夕飯何がいい？",
  "audio_level_db": -15.2,
  "timestamp": "2026-05-23T19:30:00Z"
}

// 存在報告
{
  "type": "presence",
  "device_id": "kitchen",
  "speaker": "お父さん",
  "confidence": 0.92
}
```

### 中央 → エッジ（返答テキスト）

```jsonc
// 返答（正規エッジだけに送る）
{
  "type": "reply",
  "text": "カレーはどう？昨日の話の続きだけど",
  "emotion": "gentle",
  "target_device": "kitchen"
}
```

### ブラウザクライアント ↔ エッジ（既存）

```jsonc
{"type": "state", "state": "listening"}
{"type": "emotion", "value": "happy"}
{"type": "reply_text", "delta": "カレーは"}
// バイナリ: 音声チャンク
```

---

## ディレクトリ構成

```
tomoko-voice/
├── server/
│   ├── edge/
│   │   ├── main.py              エッジエントリ
│   │   ├── pipeline/
│   │   │   ├── vad.py           Silero VAD
│   │   │   ├── stt.py           faster-whisper（常時STT）
│   │   │   └── tts.py           TTSBackend（say→kokoro-mlx と差し替え）
│   │   ├── participation/
│   │   │   ├── base.py          ParticipationJudge 抽象
│   │   │   ├── wake_word.py     WakeWordJudge
│   │   │   ├── llm_judge.py     LLMJudge（後から）
│   │   │   └── hybrid.py        HybridJudge（後から）
│   │   └── speaker/
│   │       └── identifier.py    話者識別（pyannote）
│   │
│   ├── gateway/
│   │   ├── main.py              中央リアルタイムエントリ
│   │   ├── session.py           TomoroSession（状態機械）
│   │   ├── resolver.py          DirectSpeakerResolver
│   │   ├── dedup.py             DuplicateSpeechFilter
│   │   ├── presence.py          PresenceManager
│   │   └── thinking/
│   │       ├── base.py          ThinkingMode 抽象
│   │       ├── fast.py          即応モード
│   │       └── deep.py          記憶検索モード
│   │
│   ├── thinker/
│   │   ├── main.py              バックグラウンドエントリ
│   │   ├── arrival.py           入室用事前計算（3分ごと）
│   │   ├── sources/
│   │   │   ├── base.py          InformationSource 抽象
│   │   │   ├── memory.py        記憶連想
│   │   │   ├── time_based.py    時刻ベース
│   │   │   └── diary.py         日記からの引き継ぎ
│   │   ├── evaluator/
│   │   │   ├── base.py          UtteranceEvaluator 抽象
│   │   │   └── llm.py           LLM判定
│   │   ├── selection/
│   │   │   ├── base.py          SelectionStrategy 抽象
│   │   │   ├── highest.py       HighestPriority
│   │   │   ├── relevant.py      MostRelevantToTopic
│   │   │   └── filler.py        LightweightFiller
│   │   └── pregenerator.py      事前生成
│   │
│   ├── journalist/
│   │   └── main.py              日記生成（定期実行）
│   │
│   └── shared/
│       ├── db.py                PostgreSQL アクセス
│       ├── candidate.py         UtteranceCandidate 型
│       ├── models.py            共通データモデル
│       ├── inference/
│       │   ├── router.py        InferenceRouter
│       │   ├── monitor.py       BackendHealthMonitor
│       │   ├── backends/
│       │   │   ├── base.py      InferenceBackend 抽象
│       │   │   ├── mlx.py       MLXBackend（LLM、Apple Silicon専用）
│       │   │   ├── ollama.py    OllamaBackend（LLM）
│       │   │   └── anthropic.py AnthropicBackend（LLM）
│       │   ├── tts/
│       │   │   ├── base.py      TTSBackend 抽象
│       │   │   ├── say.py       SayBackend（macOS say、M1フェーズ）
│       │   │   └── kokoro_mlx.py KokoroMLXBackend（本採用）
│       │   └── factory.py       BackendFactory
│       └── config.py            NodeConfig（TOMLから読む）
│
├── config/
│   ├── edge_kitchen.toml        キッチンエッジ設定
│   ├── edge_living.toml         リビングエッジ設定
│   ├── central_realtime.toml   中央リアルタイム設定
│   └── central_background.toml 中央バックグラウンド設定
│
├── client/
│   ├── index.html
│   ├── main.js
│   └── audio-worklet.js
│
├── assets/images/
├── prompts/
│   ├── base_persona.md
│   └── persona_history/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── perf/
├── _docs/
│   └── latency.md              計測ログ
├── docker/
│   ├── docker-compose.yml
│   └── postgres/
└── pyproject.toml
```

---

## 非機能要件

### レイテンシー目標

ユーザーが話し終わってから最初の音が出るまで: **800ms 以内**

| 区間 | 目標 |
|---|---|
| VAD 発話終了検知 | 300〜400ms |
| STT | 100〜200ms |
| エッジ→中央テキスト送信 | 10ms（LAN内） |
| LLM 最初のトークン | 100〜200ms |
| 中央→エッジテキスト送信 | 10ms（LAN内） |
| TTS 最初のチャンク | 100〜200ms |

maturity=2 の候補を使う自発発話: **10ms 以内**

### プライバシー原則

```
音声データ:     エッジの外に出ない（絶対）
テキスト:       ローカルネットワーク内のみ（原則）
クラウド送信:   プライバシー非依存タスクのみ（候補生成seed等）
会話内容:       クラウドに出さない（privacy_allowed=false で保証）
```

### 永続性

全ての会話ログ・候補・日記・presence は PostgreSQL に蓄積。
明示的な削除はしない。全ては記録として残る。

---

## 2026-05-23 追記: AttentionMode と「聞いていた/聞いてなかった」の分離

### 背景

M1 の現状は、常時 STT で全発話を `ambient_logs` に保存し、`WakeWordJudge` が
「トモコ」を含む発話だけに反応する。

このまま M2 の短期記憶へ進むと、次の境界が曖昧になる:

- どの発話を会話文脈として扱うか
- どの発話を記憶に入れるか
- wake word 後の続き発話をどこまで Tomoko 宛てとみなすか
- wake word 外で自然に入る時、それが乱入か参加か
- 「あ、聞いてなかった」をどう表現するか

そのため、`TomoroSession` に音声処理の `state` とは別の `attention_mode` を持たせる。
これは 3年前の Unity 実装で `isRecording` / `isCommunicating` / `isAITalking` が分散した反省を踏まえ、
会話参加の状態を一箇所に集約するための設計である。

### 概念モデル

音声処理としては常時聞いている。ただし、人格として会話に注意を向けていたかは別に扱う。

```text
recorded
  音声として聞こえ、STT され、ambient_logs に保存された

attended
  Tomoko が会話として注意を向けていた

remembered
  後から会話記憶として使ってよい
```

「あ、聞いてなかった」は、マイクや STT が失敗したという意味ではない。
`recorded=true` でも `attended=false` の発話はあり得る。
この時、DB には残っているが、Tomoko の主観では会話として受け取っていない。

### AttentionMode

`TomoroSession` は次の `attention_mode` を持つ。

```python
AttentionMode = Literal[
    "ambient",   # 聞こえているが参加していない
    "engaged",   # 呼ばれた/話しかけられたので会話中
    "cooldown",  # 会話終了直後の短い猶予
    "withdrawn", # 今は入らない
]
```

状態の意味:

| mode | 意味 | 参加判断 |
|---|---|---|
| `ambient` | 常時 STT はするが、基本は返答しない | wake word または強い呼びかけで参加 |
| `engaged` | wake word 後の会話中 | 続き発話なら wake word なしで返答 |
| `cooldown` | 会話が終わりそうな猶予 | 関連発話なら `engaged` に戻る |
| `withdrawn` | 明示的に引いている | 原則返答しない |

### 遷移

```text
ambient
  wake word / 強い呼びかけ
    -> engaged

engaged
  継続発話
    -> engaged
  Tomoko の返答完了後、一定時間無発話
    -> cooldown
  「静かにして」「今は入らないで」
    -> withdrawn

cooldown
  関連発話
    -> engaged
  一定時間無発話
    -> ambient

withdrawn
  明示的な呼び戻し
    -> engaged
  一定時間経過
    -> ambient
```

状態遷移は必ず `TomoroSession` 内で行い、遷移時は `log.info` に残す。
クライアントは `attention_mode` を判定しない。表示が必要な場合も WebSocket の JSON イベントを描画するだけにする。

### ParticipationDecision との関係

`ParticipationDecision.mode` は既存の `called` / `invited` / `observer` / `withdraw` を使う。
`attention_mode` は参加判断の前提、`ParticipationDecision` はその発話に対する判断結果である。

```text
ambient + wake word
  -> ParticipationDecision(mode="called", should_participate=True)
  -> attention_mode = engaged

engaged + 続き発話
  -> ParticipationDecision(mode="invited", should_participate=True)
  -> attention_mode = engaged

ambient + 無関係な発話
  -> ParticipationDecision(mode="observer", should_participate=False)
  -> attention_mode = ambient

withdrawn + 関連発話
  -> ParticipationDecision(mode="withdraw", should_participate=False)
  -> attention_mode = withdrawn
```

将来の `LLMJudge` / `HybridJudge` は、発話テキストだけでなく `attention_mode` を入力に含める。
これにより、wake word 外で会話に入る時も「返せそうだから返す」ではなく、
今入ってよい状態かどうかを前提に判断できる。

### ログと記憶の境界

`ambient_logs` は聞こえた発話を記録する場所なので、`attention_mode` にかかわらず保存する。
ただし、次のメタ情報を持たせる。

```sql
attention_mode     TEXT NOT NULL
attended           BOOLEAN NOT NULL DEFAULT FALSE
participation_mode TEXT NOT NULL
```

`conversation_logs` は `attended=true` の会話ターンだけを保存する。
M2 の短期記憶と M3 以降の日記・自発発話は、この境界を前提にする。

```text
ambient_logs:
  聞こえていたことの記録。Tomoko の主観として会話参加していないものも含む。

conversation_logs:
  Tomoko が注意を向け、会話として参加したターンの記録。
```

### 「聞いてなかった」の扱い

`attended=false` の発話は、直近会話文脈には入れない。
後からその話題を振られた時、Tomoko は必要なら次のように振る舞える。

```text
「ごめん、その時はちゃんと聞いてなかった」
```

これは嘘ではなく、人格として注意を向けていなかったという意味である。
常時 STT と人格上の注意を分けることで、聞き耳を立て続ける不自然さを避ける。

---

## 2026-05-23 追記: TomoroSession の責務境界

Phase 6.6.4 以降、`TomoroSession` は会話状態機械のオーケストレーターとして扱う。
すべてを `TomoroSession` に直接実装し続けると、状態遷移、TTS、playback telemetry、barge-in が
同じメソッド群に混ざり、3年前の Unity 実装と同じように見通しが悪くなるためである。

ただし、「状態機械を分散させない」という原則は維持する。
分離するのは副責務の機械的な詳細であり、会話参加や attention の authoritative state は
引き続き `TomoroSession` が所有する。

### 所有ルール

| コンポーネント | 所有するもの | 所有しないもの |
|---|---|---|
| `TomoroSession` | `state` / `attention_mode` / 参加判断の流れ / WebSocket 送信順序 | audio turn の細部、句読点 flush の細部 |
| `AudioTurnController` | `turn_id` / audio sequence / playback active chunk / echo grace | participation 判定、attention 遷移、WebSocket I/O |
| `ReplyAudioPipeline` | `ThinkingEvent` から reply text / emotion / TTS flush command への変換 | TTS 実行、WebSocket I/O、attention / participation 判定 |

### 重要な制約

- WebSocket エンドポイントは増やさない。すべて既存 `/ws` の event / binary chunk として流す。
- クライアントに判断ロジックを移さない。クライアントは playback telemetry という事実だけを返す。
- `AudioTurnController` は送るべき event / chunk metadata を返すだけで、`send_event` / `send_audio` を呼ばない。
- `ReplyAudioPipeline` は変換だけを行い、`TTSBackend` を直接呼ばない。
- `TomoroSession` の public entrypoint は当面 `process_audio_chunk` と `handle_playback_telemetry` に限定する。

### 2026-05-23 追記: reply 配下の分割

上の `ReplyAudioPipeline` という名前と境界は、emotion / image を扱い始めると音声寄りに見えすぎるため、
`session -> reply -> audio/emotion/image` の依存方向へ整理する。

```text
TomoroSession
  -> ReplyPipeline
       -> ReplyAudioPlanner
       -> ReplyEmotionState
       -> EmotionImageMapper
```

`TomoroSession` は `ReplyPipeline` だけを知る。
`ReplyPipeline` は `ThinkingEvent` から WebSocket 表示 command と TTS flush command を作る。
audio / emotion / image の個別ルールは `server/gateway/reply/` 配下の小さな helper に閉じ込める。

### 2026-05-25 追記: AudioTurnController は純粋な制御対象

Phase 6.6.4 で互換のために残した `TomoroSession` の audio turn thin delegate は否定する。
`TomoroSession` は、いつ話し始めるか、いつ止めるか、barge-in / interrupt をどう扱うか、
WebSocket event / audio をどの順序で送るかを決める。

`AudioTurnController` は、`turn_id` 発行、`audio_start` / `audio_end` / `audio_control stop` の
idempotent reservation、audio chunk sequence 採番、playback telemetry 由来の playback state /
echo grace、`recent_tomoko_text` / speaking elapsed の read-only snapshot だけを持つ。

`AudioTurnController` は WebSocket send、DB write、TTS 実行、reply 生成、会話参加判断、
candidate 発話判断を行わない。`TomoroSession` は `AudioTurnController` の内部 field や private method を
直接読まず、必要な情報は public API / public property から取得する。

### 2026-05-23 追記: reply display 境界

上の `audio/emotion/image` 分割は、`audio/display` 分割へ改める。
emotion は TTS style にも使うが、画像や将来の pose / animation などの表示状態を駆動する入力でもあるため、
reply 配下では display concern としてまとめて扱う。

```text
TomoroSession
  -> ReplyPipeline
       -> ReplyAudioPlanner
       -> ReplyDisplayPlanner
```

`ReplyDisplayPlanner` は current emotion と表示 asset 解決を所有する。
`TomoroSession` は image path や表示媒体ごとの対応表を直接持たず、`ReplyPipeline` から返る command を
既存 WebSocket event に変換するだけに留める。

### 2026-05-23 追記: Kokoro MLX streaming TTS と reply task 化

TTS backend は `say` から `kokoro_mlx` を default に切り替える。
`say` は fallback / regression 用に残すが、設計上は「同期コマンドが終わるまで `/ws` 受信ループを止める」
前提を捨てる。

```text
ThinkingMode token stream
  -> ReplyPipeline
       -> text_delta: すぐ WebSocket JSON
       -> tts_text: sentence flush 単位で TTS queue
  -> TTS worker
       -> TTSBackend.synthesize(TTSInput) を streaming 消費
       -> AudioChunkOut が出るたび WebSocket binary
```

`TomoroSession.process_audio_chunk()` は参加判断後に reply generation task を起動して戻る。
これにより、Tomoko が返答を生成中でも同じ `/ws` でマイク入力を受け続けられる。

`KokoroMLXBackend` は `kokoro_mlx.KokoroTTS.from_pretrained()` でモデルをロードし、
`generate_stream(text, voice, speed, sample_rate)` を `asyncio.to_thread` で非同期 generator に包む。
Kokoro が返す numpy audio chunk は、ブラウザの `decodeAudioData` 互換性を保つため
chunk ごとに RIFF/WAVE として `AudioChunkOut` に入れる。raw PCM の解釈をクライアントに移さない。

hard interrupt の扱い:

```text
STT final while reply/TTS active
  -> BargeInDetector
  -> hard_interrupt / restart_turn
  -> reply task cancel
  -> TTS worker cancel
  -> audio_control stop
```

クライアントは引き続き判定しない。
`audio_control stop` を受けたら再生中/予約済み source を止めるだけで、
barge-in 判定と TTS cancel の判断はサーバー側 `TomoroSession` に残す。

---

## 2026-05-24 追記: 長期記憶（エピソード記憶）

Phase 8 では、会話ログ本体と embedding を分離する。
`conversation_logs` は人間が読める会話原本として保持し、検索用 vector は
`conversation_embeddings` に保存する。

```text
conversation_logs
  id / recorded_at / role / transcript / emotion / status
        │
        ▼
conversation_embeddings
  conversation_log_id / embedding vector(384) / model / embedded_at
```

embedding は `intfloat/multilingual-e5-small` をローカル実行する。
音声データは関与せず、保存済みの会話テキストだけを `passage: ...` として embedding 化する。
検索時は現在発話を `query: ...` として embedding 化し、pgvector cosine search で top-K を取得する。

`ThinkDeepMode` は FastMode の WebSocket 出力契約を変えない。
`ThinkingInput.long_term_memory` に入った `MemoryHit` を system prompt に追加し、
返答ストリーミング、emotion 分離、TTS への流れは既存のまま使う。

`TomoroSession` は短い発話では `ThinkFastMode`、記憶 cue がある発話や長めの相談文では
`ThinkDeepMode` を選ぶ。
この選択もサーバー側に閉じ、クライアントへ判断ロジックは移さない。

---

## 2026-05-24 追記: 会話セッションと要約索引

Phase 7/8 の実装では、`conversation_logs` の role 行と turn 単位 embedding によって
短期記憶と長期記憶を作った。
この設計は原本保存としては正しいが、「どこからどこまでが一つの会話か」を DB 上で表せない。
そのため M2 の追加 Phase では、会話単位を `conversation_sessions` として明示する。

### 原本と索引の分担

```text
conversation_sessions
  id / started_at / ended_at / start_reason / end_reason
  summary_text / summary_status / summary_embedding vector(384)
        │
        ├── conversation_logs
        │     conversation_session_id / role / transcript / emotion / status
        │
        └── session summary search
              現在発話 -> summary_embedding 検索 -> 関連 session -> session 内 turn を読む
```

`conversation_logs` は会話原本であり、要約で上書きしない。
`conversation_sessions.summary_text` と `summary_embedding` は検索・文脈復元のための派生データである。
要約が間違っていた場合は再生成できるが、原本の `conversation_logs` は保持する。

### なぜ summary embedding を別テーブルにしないか

turn 単位 embedding は既に `conversation_embeddings` に分離している。
一方、session 要約 embedding は session そのものの代表表現なので、まずは
`conversation_sessions.summary_embedding` として同じ行に持たせる。

別テーブルに分けるのは、次の必要が出た時でよい。

- 複数 embedding モデルを同じ session に対して保持する
- 要約種類を複数持つ（感情要約、タスク要約、日記候補など）
- embedding の履歴や A/B 比較を保存する
- 運用上、pgvector index やテーブル肥大化を分離する必要がある

現時点では、管理しやすさとデバッグしやすさを優先し、`conversation_sessions` 一本にまとめる。

### セッション開始と終了

session の authoritative state は `TomoroSession` が持つ。
クライアントや background worker は会話参加を判断しない。

```text
ambient
  wake word / should_participate=True
    -> conversation_sessions を作成
    -> attention_mode = engaged

engaged / cooldown
  user / tomoko turn
    -> 同じ conversation_session_id で conversation_logs に保存

cooldown
  無発話 timeout
    -> ended_at を保存
    -> summary_status = pending
    -> attention_mode = ambient

withdrawn
  明示的に引く
    -> ended_at を保存
    -> summary_status = pending
```

ambient / observer 発話は `ambient_logs` に残すが、会話 session には入れない。
hard interrupt で `status='interrupted'` として保存する Tomoko turn は、その発話中の session に紐づける。

### 要約と embedding はオンライン経路から外す

会話終了時に `TomoroSession` が LLM 要約や embedding 生成を実行すると、
次の会話開始や `/ws` 受信ループに不要な計算負荷が乗る。
そのため、`TomoroSession` は session を閉じて `summary_status='pending'` にするだけにする。

別プロセスの `session_summarizer` が pending session を追いかける。

```text
session_summarizer loop
  -> summary_status='pending' の ended session を取得
  -> session 内の conversation_logs を読む
  -> InferenceRouter.select("session_summary", "privacy") で要約
  -> EmbeddingBackend で summary_text を embedding
  -> conversation_sessions に summary_text / summary_embedding を保存
  -> summary_status='completed'
```

失敗時は `summary_status='error'` と `summary_error` を残し、再実行できるようにする。
要約と embedding はローカル実行を基本にし、会話内容をクラウドへ出さない。

### 記憶検索の使い方

短期文脈:

1. active `conversation_session_id` の completed turn を優先して読む
2. 足りない場合だけ、最近の completed turn で補う
3. 現在の user transcript は重複除外する

長期文脈:

1. 現在発話で `conversation_sessions.summary_embedding` を検索する
2. 関連 session の summary を候補として `ThinkingInput.long_term_memory` に渡す
3. 必要なら、その session 内の `conversation_logs` や turn 単位 `conversation_embeddings` で細部を読む

これにより、プログラムは log を毎回走査して会話単位を推定せず、
「会話単位の索引カード」から関連する原本へ辿れる。

---

## 2026-05-24 追記: 用語集ログと人格スナップショット

セッション要約は「何の話だったか」を圧縮する。
一方で、印象的な言い回し、関係性の合図、訂正された事実、Tomoko らしい応答癖は
要約だけでは落ちやすい。
このため、会話セッション要約とは別に、用語集と人格状態を versioned JSONB snapshot として残す。

### 目的

- 後から「いつ、何が Tomoko の語彙や性格状態に影響したか」を追跡できる
- 要約で落ちやすい印象的フレーズや関係性の手触りを残す
- persona update / diary / 自発発話が全 raw log を毎回読まずに済む
- 外部分析では PostgreSQL の `jsonb` / jsonpath / GIN index を使える
- プログラム内では JSON を直接触らず、schema version 付きモデルクラスに移して扱う

### テーブル

```sql
CREATE TABLE persona_lexicon_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_session_id UUID REFERENCES conversation_sessions(id),
    previous_version_id UUID REFERENCES persona_lexicon_versions(id),
    reason TEXT NOT NULL,
    lexicon_json JSONB NOT NULL,
    diff_json JSONB NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'completed'
);

CREATE TABLE persona_state_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_session_id UUID REFERENCES conversation_sessions(id),
    previous_version_id UUID REFERENCES persona_state_versions(id),
    reason TEXT NOT NULL,
    state_json JSONB NOT NULL,
    diff_json JSONB NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'completed'
);
```

`lexicon_json` / `state_json` はその時点の全体 snapshot。
`diff_json` は前 version からの変動点だけを持つ。
これにより、ある応答時点の Tomoko が持っていた語彙・関係性・性格状態を再現しやすくする。

JSONB を使う理由:

- JSON 全体を1レコードとして保持でき、versioned snapshot として読みやすい
- `jsonb_path_query` / `@>` / `?` などで外部分析しやすい
- 必要な key に GIN index や expression index を張れる
- 正規化しすぎず、LLM が返す構造化知識をそのまま保存できる

ただし、アプリケーションコードは生 dict を持ち回らない。
`server/shared/models.py` に `PersonaLexiconSnapshot` / `PersonaStateSnapshot` のようなモデルクラスを置き、
DB 入出力時に JSONB と相互変換する。
schema を変える時は `schema_version` を上げ、migration / loader で吸収する。

### lexicon_json の形

```jsonc
{
  "schema_version": 1,
  "user_terms": [
    {
      "term": "カレーの話",
      "meaning": "前に作ったカレーの経過や味の話題",
      "tone": "親しみ",
      "salience": 0.82,
      "first_seen_session_id": "...",
      "last_seen_session_id": "...",
      "evidence": ["昨日カレーを作ったよ"]
    }
  ],
  "tomoko_phrases": [
    {
      "phrase": "それ、ちょっと覚えておきたい",
      "usage": "相手のこだわりや感情が出た時",
      "salience": 0.74,
      "evidence_session_id": "..."
    }
  ],
  "relationship_markers": [
    {
      "marker": "さっきの続き",
      "meaning": "同一会話セッション内の継続話題として扱う",
      "salience": 0.7
    }
  ],
  "corrections": [
    {
      "wrong": "以前の仮理解",
      "correct": "訂正後の理解",
      "source_session_id": "..."
    }
  ]
}
```

### state_json の形

```jsonc
{
  "schema_version": 1,
  "traits": {
    "warmth": 0.72,
    "playfulness": 0.48,
    "initiative": 0.35
  },
  "relationship": {
    "familiarity": 0.61,
    "preferred_address": "トモコ",
    "boundaries": ["静かにしてと言われたら withdrawn を尊重する"]
  },
  "speaking_style": {
    "sentence_length": "short",
    "honorific_level": "casual_polite",
    "signature_phrases": ["うん", "それ、覚えておきたい"]
  },
  "open_threads": [
    {
      "topic": "カレーの味の変化",
      "source_session_id": "...",
      "status": "watch"
    }
  ]
}
```

### diff_json の形

```jsonc
{
  "schema_version": 1,
  "added": [
    {
      "path": "$.user_terms",
      "value": {"term": "カレーの話", "meaning": "..."},
      "reason": "会話内で繰り返し参照された"
    }
  ],
  "updated": [
    {
      "path": "$.relationship.familiarity",
      "from": 0.58,
      "to": 0.61,
      "reason": "継続会話が自然に成立した"
    }
  ],
  "deprecated": [
    {
      "path": "$.corrections[0]",
      "reason": "新しい訂正で置き換えられた"
    }
  ]
}
```

### 生成タイミング

`session_summarizer` が session summary を作った後、`lexicon_update` / `persona_update` が
その session の summary、salient phrases、必要な raw turns を読んで version を追加する。

```text
conversation session closed
  -> session_summarizer: summary_text / summary_embedding
  -> lexicon_update: persona_lexicon_versions を追加
  -> persona_update: persona_state_versions を追加
```

この処理は background worker で行い、`TomoroSession` のオンライン経路に乗せない。
人格変化は即時反映ではなく、次回以降の応答で使われればよい。

### 応答生成での使い方

`ThinkFastMode` / `ThinkDeepMode` は必要に応じて最新の lexicon / persona snapshot を
軽く圧縮して system prompt に入れる。
毎回全 JSON を入れるのではなく、現在発話や関連 session summary に関係する subset だけを選ぶ。

```text
current transcript
  -> session summary search
  -> related sessions
  -> lexicon_json から関連 term / phrase を抽出
  -> persona_state_versions 最新 snapshot から speaking_style / relationship を抽出
  -> ThinkingInput に補助文脈として渡す
```

JSONB snapshot は分析と再現性のための保存形式であり、LLM prompt にそのまま全量投入しない。
必要な subset をモデルクラスに読み込み、プロンプト用 DTO に変換する。

---

## 2026-05-24 追記: ContextSnapshotBuilder

記憶・要約・用語集・人格スナップショットが増えるほど、各 `ThinkingMode` が個別に DB を読む設計は
レイテンシーとテスト範囲の両方を悪化させる。
そのため、LLM に渡す文脈は `ContextSnapshotBuilder` で一箇所に組み立てる。

### 役割分担

```text
TomoroSession
  state / attention_mode / active conversation_session_id を決める
        │
        ▼
ContextSnapshotBuilder
  depth と token / latency budget に従って文脈を読む
        │
        ▼
ThinkingMode
  TomokoContextSnapshot を prompt messages に変換して応答する
```

`ContextSnapshotBuilder` は読み取り専用である。
session 開始/終了、summary 生成、persona update、lexicon update などの副作用を持たない。
これにより、文脈取得のレイテンシーと正しさを単独でテストできる。

### 長期運用における context build の原則

Tomoko は長期運用すると `conversation_logs` / `conversation_embeddings` /
`conversation_sessions` / `persona_lexicon_versions` / `persona_state_versions` が増え続ける。
しかし、メイン対話 LLM の応答開始前に使える context 生成時間は増やしてはいけない。

そのため `ContextSnapshotBuilder` は「全記憶を成功するまで読む処理」ではなく、
**時間予算つきの best-effort context runtime** として扱う。

```text
原本:
  PostgreSQL に増え続けてよい

索引:
  embedding / summary / lexicon / persona snapshot として再生成可能

応答前 context:
  latency budget / token budget / depth に従って固定時間内に構築する
```

context build が時間切れになっても、オンライン応答全体を失敗にしない。
同一会話セッションの直近文脈を baseline とし、長期記憶・人格・用語集は
時間内に取れた場合だけ応答品質を上げる optional enrichment として扱う。

```text
required:
  same session recent turns

preferred:
  recent completed turns
  related session summaries

optional:
  turn-level memory hits
  lexicon terms
  persona slice
  diary hints
```

これにより、記憶量が増えてもメイン応答 LLM に渡す context 生成時間の上限を固定できる。
PC の性能が上がった場合は、設計を変えずに `ContextBuildPolicy` の budget / top-K /
token budget だけを広げる。

### parallel DB I/O

PostgreSQL が唯一の真実であり、context 生成は DB 以外の権威ある状態を読まない。
その前提では、context build は複数の DB read を直列に積むのではなく、
時間制限付きの parallel DB I/O として実行する。

```text
ContextSnapshotBuilder
  ├─ same_session_recent_turns query
  ├─ recent_completed_turns query
  ├─ session_summary_vector_search query
  ├─ turn_embedding_vector_search query
  ├─ persona_state query
  └─ lexicon_snapshot query

deadline に到達したら未完了 query は cancel / ignore し、
返ってきた候補だけで snapshot を assemble する。
```

並列に返ってきた結果は、返却順ではなく次の順で再評価してから prompt context に入れる。

```text
1. same session 優先
2. attended=true / completed turn 優先
3. relevance
4. recency
5. salience
6. token budget
7. deduplication
```

これにより、取得源を増やしても応答前の最大待ち時間を増やさずに済む。
遅い取得源は trace に残し、DB / index / query / PC 性能 / retrieval strategy のどれが悪いかを
局所化して分析する。

### ContextBuildPolicy

`ContextSnapshotBuilder` は設定可能な policy を受け取る。

```python
@dataclass(frozen=True)
class ContextBuildPolicy:
    depth: ContextDepth
    max_build_ms: int
    max_prompt_tokens: int
    max_same_session_turns: int
    max_recent_turns: int
    max_session_summaries: int
    max_memory_hits: int
    max_lexicon_terms: int
    allow_turn_memory_search: bool
    allow_persona_slice: bool
```

初期値は conservative にし、性能改善や実測に応じて広げる。
policy は「Tomoko の賢さ」と「応答速度」のトレードオフを調整するための制御点である。

### ContextBuildTrace

context build は必ず trace を返す。
trace は DB には必須保存しないが、少なくとも debug / latency log へ出せるようにする。

```python
@dataclass
class ContextBuildTrace:
    budget_ms: int
    elapsed_ms: float
    timed_out: bool
    depth: ContextDepth
    included_counts: dict[str, int]
    skipped_sources: list[str]
    stage_timings_ms: dict[str, float]
    cache_hits: dict[str, bool]
    source_errors: dict[str, str]
```

ログ例:

```json
{
  "event": "context_build_completed",
  "depth": "normal",
  "budget_ms": 50,
  "elapsed_ms": 48.7,
  "timed_out": true,
  "included": {
    "same_session_turns": 8,
    "recent_turns": 2,
    "session_summaries": 1,
    "memory_hits": 0,
    "lexicon_terms": 0
  },
  "skipped": ["turn_memory_search", "persona_slice"],
  "stage_timings_ms": {
    "same_session": 6.2,
    "recent_turns": 8.1,
    "session_summary_search": 31.4
  }
}
```

trace により、次を切り分ける。

```text
DB が遅い
pgvector / index が効いていない
connection pool が足りない
retrieval strategy が欲張りすぎ
PC の性能が不足している
そもそも取得した context が応答品質に効いていない
cache TTL が短すぎる / 長すぎる
```

context build timeout は failure ではなく degraded response とする。
最低限 same session recent turns が取れていれば応答は継続する。

### process-local TTL cache

単一サーバーインスタンスで運用している間は、Redis を入れずに
`ContextSnapshotBuilder` 内部の process-local TTL cache で高速化してよい。

ただし cache は source of truth ではなく、DB read の speed-up に限定する。

```text
DB:
  唯一の真実

process-local TTL cache:
  直近の DB read 結果を短時間だけ再利用する最適化
```

cache してよいもの:

```text
latest persona state
latest lexicon snapshot
recent completed turns
same session turns
session summary search result
query embedding result
```

cache しないもの:

```text
conversation_logs への書き込み
active session の authoritative state
attention_mode の authoritative state
playback / barge-in の現在状態
hard interrupt 判定
```

TTL の初期値は短くする。

```text
same_session_turns: 0.5〜2秒
recent_turns: 1〜5秒
persona_state: 5〜30秒
lexicon_snapshot: 30〜300秒
session_summary_search: 5〜30秒
```

cache hit / miss / age_ms / ttl_ms は `ContextBuildTrace` に含める。
将来サーバーインスタンスが複数になったり、background worker と realtime node 間で
共有 cache が必要になった時点で Redis 等を検討する。

### non-blocking / parallel / state の境界

Tomoko のオンライン経路では、non-blocking I/O、parallel retrieval、状態機械が同時に動く。
非同期処理は順序を崩すが、状態遷移は順序に依存するため、
状態更新の入口は `TomoroSession` に寄せる。

```text
外部イベント
  WebSocket binary
  playback telemetry
  transcript finalized
  LLM delta
  TTS chunk
  timeout
        │
        ▼
TomoroSession
  authoritative state / attention_mode / session_id / turn_id を更新
        │
        ▼
Command
  DB read/write
  context build
  LLM generation
  TTS generation
  WebSocket send
```

`TomoroSession` は現時点では複雑さを一手に引き受ける管制塔として残す。
ただし、巨大クラス化を目的にするのではなく、現実の依存関係を集約して観察し、
安定した境界から順に小さな component へ切り出す。

切り出し候補:

```text
AttentionStateMachine
PlaybackTracker / AudioTurnController
ConversationSessionManager
TurnLifecycleManager
BargeInDetector
ContextSnapshotBuilder
ReplyPipeline
```

状態を持つものと、判定だけを行うものは分ける。
authoritative な会話 state / attention state は引き続き `TomoroSession` が所有する。

---

## 2026-05-24 追記: TomoroSession の状態管理戦略

Tomoko のオンライン経路では、音声入力、VAD、STT、参加判断、attention、playback telemetry、
barge-in、conversation session、context build、LLM、TTS、WebSocket 出力が同時に進む。
これらは non-blocking / parallel に動く一方で、状態遷移は順序に依存する。

そのため、メイン層に制御判断を残さない。
メイン層は I/O adapter と command executor に寄せ、`TomoroSession` を stateful control core とする。

```text
Main layer:
  WebSocket / timer / backend result を SessionEvent に変換する
  TomoroSession から返された StateEmission / SessionCommand を実行する
  participation / playback / session lifecycle の判断はしない

TomoroSession:
  TomoroRuntimeState を所有する
  状態変更の入口を post_event(event) に集約する
  event と現在 state から制御判断する
  new_state / emissions / commands を返す
```

### 一方向の制御フロー

`TomoroSession` の外側は、状態を直接変更しない。
外部入力はすべて `SessionEvent` として `TomoroSession` に渡す。
`TomoroSession` は判断済みの結果だけを `StateEmission` / `SessionCommand` として外へ出す。

```text
external input
  WebSocket binary
  playback_started / playback_ended
  transcript_finalized
  timer_tick
  context_build_completed
  llm_delta
  tts_chunk_ready
        │
        ▼
TomoroSession.post_event(event)
        │
        ▼
TomoroSession._reduce(event, state)
        │
        ▼
TransitionResult
  new_state
  state_emissions
  session_commands
        │
        ▼
Main layer
  emissions を WebSocket / log / metrics に流す
  commands を実行する
  command 結果を event として TomoroSession に戻す
```

この流れにより、メイン層に「今は返答すべきか」「これは echo か」
「session に入れるべきか」「audio stop すべきか」といった判断を残さない。

### TomoroRuntimeState

直交する状態は `TomoroRuntimeState` に集約する。
ただし、state は「今どうなっているか」を表すだけで、制御ロジックは持たない。

```python
@dataclass(frozen=True)
class TomoroRuntimeState:
    attention_mode: AttentionMode
    vad_state: VadState
    playback_state: PlaybackState
    active_session_id: UUID | None
    active_turn_id: UUID | None
    speaking_turn_id: UUID | None
    context_build_id: UUID | None
    updated_at: datetime
```

外部から現在状態を読む場合は `get_now_state()` を使う。
返すのは snapshot であり、外部は state を変更しない。

### SessionEvent と TransitionResult

状態を変える入力は `SessionEvent` として表現する。
初期実装では文字列 `type` でよい。
必要になった段階で `TranscriptFinalized` / `PlaybackStarted` /
`ContextBuildCompleted` などの個別 dataclass に分ける。

```python
@dataclass(frozen=True)
class SessionEvent:
    type: str
    payload: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True)
class StateEmission:
    type: str
    payload: dict[str, Any]
    state_snapshot: TomoroRuntimeState
    occurred_at: datetime


@dataclass(frozen=True)
class SessionCommand:
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class TransitionResult:
    state: TomoroRuntimeState
    emissions: list[StateEmission]
    commands: list[SessionCommand]
```

`StateEmission` は観測・通知である。
WebSocket の状態表示、debug log、metrics、test probe に使う。

`SessionCommand` は副作用要求である。
DB write、context build、LLM generation、TTS generation、audio control stop、
WebSocket send など、`await` が必要な処理は command として外に出す。

### reducer の原則

`_reduce()` は可能な限り同期的・短時間・副作用なしに寄せる。
`await` をまたいで中途半端な state を残さない。

```python
def _reduce(self, event: SessionEvent) -> TransitionResult:
    match event.type:
        case "transcript_finalized":
            return self._resolve_transcript_event(event)
        case "playback_started":
            return self._resolve_playback_started(event)
        case "playback_ended":
            return self._resolve_playback_ended(event)
        case "timer_tick":
            return self._resolve_timer_tick(event)
        case _:
            return TransitionResult(
                state=self._state,
                emissions=[],
                commands=[],
            )
```

重い処理は command として起動し、その結果を再び event として `TomoroSession` に戻す。

```text
SessionCommand("build_context")
  -> ContextSnapshotBuilder.build(...)
  -> SessionEvent("context_build_completed")

SessionCommand("start_llm_reply")
  -> LLM streaming
  -> SessionEvent("llm_delta")
  -> SessionEvent("llm_completed")

SessionCommand("start_tts")
  -> TTS chunk generation
  -> SessionEvent("tts_chunk_ready")
```

### 優先順位の解決箇所

直交する状態と優先順位の解決は `TomoroSession` に閉じ込める。
判定器は部品として使うが、最終的な制御判断は `TomoroSession` が行う。

例:

```text
transcript_finalized を受けたとき:
  withdrawn か
  active playback chunk 中か
  playback ended grace 中か
  echo か
  hard interrupt か
  wake word か
  follow-up として扱うか
  active session に紐づけるか
  Tomoko turn を interrupted 保存するか
  audio_control stop を出すか
  reply generation を開始するか
```

これらの判断をメイン層、`BargeInDetector`、`ParticipationJudge`、`PlaybackTracker` に分散させない。
各 detector / judge は分類結果を返すだけにし、それをどう優先するかは
`TomoroSession` の `_resolve_transcript_event()` で決める。

### event-shaped session runtime

これは本格的な event-driven architecture ではない。
外部 EventBus、pub/sub、状態機械ライブラリ、event sourcing は初期段階では導入しない。

初期実装は `TomoroSession` 内部の小さな reducer と command 境界に留める。
M2 では `post_event()` の入口と `TransitionResult` の契約を作り、必要になった段階で
小さな event queue / drain loop を足す。

```python
class TomoroSession:
    async def post_event(self, event: SessionEvent) -> TransitionResult:
        result = self._reduce(event)
        self._state = result.state
        return result
```

M3 の自発発話や arrival で競合が増えた場合は、event queue / drain loop を追加し、
command result を必ず `SessionEvent` として戻す。

```python
class TomoroSession:
    async def post_event(self, event: SessionEvent) -> None:
        await self._event_queue.put(event)
        await self._drain_events()

    async def _drain_events(self) -> None:
        if self._draining:
            return

        self._draining = True
        try:
            while not self._event_queue.empty():
                event = await self._event_queue.get()
                result = self._reduce(event)
                self._state = result.state

                for emission in result.emissions:
                    await self._emit(emission)

                for command in result.commands:
                    self._start_command(command)
        finally:
            self._draining = False
```

timer や background worker は polling してよい。
ただし、状態を変える場合は直接 state を変更せず、必ず `SessionEvent` として
`post_event()` に渡す。

### stale result の破棄

非同期処理では、古い LLM delta、TTS chunk、context build result、playback telemetry が
遅れて戻ることがある。

そのため、event / command には必要に応じて次の ID を持たせる。

```text
session_id
turn_id
chunk_id
context_build_id
```

`TomoroSession` は現在の `TomoroRuntimeState` と照合し、現在 state と一致しない結果は
stale として捨てる。

### 将来の切り出し方針

現時点では `TomoroSession` に複雑さを集約する。
これは巨大クラス化を目的にするのではなく、現実の依存関係を観察し、
安定した境界から component へ切り出すためである。

切り出し後も、メイン層との契約は `SessionEvent` / `StateEmission` / `SessionCommand`
に保つ。
これにより、内部実装を作り変えてもメイン層を薄い adapter のまま維持できる。

### DTO

```python
ContextDepth = Literal["fast", "normal", "deep", "reflective"]

@dataclass
class TomokoContextSnapshot:
    depth: ContextDepth
    recent_turns: list[ConversationTurn]
    session_summaries: list[SessionSummaryHit]
    memory_hits: list[MemoryHit]
    lexicon_terms: list[LexiconTerm]
    persona_slice: PersonaPromptSlice | None
    token_budget_hint: int
    build_elapsed_ms: float
    source_counts: dict[str, int]
    trace: ContextBuildTrace
```

`TomokoContextSnapshot` は DB row や JSONB をそのまま露出しない。
JSONB snapshot は `PersonaLexiconSnapshot` / `PersonaStateSnapshot` のモデルクラスへ変換し、
さらに prompt 用の subset として `LexiconTerm` / `PersonaPromptSlice` に落とす。

### depth

| depth | 用途 | 読むもの | online |
|---|---|---|---|
| `fast` | 通常の即応 | active session の直近 completed turn | yes |
| `normal` | 通常 + 軽い記憶 | fast + 関連 session summary + 関連 lexicon 少量 | yes |
| `deep` | 記憶 cue / 長め相談 | normal + turn embedding / session 内代表 turn | yes（必要時のみ） |
| `reflective` | 日記・人格更新 | raw logs / summaries / lexicon / persona を広めに読む | no |

online 会話の default は `fast` または `normal` とする。
`deep` は記憶 cue や長めの相談文でのみ使い、`reflective` は background worker 専用にする。

### 初段実装

初段では Phase 8.5/8.6/8.7 の全要素が未実装でも動くようにする。

```text
fast:
  active_session_id があれば同一 session の recent turns
  なければ既存 read_recent_turns(limit=N)

normal:
  fast + completed session summaries があれば summary search
  summary 未実装なら空 list

deep:
  normal + 既存 conversation_embeddings search
```

これにより、`ThinkFastMode` / `ThinkDeepMode` は段階的に DB 詳細から切り離せる。

### レイテンシー目標

context snapshot はメイン対話推論の前段なので、絶対ラウンドトリップ時間を固定して監視する。

| depth | 目標 |
|---|---:|
| `fast` | 20ms 以内 |
| `normal` | 50ms 以内 |
| `deep` | 100ms 以内 |

perf test 例:

```bash
pytest -m perf tests/perf/test_context_snapshot_latency.py
```

ログには少なくとも次を残す。

```text
ContextSnapshotBuilder depth=normal elapsed_ms=34.2
recent_turns=8 session_summaries=2 memory_hits=0 lexicon_terms=3
```

この値が悪化した時点で、記憶や人格の追加がオンライン会話レイテンシーを侵食していると判断できる。

## Short memory extraction lane

短期作業メモリは、会話原本や長期記憶ではなく、数ターンだけ使う揮発的な prompt hint として扱う。
`TomoroSession` が reply 完了後に background task として extraction を起動し、現在ターンの reply / TTS / playback hot path は待たせない。

extraction は `InferenceRouter` の `memory_extraction` role を経由して、可能な場合は LLM structured output を使う。
出力は store / skip decision、reason、raw_text、proposal list を持つ JSON とし、store の proposal だけを `ShortMemoryBuffer` に追加する。
LLM structured output が失敗した場合、または明らかなノイズ発話の場合は heuristic fallback / prefilter を使う。

この lane は DB 永続化、long-term memory、persona snapshot、embedding retrieval、task scheduling へ昇格しない。
ContextSnapshotBuilder は引き続き読み取り専用で、short memory buffer への書き込み責務を持たない。
