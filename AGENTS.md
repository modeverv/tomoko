# AGENTS.md

このファイルはこのリポジトリで作業する AI コーディングエージェント
（Claude Code / Cursor / その他）向けの指示書です。

---

## 禁止事項
- AGENTS.mdの追記以外の変更は受け入れない。追記する場合は既存の内容を言語で否定し新しい内容を追記すること
- PLAN.mdの追記以外の変更は受け入れない。追記する場合は既存の内容を言語で否定し新しい内容を追記すること。ただし チェックボックス(- [ ] )の状態を変えることは許可する。
- reference/ディレクトリの変更は受け入れない。過去の実装経験を記録する場所なので、変更が必要な場合は新しいファイルを追加すること
- LOG.mdの追記以外の変更は受け入れない。作業セッションの記録なので、変更が必要な場合は新しいセッションを追加すること
- MEMORY.mdの追記以外の変更は受け入れない。設計判断と気づきの記録なので、変更が必要な場合は新しいセクションを追加すること

## 作業開始前に必ずやること（必須）

**この手順を省略してはいけない。**

### Step 1: 現在地を確認する

```bash
cat MEMORY.md   # 確定済みの設計判断を把握する
cat LOG.md      # 前回セッションの状況と次にやることを確認する
```

これを読まずに実装を始めてはいけない。
前回セッションの判断を知らないまま実装すると、確定済みの判断を覆す実装をしてしまう。

### Step 2: 今回やる Phase を確認する

```bash
cat PLAN.md     # 今のマイルストーンと対象 Phase を確認する
```

PLAN.md の完了条件（✅）を確認して、何をもって完了とするかを把握してから実装を始める。

### Step 3: 関連するリファレンス実装を確認する

```bash
ls reference/   # 参考実装の一覧を確認する
```

`reference/` ディレクトリには過去の実装経験から得た参考コードが置いてある。
実装に入る前に関連するファイルを読んで、同じ苦労を繰り返さないようにする。

特に以下は必ず確認する：
- `reference/unity/MyAIRoomScript.cs` — 音声録音・VAD・API通信の実装経験
- `reference/server/api.py` — サーバー側の旧実装。今回はここに書いてあることの逆をやる

### Step 4: LOG.md に作業開始を記録する

```markdown
## YYYY-MM-DD セッションN

### やること（開始時に書く）
- 今回実装する Phase と内容
```

---

## 作業中のルール

### テストを先に書く

実装より先にテストを書く。書けないなら設計が曖昧なので MEMORY.md に疑問を記録して人間に確認する。

### 詰まったら MEMORY.md に書いて止まる

```markdown
## 未解決の疑問（人間への確認待ち）

### [YYYY-MM-DD] 疑問のタイトル
状況の説明。何を試したか。何がわからないか。
```

深入りしない。判断を勝手に下さない。人間に委譲する。

### 気づきは即座に MEMORY.md に書く

実装中に重要な発見（制約・落とし穴・想定外の動作）があったら即座に記録する。

---

## 作業終了時にやること

### LOG.md を更新する

```markdown
### やったこと
- 実装した内容

### 詰まったこと・解決したこと
- 問題と解決策

### 次のセッションでやること
- 積み残し
```

### MEMORY.md を更新する

- 確定した判断を「確定した判断」セクションに移す
- 解決した疑問を削除する
- 新しい気づきを追記する

### テストが通っていることを確認してからコミットする

```bash
pytest -m unit
git add .
git commit -m "feat(phase-X): ..."
```

### 人間の介入
人間はgithubのマスターブランチでweb画面を見ながら確認する。
そのため、masterブランチに作業をマージする必要があるときはマージし、マスターブランチをプッシュする。

---

## reference/ ディレクトリについて

`reference/` には過去の実装経験から得た参考コードを置いている。
**そのまま使うのではなく、設計の参考として読む。**

今回の実装で解決したかった課題がここに記録されている。
同じ轍を踏まないために読む。

```
reference/
├── unity/
│   └── MyAIRoomScript.cs   音声録音・VAD（音量閾値）・OGGエンコード・REST API
│                            今回はここの「逆」をやる
│                            float[] を WebSocket で直接流す設計の出発点
└── server/
    └── api.py              旧サーバー実装
                            OGG→MP3変換・Base64・REST一括返却の苦肉の策
                            今回はこの種の変換を一切しない
```

参考にする観点：
- `MyAIRoomScript.cs` の `gaman`（無音待機時間）→ 今回は Silero VAD に置き換える
- `api.py` の `convert_ogg_to_mp3` → 今回は float32 をそのまま流すので不要
- `api.py` の `get_response_wave_from_text` → 今回はストリーミングに分解する

---

## プロジェクトの一行サマリ

ローカル推論で動く、記憶と人格を持つ音声対話システム。一人用。
レイテンシーと体験の質に全振りする。

## 必読ドキュメント

作業を始める前に必ず読んでください。

1. `README.md` — プロジェクト全体像
2. `ARCHITECTURE.md` — 設計判断とその理由
3. `PLAN.md` — 段階的実装計画。**今どの Phase か必ず確認**

## 最重要原則（これを破る変更は受け入れない）

### 1. 1 本の WebSocket で完結させる

通信エンドポイントを増やさないでください。新しい機能を追加するときは、既存の `/ws` の上で
メッセージタイプを増やす方向で考えてください。

**REST エンドポイントを足したくなったら、その前に立ち止まって ARCHITECTURE.md を読み直してください。**

### 2. クライアントにロジックを置かない

ブラウザ側は次のことだけをします：
- マイクから float32 を取って WebSocket に投げる
- WebSocket から来た音声チャンクを再生する
- WebSocket から来た JSON イベントに応じて画像と文字を表示する

**「クライアント側で状態判定」「クライアント側でリトライ」のような実装は禁止**。
全部サーバーに集めてください。

### 3. 推論層は FastAPI に依存しない

`server/thinking/*.py` の中身は FastAPI の Request や WebSocket を import してはいけません。
`AsyncGenerator` を返すただの async 関数として実装してください。

これは LLM ランタイムを差し替える時に効きます（Ollama → 別のもの）。

### 4. 状態機械を分散させない

会話のステートは `server/session.py` の `TomoroSession` クラス一箇所で管理します。
別の場所に "is_processing" フラグなどを生やさないでください。

**3 年前の Unity 実装で `isRecording` `isCommunicating` `isAITalking` が
分散して苦しんだ過去があります。同じ轍を踏まないこと。**

### 5. 会話セッション境界を DB に明示する

`conversation_logs` は会話原本の role 行であり、会話のまとまりそのものではない。
M2 Phase 8.5 以降は、会話のまとまりを `conversation_sessions` で表す。

- `attention_mode` が `ambient -> engaged` になった時、または最初の `should_participate=True` 発話で active session を作る
- `engaged` / `cooldown` 中の user / tomoko turn は同じ `conversation_session_id` で `conversation_logs` に保存する
- `cooldown -> ambient` または `withdrawn` で session を閉じる
- session の開始・終了判断は `TomoroSession` に集約し、クライアントや worker に移さない
- ambient / observer 発話は `ambient_logs` に残し、会話 session へ混ぜない

### 6. セッション要約は原本ではなく索引として扱う

`conversation_sessions.summary_text` と `summary_embedding` は、会話検索と文脈復元のための派生データである。
会話の原本は常に `conversation_logs` とする。

- `conversation_sessions` に session metadata / summary / summary embedding をまとめる
- 要約 embedding 用に別テーブルを増やさない。複数 embedding モデルや履歴管理が必要になるまで一本化を維持する
- 要約と embedding 生成はオンライン会話経路で実行しない
- `TomoroSession` は session を閉じて `summary_status='pending'` にするだけにする
- 別プロセス（`session_summarizer` または `journalist` の前段）が pending session を拾い、要約と embedding を保存する
- 要約が間違っても原本を上書きしない。再生成可能なキャッシュ/索引として扱う

### 7. 用語集と人格状態は versioned JSONB snapshot として扱う

用語集・関係性・人格状態は、後から変動点を追跡できるように versioned snapshot として保存する。
正規化テーブルを細かく増やすのではなく、まずは PostgreSQL `jsonb` カラムに「その時点の全体像」を1レコードで持つ。

- `persona_lexicon_versions.lexicon_json` は用語集・印象的フレーズ・関係性マーカーの全体 snapshot
- `persona_state_versions.state_json` は性格傾向・話し方・関係性状態の全体 snapshot
- `diff_json` は前 version からの変更点を保存する
- `schema_version` を必ず持たせる
- 外部分析では PostgreSQL の `jsonb` / jsonpath / GIN index を使える形にする
- アプリケーションコードでは生の `dict` を持ち回らず、`server/shared/models.py` のモデルクラスへ変換して使う
- JSON schema を変える場合は loader / migration を用意し、古い snapshot を読めるようにする
- これらは原本ではなく、`conversation_logs` / `conversation_sessions` から再生成可能な解釈ログとして扱う

### 8. LLM に渡す文脈は ContextSnapshotBuilder で組み立てる

短期記憶、長期記憶、セッション要約、用語集、人格スナップショットを `ThinkingMode` が個別に読む設計にしない。
M2 Phase 8.8 以降は、LLM に渡す文脈を `ContextSnapshotBuilder` で一箇所に集約する。

- `TomoroSession` は状態遷移と active session ID を決める
- `ContextSnapshotBuilder` は読み取り専用で、DB から必要な文脈を予算内に組み立てる
- `ThinkingMode` は `TomokoContextSnapshot` DTO を使って返答する
- builder は session 開始/終了、persona 更新、要約生成などの副作用を持たない
- `depth` は `fast` / `normal` / `deep` / `reflective` を基本にする
- online 会話では `fast` / `normal` / 必要時の `deep` までにし、`reflective` は background worker 用にする
- snapshot build の elapsed ms と採用した source counts をログに残す
- perf test で `fast` / `normal` / `deep` の絶対ラウンドトリップ目標を固定する
- context build は `ContextBuildPolicy.max_build_ms` に従う best-effort runtime とする
- timeout は応答失敗ではなく degraded context として扱う
- same session recent turns を baseline とし、長期記憶・用語集・人格 slice は optional enrichment とする
- 複数 source は deadline 付き parallel DB I/O として読み、返却順ではなく priority / relevance / recency / salience / token budget で assemble する
- `ContextBuildTrace` を必ず返し、budget / elapsed / skipped source / stage timings / cache hit / source error をログに出せるようにする
- 単一サーバー運用では Redis を導入せず、process-local TTL cache は DB read の speed-up に限定して使う
- cache は source of truth ではない。active session / attention / playback / barge-in など authoritative state は cache しない

## コード規約

### Python

- Python 3.11+
- 型ヒント必須（`from __future__ import annotations` を冒頭に入れる）
- 非同期処理は asyncio。`async def` を基本にする
- 依存管理は uv
- フォーマッタは ruff
- import は標準 → サードパーティ → ローカル の順、ブロック間 1 行空ける

### JavaScript（クライアント）

- TypeScript ではなくプレーン JS（小さく保つため）
- ES modules
- ビルドステップなし（ブラウザに直接読ませる）
- async/await ベース

### ファイル分割

- 1 ファイルは原則 300 行以下
- 例外: `session.py` は状態機械の都合で長くなる可能性がある（500 行まで許容）

## やってほしくないこと

- **隠れた状態を作る**: モジュールレベルの可変変数、シングルトンキャッシュなど。
  必要なら `TomoroSession` のフィールドに集める。
- **ロギングをこっそり消す**: デバッグの命綱です。冗長と思っても消さないでください。
- **「とりあえず動かす」ためのモック**: モックを書くなら明示的に `# MOCK:` コメントを付け、
  必ず `PLAN.md` の TODO に追加してから。
- **新しい外部サービス依存を増やす**: 既存スタック（Ollama / faster-whisper / Silero VAD /
  PostgreSQL / irodori-tts）で実現できないか先に検討する。
- **「設定可能」を増やす**: 一人用のプロジェクトです。設定項目は最小限に。
  全部ハードコードでも構わない。

## やってほしいこと

- **レイテンシーを毎回計測する**: 変更を入れたら必ず実測してメモを残す（`docs/latency.md`）。
- **状態遷移をログに残す**: `TomoroSession` の state が変わったら必ず log.info で記録。
- **「なぜそうしたか」をコミットメッセージに書く**: what より why を重視する。
- **ARCHITECTURE.md を update する**: 設計判断が変わったら必ず反映する。
- **設定ファイルを変えたらテストを再実行する**: `config/*.toml` を変更したら必ず `pytest -m unit` を通す。

## テスト方針

テストは3層で管理する：

| マーカー | 内容 | 実行タイミング |
|---|---|---|
| `unit` | 外部依存なし、MockのみOK | 常に（CI含む） |
| `integration` | 実際のミドルウェアが必要 | 手元でのみ |
| `perf` | レイテンシー計測 | 手元でのみ |

```bash
pytest -m unit                   # CI で常時
pytest -m "unit or integration"  # 手元フル検証
pytest -m perf --tb=short       # レイテンシー計測
```

**テストで検証すること**:
- `InferenceRouter` のバックエンド選択ロジック（全パターン）
- `ParticipationJudge` の参加判断
- `DirectSpeakerResolver` の正規発話元選択
- `DuplicateSpeechFilter` の回り込み検出
- TomoroSession の状態遷移
- E2E レイテンシー 800ms 以内

**テストしないこと**:
- LLM の応答内容そのもの（揺れるから）
- WebSocket の接続自体（手動テストで十分）

## InferenceRouter に関する規約

- コアロジック（session.py / thinking/*.py）は `InferenceRouter` を介してのみバックエンドを呼ぶ
- バックエンドを直接 import してはいけない
- `privacy="privacy"` のタスクはクラウドに出してはいけない（テストで保証する）
- 設定ファイルを変えてテストが通れば、その構成は「動作保証済み」とみなす

## 層間 DTO に関する規約

**層をまたぐ時は必ず DTO を経由する。**
`str` / `bytes` / `np.ndarray` をそのまま層間で渡してはいけない。

```python
# NG: プリミティブを層間で直接渡す
text = stt.transcribe(audio)          # str がそのまま流れる
judge.judge(text)

# OK: DTO で包む
segment = SpeechSegment(audio=audio, ...)
transcript = stt.transcribe(segment)  # Transcript が流れる
judge.judge(transcript)
```

全ての DTO は `server/shared/models.py` に集約する。
新しい境界を作る時は必ずここに DTO を追加してから実装する。

## ホットループのオーバーヘッド回避規約

VAD のホットループ（32ms ごと）は例外として**プリミティブのまま**処理する。

```python
# NG: ホットループ内で DTO を生成する
while True:
    chunk = mic.read(512)
    dto = AudioChunk(data=chunk, timestamp=datetime.now())  # 31回/秒
    vad.process(dto)

# OK: ホットループはプリミティブ、発話終了時だけ DTO に包む
class SileroVAD:
    def process_chunk(self, chunk: np.ndarray) -> float:
        return self.model(torch.from_numpy(chunk), 16000).item()

class VADProcessor:
    def on_speech_end(self, buffer: list[np.ndarray]) -> SpeechSegment:
        return SpeechSegment(audio=np.concatenate(buffer), ...)
```

追加の規約：
- `datetime.now()` をホットループ内で呼ばない。境界でだけ取る
- トークン単位で大量生成される DTO（`ThinkingEvent` / `AudioChunkOut`）は `slots=True` を使う
- VAD 内部のホットパスへの例外を除き、Rule 5（DTO 経由）を必ず守る

## 作業手順と完了判定

### Phase の完了判定

**自動テストがパスすることで、その Phase の作業は完了とみなす。**
主観的な「動いた気がする」は完了ではない。

```bash
# Phase 完了の確認コマンド
pytest -m unit          # 必須、常に通っていること
pytest -m integration   # そのPhaseで追加した統合テストが通ること
pytest -m perf          # レイテンシー目標を満たしていること
```

テストが書けない実装は実装とみなさない。
テストを先に書いてから実装するのが望ましい。

### 迷ったら即座に人間に委譲

**深入りしない。** 判断に迷ったら実装を止めて人間に確認する。

委譲すべき判断の例：

```
- 設計の意図が読み取れない
- ARCHITECTURE.md に書いていない境界が必要になった
- 2つ以上の実装方針が考えられて甲乙つけがたい
- パフォーマンステストが目標値を外れた原因が不明
- 既存の抽象が合わない場面に遭遇した
```

「とりあえずこう判断して進めました」は禁止。
判断ログを MEMORY.md に書いて、人間の確認を待つ。

---

## 作業ログの管理

### LOG.md（作業セッションのログ）

実装セッション中に起きたことを時系列で記録する。
**セッションをまたいで引き継ぐための記録。**

```markdown
# LOG.md

## 2026-05-23 セッション1

### やったこと
- Phase 0 環境構築完了
- PostgreSQL 起動確認
- Ollama + qwen2.5:7b ダウンロード完了

### 詰まったこと
- PGroonga の拡張インストールで失敗
  → docker-compose.yml に build args が必要だった
  → 修正済み、動作確認済み

### 次のセッションでやること
- Phase 1 エコーバックの実装
```

更新タイミング：
- 作業開始時に「今日やること」を書く
- 詰まった時に「何が問題だったか」を書く
- 作業終了時に「次回やること」を書く

### MEMORY.md（判断と設計の記録）

**セッションをまたいで有効な判断・気づき・変更を記録する。**
LOG.md が時系列なのに対して、MEMORY.md はトピックごとに整理する。

```markdown
# MEMORY.md

## 確定した判断

### VAD 無音閾値
実測の結果 400ms が最適。300ms だと「えーっと」で誤検出した。
→ config/central_realtime.toml の vad_silence_ms = 400

### TTS
M1フェーズは say コマンド（Kyoko）で動作確認済み。
kokoro-mlx への切り替えは Phase 5 完了後。

## 未解決の疑問（人間への確認待ち）

### [2026-05-23] faster-whisper のモデルサイズ
small で日本語精度が不十分だった場合、medium に切り替えるか？
→ レイテンシーへの影響を計測してから判断が必要。人間に確認。

## 気づき

### AudioWorklet と Safari の互換性
Safari では AudioWorklet の動作に制限がある可能性。
一人用なので Chrome 専用で割り切るか、確認が必要。
```

更新タイミング：
- 設計判断が確定した時
- 人間への確認が必要な疑問が生じた時
- 実装中に重要な気づきがあった時

---

## Git 運用ルール

### 許可していること

```bash
git add .
git commit -m "..."     # コミットは自由にしてよい
git checkout -b feature/xxx  # ブランチ作成も自由
git merge               # ローカルでのマージも可
```

コミットメッセージは what より why を書く：

```bash
# NG
git commit -m "add vad.py"

# OK
git commit -m "feat(vad): Silero VAD ラッパーを追加

400ms の無音閾値で発話終了を検知する。
300ms だと日常会話の「えーっと」で誤検出したため。
実測値は docs/latency.md に記録済み。"
```

<重要> llm がコミットする場合は、コミット者を作業したllmであることがわかるようにコミット者を"Codex"などのエージェント名にすること。


### 禁止していること

```bash
git push --force        # 絶対禁止
```

#### memo
git pushは現段階では許可する。
<!-- git push origin main    # 絶対禁止-->
<!-- git push origin master  # 絶対禁止-->
<!--git push                # origin への push は全て禁止-->
<!--**origin への push は人間だけが行う。**-->
<!--**LLM がリモートに変更を加えてはいけない。**-->

<!--push が必要だと判断した場合は MEMORY.md に記録して人間に委譲する。-->

### コミットの粒度

Phase 単位ではなく**テストが通る単位でコミットする**：

```
✓ unit テストが通った → コミット
✓ integration テストが通った → コミット
✓ perf テストが通った → コミット
✗ 「とりあえず途中まで」はコミットしない
```

何かおかしくなったら次の順で疑ってください：

1. **マイクの音が WebSocket に乗っているか** — クライアントのコンソールで送信バイト数を確認
2. **VAD が反応しているか** — サーバーログで `state` 遷移を確認
3. **STT がテキストを返しているか** — transcript イベントを確認
4. **LLM がトークンを返しているか** — Ollama の生ログを直接見る
5. **TTS が音声を返しているか** — irodori-tts のログを確認

**「全体が動かない」と言わずに、どこで止まっているかを特定してから報告してください**。

## 既存コードの参考資料

この設計は次の過去の経験を踏まえています：

- **Unity 版（3 年前）**: `MyAIRoomScript.cs` 相当の実装。音量閾値 VAD、OGG エンコード、
  REST 一括 API という構成だった。今回はその逆をやる。
- **api.py（3 年前）**: OGG → MP3 変換を挟む苦肉のサーバー実装。今回はこの種の変換を
  一切しない（float32 を生で流す）。
- **Zettelkasten プロジェクト**: PostgreSQL の pgvector + PGroonga 利用ノウハウ。
  記憶層の実装で活用する。
- **過去の Tomoko 人格プロンプト**: 感情状態を持つキャラクター設計。
  `prompts/base_persona.md` のベースに使う。

## 質問

不明点があれば実装を進める前に確認してください。
「とりあえずこう書きました」よりも「ここどうしますか？」の方が歓迎されます。
