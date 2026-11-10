"""V0-2: 上書き実験の Moshi 音声を Whisper で書き起こし、狙った文との文字誤り率（CER）を出す。

機械の目安であり、自然に聞こえるかの判定ではない（人が聴いて判定する）。
uvx --python 3.12 --from mlx-whisper python scripts/v0/asr_check.py <force json>
"""

import json
import re
import sys
from pathlib import Path

import mlx_whisper

MODEL = "mlx-community/whisper-large-v3-turbo"


def norm(s: str) -> str:
    return re.sub(r"[\s、。！？!?,.・ー〜]", "", s)


def cer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hc in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rc != hc))
    return d[len(h)] / max(1, len(r))


def main() -> None:
    src = Path(sys.argv[1])
    data = json.loads(src.read_text())
    rows = []
    for t in data["trials"]:
        wav = t["wav"].replace(".wav", "-moshi.wav")
        hyp = mlx_whisper.transcribe(wav, path_or_hf_repo=MODEL, language="ja")["text"]
        stream = t["text_stream"]
        row = {"mode": t["mode"], "index": t["index"], "target": t["target"], "asr": hyp,
               "cer_vs_target_substring": None, "cer_vs_text_stream": cer(stream, hyp)}
        if t["mode"] != "none":
            # 置換前に Moshi 自身が話した部分は除いて、狙った文が含まれるかを見る
            tail = hyp[-max(len(t["target"]) + 4, 1):]
            row["cer_vs_target_substring"] = min(cer(t["target"], hyp[k:]) for k in range(0, max(1, len(hyp) - len(norm(t["target"])) // 2)))
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    out = src.with_name(src.stem + "-asr.json")
    out.write_text(json.dumps({"model": MODEL, "source": str(src), "rows": rows}, ensure_ascii=False, indent=1))
    print(out)


if __name__ == "__main__":
    main()
