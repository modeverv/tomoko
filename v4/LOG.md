# LOG.md — Tomoko v4

追記のみ。

## 2026-11-10 セッション1（Claude Code / Fable 5.2）

### やること（開始時に書く）
- ユーザーの指示「P0 から始め、人間の作業が必要なところまで進める」に従う。
- 読んだもの: v4/PLAN.html、v4/ARCHITECTURE.html、AGENTS.md、_docs/knowlaedge.md の遅延とMaAI の節、Drive の project-software-tomoko の直近イベント。
- 採点役は Claude の API（`ant auth login` 済み、組織 norainu、Max の API クレジットを紐付け済み。ユーザー報告）。

### 終わり（2026-11-10、同じセッション）
- P0: `README.md`、`pyproject.toml`、`_docs/licenses.md`、`_docs/carry-over.md` を作った。関門（ライセンスの一覧）は通った。
- P1: `bench/` を作った（シナリオ、入力の生成、再生の規則、実行、採点、偽のシステム 4 つ、報告）。小の組 55 件の台本を書き、入力を作った。
- 試験: 単体 49、結合 4、すべて通る。`ruff check` も通る。
- 偽のシステムで検査した（`_docs/bench-definitions.md` の 7 節）。期待どおりの点になった。
- 関門の条件「遅れの誤差が VAD の刻み以内」は、厳密には満たしていない（+0.022〜+0.047 秒、刻みは 0.032 秒）。繰り返しの一致は 0.032 秒以内。
- 詰まったこと: 偽のシステム 4 つを同時に起こすと、`say` の作業ファイルが同名でぶつかった → プロセス番号を名前に入れ、置き換えで保存するようにした。
- 詰まったこと: 偽の送り手が実時間ちょうどで送ると、再生に数サンプルの隙間ができた → 2 断片ぶん先に送るようにした。
- まだないもの: 全の組（各軸 50 件）、C3 の台本、本人の録音の組。
- 人間に頼むこと: 合成の入力を数件聴く。遅れの誤差を受け入れるか決める。

### 人間の聴取（2026-11-10）
- ユーザーが合成の入力 5 件（t2-04、t2-09、t4-04、t3-01、t5-01）を聴き、「このまま使える」と答えた（user-reported）。
- 未回答: 遅れの誤差（+0.022〜+0.047 秒）を受け入れるか。P2 へ進むか。

## 2026-11-10 セッション1 の続き: P2（Claude Code / Fable 5.2）

### やること
- ユーザーの決定: 遅れの誤差の条件を 0.05 秒以内に改める。P2 へ進む。LLM のサーバーはエージェントが起こす。
- いちばん単純な通しの経路（VAD 400 ms → STT → E4B → Kokoro）を作り、小の組を 3 回測る。

### 終わり（2026-11-10、P2）
- `engine/`（VAD、STT、手前の LLM、Kokoro、基準線の会話、`/ws`）と `client/`（ブラウザのページ）を作った。単体試験 8 件を足した。
- サーバーを起こした: tmux `v4-llm`（`mlx_lm.server`、E4B、8090 番）、tmux `v4-engine`（8700 番）。v3 の 8082 番には触れていない。
- 小の組 55 件を 3 回流し、採点した。接続の失敗 0 件。結果は `_docs/baseline.md`。
- T1 の遅れは中央値 2.1〜2.3 秒。内訳でいちばん大きいのは TTS の最初の句（0.7〜0.9 秒）。
- STT の途中認識を測った（`scripts/stt_partial_bench.py`、`_docs/data/stt-partial-*.json`）。この Mac 1 台では turbo は 250 ms の刻みに間に合わない。
- 気づき: Kokoro の出力を Whisper で書き起こすと、ガ行などが落ちている（「ながら」→「なあら」）。人間の聴取はまだ。
- 人間に頼むこと: 基準線の報告を読み、P3 の目標の数字と順番を決める。ブラウザのページをヘッドセットで試す。Kokoro の発音を聴く。STT を 3060 に出す案を P3 に入れるか決める。

## 2026-11-10 セッション1 の続き: P3（Claude Code / Fable 5.2）

### やること
- ユーザーの決定: TTS を VOICEVOX（春日部つむぎ）に替える。順番は「最初の句を短く → 頭の無音を切る → 言い終わりを MaAI に → 割り込みで止める」。STT と LLM を RTX 3060 に出す（LAN に流れてよい）。
- ユーザーの依頼: MBP（192.168.11.34）にある非同期の VOICEVOX が使えるか確かめる。

### 終わり（2026-11-10、P3 の途中）
- 足したもの: `engine/speaker/tts_voicevox.py`（ふつうの合成と逐次合成）、`engine/sensors/stt_remote.py`（whisper.cpp）、設定 `first_cut_min_chars`・`tts_pre_silence_s`・`voicevox_streaming`・`barge_in`、割り込みで止める経路（`engine/baseline.py`）。既定はどれも切。単体試験 75 件。
- 測ったもの: 遅れの 6 構成（20 件 × 3 回）、55 件を止めない／止めるで各 3 回、MaAI の下調べ。結果は `_docs/p3-results.md`。
- T1 の遅れ中央値: 基準線 2.1〜2.3 秒 → 逐次合成 + 割り込みで 1.27〜1.36 秒。T4 は 10 件とも止まり、停止の遅れ 0.65〜0.68 秒。T5 の遮り 42 → 0。
- 悪化: 相づちで止まる回が 10 件中 3 件（T3）。
- 起こしたもの: tmux `v4-voicevox`（50021）、`v4-tunnel`（3060、18090/18091）、`v4-tunnel-mbp`（MBP、50122）。MBP では PR 1823 版の VOICEVOX を nohup で起こした（127.0.0.1:50122、ログ `~/tomoko-v4-logs/voicevox-pr1823.log`）。MBP の設定は変えていない。
- 無効: p3-5（STT を 3060 に）。3060 が 20:20 ごろから応答しない。
- 人間に頼むこと: 3060 を起こす。MaAI の手順を入れるか決める。相づちで止まる 3 件の扱いを決める。VOICEVOX の声を聴く。P3 の目標の数字を決める。

## 2026-11-13 セッション2: P3 の続き（Claude Code / Opus 5.5）

### やること（開始時）
- ユーザーの決定（2026-11-13 未明）: 3060 を寝かせない。MaAI を先に進める（台本を変え、MaAI の効き目が測れるようにする）。相づちは体験にとても重要。相づちで止まる 3 件は、声の長さ以外の手がかりを 1 つずつ測る。VOICEVOX は当面 MBP2021（Wi-Fi、.36）。P3 の目標は数字ではなく変更の順番。LAN 内の計算資源は自由に使ってよい。ユーザーは就寝中で、しばらく人間の助けはない。
- サブエージェントは Claude 以外（Codex gpt-6.1-sol max、agy gemini-4-argon-high、dsh）を並べて使う（AGENTS.md の 2026-11-13 の追記）。dsh の headless は HTTP 404 で使えていない。
- 測る: p3-5b（STT を 3060 に、20 件 × 3）、p3-9-lan（LLM・STT は 3060、TTS は MBP の逐次合成、割り込みあり、55 件 × 3）。
- MaAI: Codex と agy で事実を調べ、互いに確かめ、独立に設計して批判し合う（ワークフロー）。並べて、台本の声の作り方（1 回の合成で無音を入れる）を Codex、台本の新しい版 small-v2 を agy が書く。
- 起こしたもの: tmux `v4-awake`（3060 を寝かせない）、`v4-tunnel-3060`、`v4-tunnel-mbp`、`v4-watch`（1 分ごとの見張り）、`v4-caffeinate`（この Mac を寝かせない）。手順は scripts/lan_upkeep.sh、記録は work/lan/。

## 2026-11-13 セッション2（Codex）: ベンチ入力の 1 回合成

### やること（開始時に書く）
- ユーザーの依頼に従い、P1 の入力生成へ `--one-pass` を追加する。P3 の MaAI 検証用に、文の途中の区切りごとに合成を切らず、`[[slnc ms]]` を埋めた 1 回の合成を選べるようにする。
- 単体テストを先に追加し、従来の音声・注釈とキャッシュの互換性、実音声由来の注釈、区切りの長さの検証、古い Manifest の読み込みを確かめる。
- 作業範囲は v4/。コミット、uv sync、ネットワーク、モデルの読み込みは行わない。Drive は今回のネットワーク禁止に従い未取得・未保存。
- 開始時点の Git 差分はなし。v4 の MEMORY.md、LOG.md、README.md、PLAN.html、ARCHITECTURE.html と過去世代の関連記録を確認した。
- 変更前の `.venv/bin/python -m pytest -q tests/unit` は `httpx` 不足で 4 ファイルの収集に失敗。依存を取得せずに利用可能なローカル環境を確認する。

### やったこと
- 先に `tests/unit/test_generate.py` にテストを追加し、17 件失敗・既存 1 件成功を確認してから実装した。端数を持つ要求長の回帰テストも、修正前の失敗を確認した。
- `synthesize_one_pass`、共通キャッシュ処理 `_synthesize`、音量による `annotate_one_pass` を追加。`build` と CLI の `--one-pass`、`Manifest.one_pass=False` と古い JSON の読み込みを実装した。
- 生成の単体試験は既存 1 件に 22 件を追加。埋め込み文字列・say 呼び出し回数・キャッシュ分離・出力形式・注釈・長さの下限・無音の候補不足・従来 WAV のバイト一致・CLI・古い Manifest を検証した。
- README.md、ARCHITECTURE.html、MEMORY.md を更新し、注釈のみの軽い処理時間を `_docs/latency.md` に記録した。会話エンジンや設定・依存宣言・uv.lock は変えていない。
- Codex（gpt-6.1-sol / max）にコードと合成データのみの読み取りレビューを依頼。要求長の下限の切り上げを修正した後、残る確定問題なしとの報告を受けた。

### 詰まったこと・解決したこと
- `httpx` と依存は、同じ機械の既存 v4 環境からこの worktree の `.venv` にコピーして補った。ネットワーク・uv sync は使っていない。
- 実際の say は t5-01（Sandy）1 件だけ CLI で試したが、サンドボックスの `/usr/bin` の権限エラー後に無音になった。実音声の注釈は未確認。失敗記録は `work/one-pass-smoke-wp5xysol/result.json`。
- 25.2 秒の人工波形の注釈は 1 回の計測で 1.389 ms。`work/one-pass-smoke-wp5xysol/annotation-timing.json` に境界と計測値を保存した。実音声の精度や会話の遅れの検証とは分ける。

### 最終検証
- `.venv/bin/python -m pytest -q tests/unit`: **97 passed in 0.47s**。既存の pytest-asyncio の loop scope 未指定の警告は残る。
- `.venv/bin/ruff check .`: **All checks passed!**
- `git diff --check`: 成功。
- コミット・マージ・push はしていない。Google Drive は今回のネットワーク禁止に従い、未取得・未保存。

### 次のセッションでやること
- 権限が使える実行環境で t5-01 を 1 件再生成して、実音声の注釈と自然な間を確認する。MaAI の評価や測定中のエンジンは今回の作業対象外。

## 2026-11-13 セッション2（Codex）: MaAI のオフライン評価の拡張

### やること（開始時）
- ユーザーの依頼に従い、生成済み入力の評価、vap / bc / bc_2type / bc_det の集計を追加する。実運用への組み込みとコミットはしない。
- MaAI を読み込まない単体テストを先に追加し、tests/unit 全体と ruff を通す。
- パッケージのチャンネル順と出力をコードで確認する。コード索引ツールは承認拒否のため、ファイルの直接参照へ切り替える。
- 余力があれば small の入力を各モード一巡だけ評価する。torch は 2 スレッド、推論は直列、1 フレームごとに受信する。
- work/ は本体の共有ディレクトリへのリンクなので読み取りだけに使い、出力はこの worktree の _docs/data/ に置く。

### やったこと
- tests/unit/ の先行テストで AUC・分位点・無音と声の区間・T3/T4 の末尾選択を検証してから実装した。
- scripts/maai_offline.py を生成入力と4モードに対応させ、入力の処理は bench/maai_input.py、モデルに依存しない集計は bench/score/maai.py に分けた。
- 偽モデルで、チャンネル順・出力の軸・1フレームずつの回収・サンプル時刻・旧 run の JSON と helper API の互換性を検証した。
- Codex（gpt-6.1-sol、推論 max）にパッケージコードの調査とレビューを依頼した。コードだけを渡し、本人の録音・実会話・私的なログは渡していない。
- レビューの指摘を反映し、録音末尾以後の vap の遅れを欠測にした。実際のモデル variant を JSON に残し、生成注釈の voiced の意味を文書に明記した。
- _docs/maai-offline.md に使い方・集計の定義・解釈の制約を、_docs/latency.md に未測定の理由を記録した。

### 検証結果と制約
- .venv/bin/python -m pytest -q tests/unit: 99 passed（0.53秒）。既存の pytest-asyncio の fixture loop scope 未指定の警告あり。
- .venv/bin/ruff check .: All checks passed。
- 実測の準備で BC / BC-Det の重みが未取得と分かった。書き込み先を一時ディレクトリに限定して BC の取得を試したが、huggingface.co の名前解決に失敗した（Errno 8 / LocalEntryNotFoundError）。指示どおり停止し、MaAI 推論は1件も実行していない。
- _docs/data/maai-offline-v1inputs.json は作成していない。コミット・共有 work/ への書き込みはしていない。

### 次のセッションでやること
- 必要な重みが取得できる環境で small を vap,bc,bc_det の順に直列で一巡だけ評価する。small-v2 も同じ定義で測り、音声の生成方法と区間定義の違いを区別して比較する。

### 共通記録の保存状況
- Drive の START-HERE.org と関連する Tomoko のイベントを取得した。snapshots フォルダの検索結果は0件だった。
- 新規 org イベントを作成し、events フォルダへ text/plain でアップロードしようとしたが、ツールが「MCP tool call requires approval, but approval policy is never」と拒否した。Drive は未保存で、読み戻しの検証も未実施。
- org 下書き: /private/tmp/20261113T013928-f65c5b2b-8029-437d-bad5-a5d2bf0388b2.org（作成者 Codex、原典と試験結果と実測中止の理由を記載）。

## 2026-11-13 セッション3（Codex）: MaAI の実時間センサー

### やること（開始時に書く）
- P3 のセンサー部品として MaaiSensor と MaaiFrame を追加する。マイクの時計に Tomoko の音を合わせ、同期 process を専用スレッドで呼び、最新値・通知・待機フレームの破棄数を公開する。
- MaAI / torch を読み込まない偽モデルの単体テストを先に書く。指定の tests/unit と ruff を検証し、モデルの読み込み・実推論・ネットワーク・uv sync は行わない。
- 作業開始時の Git 差分はなし。v4 の MEMORY.md / LOG.md / README.md / PLAN.html / ARCHITECTURE.html と過去世代の関連記録を確認した。コード索引は承認ポリシーで拒否されたため直接参照する。
- Codex（gpt-6.1-sol、推論 max）に公開パッケージの静的 API 調査を並行依頼。コードだけを渡し、本人の録音・実会話・私的なデータは渡さない。
- 変更はこの worktree の v4 内だけ。ws / baseline / EngineConfig は変更せず、コミットしない。今回の明示指示に従い Drive は未取得・未保存。

### やったこと
- tests/unit/test_engine_maai_sensor.py を先に追加し、未実装の MaaiFrame の import 失敗を確認してから実装した。工場の fake import 試験も実装より先に追加した。
- engine/dto.py に frozen / slots の MaaiFrame、engine/sensors/maai.py に MaaiSensor と make_maai_multiple を追加した。音声の予約と補間は内部用 _maai_audio.py に分けた。
- 任意長入力の分割、24→16 kHz・時刻・連結・停止、Multiple の部分辞書・ユーザー軸、初回結果なし、過負荷の破棄と累計、asyncio 通知、終了を合成波形だけで検証した。
- Codex（gpt-6.1-sol / max）のレビューで判明した、欠落後の文脈継続・close 後の通知・一次障害の上書きを回帰テストで失敗させてから修正した。未着 TTS の境界は未来を待たない規約と交互投入テストを追加し、最終レビューで残る公開契約違反は見つからなかった。
- README.md、ARCHITECTURE.html、_docs/maai-sensor.md に API と検証範囲を記録した。LOG.md / MEMORY.md は追記のみ。EngineConfig の項目・既定・環境変数の挙動は変更していない。dto.py への future import に伴い from_env の戻り値注釈の引用符だけ外して ruff に合わせた。

### 詰まったこと・解決したこと
- 変更前の tests/unit は既存の httpx 不足で4ファイルの収集に失敗した。この worktree の .venv に、指定された p3wt の既存 .venv から httpx / httpcore / anyio / certifi / idna / h11 の不足分だけをコピーして補った。元環境・依存宣言・uv.lock は変更せず、ネットワーク・uv sync も使っていない。
- Mimi が同期 process の初回に結果を返さないことを静的に確認し、空の結果キューを待たず次の入力へ進む。Multiple の mode ごとの部分辞書も確認できた。BC の意味として正しいチャンネル順と、実モデルの実時間性能は未確定。
- 偽モデルを待機させた状態の feed_mic 1回は0.023458 ms、破棄0件。_docs/latency.md に条件と限界を記録した。MaAI の実推論や E2E の計測値ではない。

### 最終検証
- `.venv/bin/python -m pytest -q tests/unit`: **155 passed in 0.58s**（追加25件）。既存の pytest-asyncio の loop scope 未指定の警告は残る。
- `.venv/bin/ruff check .`: **All checks passed!**
- `git diff --check`: 成功。実 MaAI / torch の import、重みのロード、実推論、ネットワーク、エンジンへの接続、コミットは行っていない。Drive はユーザー指示により未取得・未保存。

### 次のセッションでやること
- 別担当が利用方針と EngineConfig を定めてから組み込む。遅延測定と重ならない機会に、実 MaaiMultiple の3モード・チャンネルの意味・warmup / reset・CPU 負荷を検証する。

### 途中経過（2026-11-13 06:20、Claude Code / Opus 5.5）
- 02:20〜05:41 は、Claude の利用枠の上限で止まっていた（サブエージェントと測定は、その間に終わっていた）。
- 測った: p3-5b（STT も 3060）、p3-9-lan（LAN に分けた構成、55 件 × 3）。T1 の遅れ中央値 1.05〜1.07 秒。悪化: T5 で返事の切れ端が 1 分に 6〜8 回。結果は _docs/p3-results.md の 7 節。
- MaAI: Codex と agy の調べ・確かめ・設計・批判（ワークフロー）。センサー（Codex）、評価の道具（Codex）、台本の第 2 版（agy）、採点の指標（agy）、1 回合成（Codex）、組み込み（Claude Code）。組み込みは Codex と agy に読ませて直した。オフラインの評価と 3 件の試運転の結果は 8 節。
- 測定を流している: scripts/p3_maai_queue.sh（p3-10-v2、p3-11-bc、p3-13-guard。言い終わりの段は飛ばした）。終わりは 09:40 ごろ。
- Codex に、言い終わりの判定のための CPC の vap（2 つ目のセンサー）を頼んでいる（worktree wt-ep）。
- dsh の headless は HTTP 404 で使えなかった（人間に確認する）。

## 2026-11-13 セッション4（Codex）: CPC vap の追加

### やること（開始時に書く）
- ユーザーの実装依頼に従い、P3 の言い終わり用 CPC vap を Mimi の相づち用センサーと併用する。`make_maai_single`・`CompositeMaai`・既定 False の `maai_vap_cpc` と起動／接続の配線を追加する。`baseline.py` は変更しない。
- MaAI / torch を読み込まない単体テストを先に書き、フレームの統合・欠測・委譲・終了の失敗・工場の引数・起動と接続の配線を確かめる。指定の tests/unit と ruff を検証する。
- 変更はこの worktree の v4 内だけ。ネットワーク・Drive の取得／保存・uv sync・実モデルの読み込み・実推論・コミットは行わない。同じ機械で遅延測定中のため実モデルの性能は測らない。
- 作業開始時の Git 差分はなし。現行 MEMORY.md / LOG.md / README.md / PLAN.html / ARCHITECTURE.html と過去世代の関連記録を確認した。索引ツールは承認ポリシーで拒否されたため、コードはファイルを直接参照する。
- Codex（gpt-6.1-sol、推論 max）へ公開コード相当の静的 API 調査とレビューを並行依頼する。本人の録音・実会話・私的な記録は渡さない。

### やったこと
- 新規32件の単体テストを先に追加し、32件失敗・既存25件成功を確認してから実装した。対象は CompositeMaai の統合・欠測・古い時刻・委譲・破棄数・error・終了の多重障害、単体 factory の引数、単体 CPC の平坦出力、既定設定と環境変数、起動／接続の配線。
- `make_maai_single`・`CompositeMaai` と既定 False の `EngineConfig.maai_vap_cpc` を追加した。起動時は CPC vap を別の SharedMaai にし、接続ごとに Mimi と合わせて渡す。両センサーの入力は ch1=ユーザー、通知は session.on_maai。baseline.py は変更していない。
- 単体 Maai と Multiple で出力辞書の形が違う点を偽モデルで再現してから対応した。vap だけを指定した場合は、空の Multiple を作らず CPC だけをつなぐ。
- ARCHITECTURE.html と _docs/maai-sensor.md に API・配線・欠測と終了の契約を反映した。MEMORY.md と _docs/latency.md に確認事項と未測定の範囲を追記した。
- 独立 Codex（gpt-6.1-sol / max）の静的レビューで、要修正の指摘はなかった。

### 検証結果
- 関連4ファイル: **57 passed in 0.72s**。
- `.venv/bin/python -m pytest -q tests/unit`: **216 passed in 2.12s**。既存の pytest-asyncio の fixture loop scope 未指定の警告あり。
- `.venv/bin/ruff check .`: **All checks passed!**。
- `git diff --check`: 成功。変更は v4 内だけで、LOG.md / MEMORY.md は追記のみ。
- 実 MaAI / torch の読み込み・実推論・性能計測・ネットワーク・uv sync・コミットは行っていない。ユーザーの明示指示により Drive は未取得・未保存。

### 次のセッションでやること
- 測定中の処理に重ならない機会に、別担当が実モデル2個の同時推論時の負荷・破棄数・予測精度・E2E 遅延を検証する。今回の単体試験を実体験の改善実績にはしない。


## 2026-11-13 セッション5（Codex）: Orca 引き継ぎ、P3 MaAI 比較測定の回収

### やること（開始時に書く）
- 前 Claude セッションは参照専用。07:09 に p3-10-v2 が完了し、p3-11〜13 のキューを確認したところで利用枠に達した。現在の HEAD 0e35605 と作業ツリーを照合して引き継ぐ。
- 再開時（08:42 JST）は p3-10-v2 と p3-11-bc が各55件×3回完了、p3-12-ep の2回目が実行中。既存キューとLAN維持処理を重複起動しない。v3 の未コミット差分は対象外。
- 完了した合成ベンチ結果とトレースを集計し、残りを回収して p3-results.md / latency.md / PLAN.html に追記する。MaAI の採用と P3 完了は自動認定しない。
- Codex（gpt-6.1-sol、推論 max）へコードの静的レビューと合成結果の読み解きを並行依頼する。本人の音声・実会話・私的なデータは渡さない。
- Drive START-HERE.org と既存進捗イベント aba2dd98-2fe5-40f9-b019-4e2d0c941d49 を取得。snapshots の検索結果は0件。org-agenda.txt は2026-11-02更新の保存時点の一覧で、Tomokoの項目なし。新規報告は作成者・原典を付けて保存後に再取得する。

### 引き継ぎ中の途中記録（2026-11-13 09:38 JST）
- 元キューPID68131が継続中。p3-10-v2 / p3-11-bc / p3-12-ep は各3回採点済み（9/12組）、p3-13-guard-1を収録中。元のコードは変えていない。
- 相づちの再生待ち時間の修正は隔離ブランチ modeverv/p3-bc-playback-fix、コミット060cd209（Codex）。新規7件を修正前に失敗させ、unit223件・ruff・独立レビューで確認。現在の比較が全て終わるまでmasterへ取り込まない。
- 作業補助は /private/tmp/tomoko-p3-recovery/。collect.pyが既存の合成結果だけを集計し、online-summary.jsonへ保存する。render_report.pyの下書きはreport-section.md。progress.jsonは監視用。これらは現時点で途中成果。
- 計660実行の回収後、結果をv4/_docs/data/とp3-results.mdへ保存し、修正をマージして6件の合成smokeを実施する。既存入力の参照は同tempのsmoke-inputs。Driveの新規報告は最終確認後に作成・再取得する。
- p3-12-epのT5遮りは3/2/2。2回目は395、3回目は129フレーム破棄。原因未確定で、閾値や常用設定は変更していない。詳しくはMEMORY.mdの今回の追記。

### 比較測定の回収完了
- 4構成×55件×3回、計660実行のresults/reportを回収し、各runとtraceの全STT列・停止世代列を照合した。音声や会話本文は集計資料に保存していない。
- _docs/p3-results.md の9節、_docs/data/p3-maai-online-20261113.json、_docs/latency.md、PLAN.htmlに結果を追記。旧報告の3段予定、VOICEVOX聴取未実施、相づちのはみ出しだけという説明を訂正した。
- 次に隔離ブランチ060cd209を取り込み、単体試験と7件の合成smokeを行う。P3完了とは判定しない。

## 2026-11-13 セッション5（Codex）: 相づち後の本返答の再生時刻

### やること（開始時に書く）
- P3 の静的レビューで、相づちの後ろにすぐ本返答を送ると、再生待ちの相づちの長さを本返答の終了見込みに含めない問題を確認した。相づち0.3秒と合成本返答0.1秒を Player に渡すと、0.329秒時点で本返答が鳴っていても `_playing()` は False だった。実ベンチで発生したという報告ではない。
- 独立 worktree `p3-bc-playback-fix` だけで、合成波形と偽部品の回帰テストを先に追加する。全音声の待ち行列の末尾と、本返答の再生中判定を分け、相づちだけでユーザーの発話を聞き流したり停止したりしない。
- 指定された root の v4/.venv の Python を使い、この worktree のコードを読み込んで unit と ruff を実行する。実行中の親のベンチ・プロセス・root のファイルは変更しない。モデル・本人の音声・実会話・ネットワーク・uv sync は使わず、マージも push もしない。

### やったこと
- 新規6件の合成テストを先に実行し、6件すべての失敗を確認した。Player と本返答の終了時刻の不一致、再生待ち中の短い声と bc_det の保留、実割り込みの stop、重複相づち、MaAI 音声通知失敗時の再生待ちを対象にした。
- 相づちを含む全音声の末尾 `_output_end` を追加し、本返答の終了見込みをそこへ合わせた。相づちだけでは `_playing()` を True にせず、本返答を停止したら両方の末尾を消す。
- 未完了の相づち送信を上書きする問題を、送信を待機させる7件目の先行テストで再現した。新しい相づちの前に送信タスクの終了も確認する。これにより close が元の未完了タスクを取り消せる。
- `ARCHITECTURE.html`・`MEMORY.md`・`_docs/latency.md` に契約・合成再現の条件・未検証範囲を追記した。既定設定、WS仕様、MaAI のモデル・チャンネル・鮮度判断は変更していない。

### 検証結果
- root の既存 v4/.venv/bin/python と、この worktree の cwd / PYTHONPATH を使った。`engine.baseline.__file__` がこの worktree を指すことを確認した。
- 初回6件失敗、追加の未完了タスク試験1件失敗を確認してから各修正を実施。最終 `python -m pytest -q -m unit -p no:cacheprovider tests/unit`: **223 passed in 1.72s**（新規7件）。既存の pytest-asyncio の fixture loop scope 未指定の警告は残る。
- root の既存 ruff で `ruff check --no-cache .`: **All checks passed!**。`git diff --check`: 成功。
- 本人の音声・実会話・モデル推論・追加の性能ベンチ・ネットワーク・uv sync は使っていない。Drive はこの担当では未取得・未保存。共通記録は親担当が扱う。

### 次のセッションでやること
- 親担当が元の長時間ベンチの終了後にこのブランチを統合し、相づちの直後に速い本返答が出る条件を実機で確認する。この担当から master へマージ・push は行わない。

### 終わり（2026-11-13 10:45、Claude Code / Opus 5.5）
- 測った（台本の第 2 版、55 件 × 3 回 × 4 段）: MaAI なし、相づち、+ 言い終わりの判定（CPC の vap）、+ 止まらない保留。結果は _docs/p3-results.md の 8.5 節。
- 相づちは、T5 の 86〜87% が台本の区切りの中に収まった。採点の「間に入った割合」は、返事の切れ端とまとめられて低く出る。
- 言い終わりの判定で T2 は改善（0.2〜0.4 → 0.1〜0.2）。T5 で本発話で遮った回数は増えた（0〜1 → 2〜3）。
- 止まらない保留は働かなかった（Tomoko の声があるとき、bc_det が 0.0）。
- 人間に頼むこと: dsh の headless の 404。agy のモデルと Codex の max / ultra。実際の人の声で相づちを聴く。8.6 節の次の手の順番。


### 引き継ぎ作業の完了（Codex）
- 元キューは10:40に終了。660実行を再実行せず回収し、8f23abfで比較報告を保存した。
- 060cd209を318180eでマージ。追記型3文書の競合は双方を保持して解消。unit223件・ruff成功。
- p3-14-playback-fix-smoke-1で合成7件を実機確認。7件とも音声出力あり、接続失敗・実行エラー・無応答・MaAI破棄0。判定の未解決問題は残る。
- 別Claudeの681eca8による同時更新を保存し、詳細報告は_docs/p3-maai-recovery-20261113.mdへ分離した。次の変更は隔離作業ツリーで準備し、元のClaudeセッションは操作していない。
- 独立Codexが数表276セル、660件のtrace対応、収集時コードSHAを照合してPASS。音声・会話本文は数値成果物に含めない。
- 次に人間と決めること: 終端判定と中断保留の方針、予測処理の安定性の調査範囲、本人の声での評価。P3は未完了。
