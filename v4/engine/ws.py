"""/ws の受付。WebSocket はこの 1 本だけ。ブラウザもベンチも同じ口を使う。

    uv run uvicorn engine.ws:app --port 8700

取り決め（ARCHITECTURE 4.9）:
  クライアントから  バイナリ = マイクの音（16 kHz、モノラル、float32 LE）
                    JSON = {"type": "playback", "generation": g, "played_s": 秒}
  サーバーから      バイナリ = 世代と通し番号（uint32 LE x 2）+ 声（24 kHz、モノラル、float32 LE）
                    JSON = {"type": "stop", "generation": g} / {"type": "text", ...}
"""

import asyncio
import itertools
import json
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from engine.baseline import BaselineSession
from engine.dto import EngineConfig
from engine.front_llm import stream_reply
from engine.sensors.stt import WhisperStt
from engine.sensors.stt_remote import WhisperCppStt
from engine.sensors.vad import VadSensor, silero_prob
from engine.speaker.tts_voicevox import VoicevoxTts
from engine.trace import Trace

_HEADER = struct.Struct("<II")
_ids = itertools.count()


class Models:
    """重い部品。プロセスに 1 つ。MLX は 1 本のスレッドからだけ呼ぶ。"""

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg
        self.mlx = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")
        self.http = httpx.AsyncClient(timeout=60)
        self.transcribe = None  # 音 → 文字
        self.synthesize = None  # 句 → 声。TTS ごとに呼び方が違うので、ここでそろえる
        self.synthesize_stream = None  # 句 → 声の断片。逐次に返せる TTS だけ

    def load(self) -> None:
        if self.cfg.stt == "mlx":
            stt = WhisperStt(self.cfg.stt_model)
            stt.warm()
            self.transcribe = lambda audio: self.run(stt.transcribe, audio)
        if self.cfg.tts == "kokoro":
            from engine.speaker.tts_kokoro import KokoroTts

            kokoro = KokoroTts(self.cfg.tts_voice)
            kokoro.warm()
            self.synthesize = lambda phrase: self.run(kokoro.synthesize, phrase)

    async def start(self) -> None:
        await self.run(self.load)
        if self.cfg.tts == "voicevox":
            vv = VoicevoxTts(self.http, self.cfg.voicevox_url, self.cfg.voicevox_speaker,
                             pre_silence_s=self.cfg.tts_pre_silence_s)
            await vv.warm()
            self.synthesize = vv.synthesize
            if self.cfg.voicevox_streaming:
                self.synthesize_stream = vv.stream
        if self.cfg.stt == "whispercpp":
            remote = WhisperCppStt(self.http, self.cfg.stt_url)
            await remote.warm()
            self.transcribe = remote.transcribe
        if self.transcribe is None:
            raise ValueError(f"知らない STT: {self.cfg.stt}")
        if self.synthesize is None:
            raise ValueError(f"知らない TTS: {self.cfg.tts}")

    async def run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.mlx, fn, *args)


@asynccontextmanager
async def lifespan(app: FastAPI):
    models = Models(EngineConfig.from_env())
    await models.start()
    app.state.models = models
    yield
    await models.http.aclose()


app = FastAPI(lifespan=lifespan)


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    models: Models = socket.app.state.models
    cfg = models.cfg
    name = f"{time.strftime('%Y%m%dT%H%M%S')}-{next(_ids):04d}.jsonl"
    trace = Trace(cfg.trace_dir / name)
    trace.event("config", **{k: str(v) for k, v in vars(cfg).items() if k != "system_prompt"})

    async def send_audio(generation: int, seq: int, samples: np.ndarray) -> None:
        await socket.send_bytes(_HEADER.pack(generation, seq) + samples.astype("<f4").tobytes())

    async def send_json(obj: dict) -> None:
        await socket.send_text(json.dumps(obj, ensure_ascii=False))

    vad = VadSensor(silero_prob(), cfg.vad_threshold, cfg.vad_silence_s, cfg.vad_preroll_s,
                    cfg.vad_min_voiced_s, sustain_s=cfg.barge_in_voiced_s)
    session = BaselineSession(
        cfg, vad, trace,
        transcribe=models.transcribe,
        reply=lambda messages: stream_reply(models.http, cfg, messages),
        synthesize=models.synthesize, synthesize_stream=models.synthesize_stream,
        send_audio=send_audio, send_json=send_json,
    )
    try:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                session.on_mic(np.frombuffer(message["bytes"], dtype="<f4"))
            elif message.get("text") is not None:
                obj = json.loads(message["text"])
                if obj.get("type") == "playback":
                    session.on_playback(obj)
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[1] / "client", html=True))
