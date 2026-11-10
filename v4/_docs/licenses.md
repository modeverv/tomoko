# 部品のライセンス一覧

作成: 2026-11-10 / Claude Code（Fable 5.2）。読んだ日はすべて 2026-11-10。
これは作成者の読み取りで、法的な助言ではない。

## 前提

- v4 は、重み・音声・外部プログラムを**同梱しない**。利用者の手元で取得し、実行する（ユーザーの決定、2026-11-10）。
- そのため、コードを公開することへの支障は、下の表のどの部品にもない。
- 残るのは**使う人の側の条件**である。README に書く。

## 一覧

| 部品 | 対象 | ライセンス | 使う人の側の条件 | 読んだ場所 |
|---|---|---|---|---|
| MaAI | コード | MIT | なし | GitHub `MaAI-Kyoto/MaAI` の LICENSE、README |
| MaAI | `vap_jp`（`mode="vap", lang="jp"` の既定） | CC BY-NC-ND 4.0 | **非商用のみ**。改変した重みの配布は不可 | Hugging Face API のタグ |
| MaAI | `vap_jp_kyoto`、`vap_mc_jp_kyoto` | MIT | なし | 同上 |
| MaAI | `vap_bc_jp`、`vap_nod_jp`、`vap_3party_jp` | MIT | なし | 同上 |
| MaAI | `vap_bc_2type_jp`（相づち 2 種） | **記載なし**（カードもタグもない） | 条件が不明。商用で使えるとは書けない | Hugging Face API |
| MaAI | `vad_jp`、`bc_det_jp` | CC BY-NC-ND 4.0 | 非商用のみ | Hugging Face API のタグ |
| MaAI | `continuous-mimi-onnx`、Mimi | CC BY 4.0 | 出典の表記 | Hugging Face API、MaAI README |
| Silero VAD | コードと重み | MIT | なし | GitHub `snakers4/silero-vad` の LICENSE。README のバッジの代替文字に「CC BY-NC 4.0」が残っているが、LICENSE 本文は MIT |
| Whisper | コードと重み（`openai/whisper-large-v3-turbo`） | MIT | なし | GitHub `openai/whisper` の LICENSE、Hugging Face API のタグ |
| mlx-whisper | コード | MIT | なし | GitHub `ml-explore/mlx-examples` の LICENSE |
| Whisper の MLX 変換 | `mlx-community/whisper-large-v3-turbo` | **カードに記載なし** | 元のモデルは MIT。変換版のカードは無記載 | Hugging Face のカード |
| Kokoro-82M | 重み | Apache 2.0 | なし | Hugging Face のカードとタグ |
| Kokoro | 日本語の声 | `jf_gongitsune`、`jf_nezumi`、`jf_tebukuro`、`jm_kumo` は CC BY の朗読データ（koniwa）由来。`jf_alpha` は出典の記載なし | CC BY 由来の声は出典の表記 | `VOICES.md` |
| misaki | コード | Apache 2.0 | なし | GitHub `hexgrad/misaki` の LICENSE |
| pyopenjtalk | コード | MIT | なし | GitHub `r9y9/pyopenjtalk` の LICENSE.md |
| Open JTalk と辞書 | コード、辞書 | 修正 BSD（検索結果経由。**原本は未読**） | 未確認 | 未読 |
| VOICEVOX エンジン | コード | LGPL v3 と、別ライセンスの二本立て | 外部のプログラムとして呼ぶ分には LGPL の条件は v4 のコードに及ばない（作成者の読み取り） | GitHub `VOICEVOX/voicevox_engine` の README |
| VOICEVOX コア | コードとビルド成果物 | MIT（0.16 未満のビルド済みは別ライセンス） | — | GitHub `VOICEVOX/voicevox_core` の README |
| VOICEVOX | ソフトウェアの利用規約 | 商用・非商用とも可 | クレジット表記が必要。無断の再配布は禁止。作った音声は各キャラクターの規約に従う | https://voicevox.hiroshiba.jp/term/ |
| VOICEVOX | キャラクターごとの規約 | **未読** | 音声を公開する前に読む | — |
| Apple のシステム音声（`say`、AVSpeechSynthesizer） | 音声 | macOS の使用許諾 2.F 項 | ソフトウェアの実行中の使用と、個人的・非商用の自作物に限る。**録音の公開・再配布は、営利・非営利・公開共有のいずれでも不可** | https://www.apple.com/legal/sla/docs/macOSTahoe.pdf |
| Apple Speech（SpeechAnalyzer） | OS の機能 | macOS の使用許諾 | 手元の実行のみ。条項は個別に読んでいない | 未読 |
| Gemma 4 | 重み | Apache 2.0（モデルカード） | なし | `v3/_docs/v0/sources.md` の調査（今回は読み直していない） |
| Full-Duplex-Bench | コード | CC BY-NC 4.0 | **参考にするだけで、コードを写さない**。定義から書き直す | GitHub `DanielLin94144/Full-Duplex-Bench` の LICENSE |
| Claude API | 採点役 | 有償のサービス | 送るのは公開データと合成データの書き起こしだけ | Anthropic の文書 |

## 既定の構成への影響

- **言い終わりの予測**: 個人利用の既定は `vap_jp` でよい。商用の利用者向けに `vap_jp_kyoto`（MIT）へ切り替える設定を置く。精度の差は未測定（P3 で測る）。
- **相づち**: `vap_bc_2type_jp` は条件が不明。`vap_bc_jp`（MIT）へ切り替える設定を置く。
- **ベンチの入力音声**: Apple の音声で作った音声ファイルは公開できない。Git に入れるのは台本と生成スクリプトだけにする。公開用のデモやベンチの音声が要るときは Kokoro を使う。
- **TTS**: 公開物の説明で既定とするのは Kokoro。VOICEVOX と Apple の音声は、利用者が自分で入れる任意の構成。

## 読めていないもの

- Open JTalk と辞書の LICENSE 原本。
- VOICEVOX のキャラクターごとの規約。
- `vap_bc_2type_jp` の条件（カードがない。必要なら京都大学の井上氏に問い合わせる）。
- Apple Speech の条項。
