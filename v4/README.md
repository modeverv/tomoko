# Tomoko v4

既製の部品（STT、MaAI、LLM、TTS）と、新しく書く「指揮者」で作る、全二重の対話エンジン。
採点の仕組み（ベンチ）を先に作り、数字を見ながら進める。

- 構成: [ARCHITECTURE.html](ARCHITECTURE.html)
- 進め方: [PLAN.html](PLAN.html)
- 部品のライセンス: [_docs/licenses.md](_docs/licenses.md)
- ベンチの定義: [_docs/bench-definitions.md](_docs/bench-definitions.md)
- MaAI センサーの API と検証範囲: [_docs/maai-sensor.md](_docs/maai-sensor.md)

## 準備

```sh
cd v4
uv sync --group dev --group vad --group content
```

採点役は Claude の API を使う。`ant auth login` でサインインするか、`v4/.env` の `ANTHROPIC_API_KEY` に鍵を置く。

## ベンチの使い方

```sh
# 1. 台本から入力音声を作る（macOS の say を使う。音声は work/ に置かれ、Git に入らない）
uv run python -m bench.generate scenarios/small work/inputs/small

# 2. 対話エンジン（または偽のシステム）の /ws に流して録る
uv run python -m bench.fakes clockwork --port 8765 &
uv run python -m bench.run ws://127.0.0.1:8765/ws work/inputs/small work/runs/clockwork --jobs 8

# 3. 採点して表を出す
uv run python -m bench.score work/runs/clockwork
uv run python -m bench.report work/runs/clockwork --previous work/runs/前回
```

`--one-pass` を付けると、`[pause N]` を `[[slnc ms]]` に置き換え、1 発話を 1 回の
`say` で合成する。例:

```sh
.venv/bin/python -m bench.generate scenarios/small work/inputs/small-one-pass --one-pass
```

省略時の音声・注釈・キャッシュキーは従来どおり。生成した `scenario.json` の
`one_pass` が方式を示す（古いファイルで項目がなければ `false`）。1 回合成では、実際の
音量から注釈を作る。句点などのあとでは声の自然な間が区切りの無音に足されるので、区切りは
台本の長さまで無音の真ん中から縮める（v1 と無音の長さをそろえ、抑揚の違いだけを比べるため）。

## 注意

- `say` で作った音声は Apple の使用許諾により公開できない。Git に入れるのは台本と生成スクリプトだけ。
- 本人の録音と実会話の記録は、Git に入れず、クラウドに送らない。`private` の印が付いた組は、採点役に送られない。
