"""/ws の取り決め（ARCHITECTURE 4.9）。ベンチ側の写し。engine は読み込まない。

クライアントからサーバー:
  バイナリ  マイクの音。16 kHz、モノラル、float32 リトルエンディアン
  JSON      {"type": "playback", "generation": g, "played_s": 秒}
サーバーからクライアント:
  バイナリ  先頭 8 バイトが再生の世代と通し番号（uint32 x 2、リトルエンディアン）。
            続きは 24 kHz、モノラル、float32 リトルエンディアン
  JSON      {"type": "stop", "generation": g}  その世代以前の音を捨てる
            {"type": "text", ...} / {"type": "state", ...}  表示用。ベンチは記録するだけ
"""

import json
import struct

import numpy as np

_HEADER = struct.Struct("<II")


def pack_audio(generation: int, seq: int, samples: np.ndarray) -> bytes:
    return _HEADER.pack(generation, seq) + samples.astype("<f4").tobytes()


def unpack_audio(data: bytes) -> tuple[int, int, np.ndarray]:
    generation, seq = _HEADER.unpack_from(data)
    return generation, seq, np.frombuffer(data, dtype="<f4", offset=_HEADER.size)


def pack_mic(samples: np.ndarray) -> bytes:
    return samples.astype("<f4").tobytes()


def unpack_mic(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype="<f4")


def stop_message(generation: int) -> str:
    return json.dumps({"type": "stop", "generation": generation})


def playback_message(generation: int | None, played_s: float) -> str:
    return json.dumps({"type": "playback", "generation": generation, "played_s": round(played_s, 3)})
