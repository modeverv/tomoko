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
