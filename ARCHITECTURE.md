# ARCHITECTURE.md

このドキュメントは Tomoko の全体設計と、なぜその設計にしたのかの理由を残すものです。

## 設計の一言サマリ

**「内面の時間が流れている存在」としての Tomoko を、ノード分散 + PostgreSQL 中央集約で実現する。**

コアロジックは起動後に変わらない。環境（バックエンド・ノード構成）だけが変わる。

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
`docs/latency.md` に Ollama と MLX の実測値を並べて判断する。

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
# docs/latency.md に say vs kokoro-mlx の実測値が並ぶ

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
- `docs/latency.md` への自動追記でトレンドが見える

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
├── docs/
│   └── latency.md              計測ログ
├── docker-compose.yml
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
