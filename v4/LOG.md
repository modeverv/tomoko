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
