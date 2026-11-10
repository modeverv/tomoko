"""V0: 別プロセスで Whisper に書き起こしを続けさせる負荷（STT 同時実行の条件）。

実際の非同期 STT は発話ごとにしか動かないので、これは上限側の負荷として扱う。
  uvx --python 3.12 --from mlx-whisper python scripts/v0/load_stt.py --seconds 330
"""

import argparse
import glob
import json
import time

import mlx_whisper

MODEL = "mlx-community/whisper-large-v3-turbo"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=330)
    ap.add_argument("--audio", default="work/audio/u*.wav")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()
    files = sorted(glob.glob(args.audio))
    end = time.time() + args.seconds
    log = open(args.log, "a") if args.log else None
    print("load ready", flush=True)
    i = 0
    while time.time() < end:
        t0 = time.time()
        mlx_whisper.transcribe(files[i % len(files)], path_or_hf_repo=MODEL, language="ja")
        if log:
            log.write(json.dumps({"t": t0, "file": files[i % len(files)], "wall_s": time.time() - t0}) + "\n")
            log.flush()
        i += 1


if __name__ == "__main__":
    main()
