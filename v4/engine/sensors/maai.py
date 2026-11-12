"""MaAI の同期 API を専用スレッドで駆動する。import だけではモデルを読み込まない。"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from math import isfinite
from queue import Empty, Queue
from typing import Any, Protocol

import numpy as np

from engine.dto import IN_SR, MaaiFrame
from engine.sensors._maai_audio import _TomokoAudio

log = logging.getLogger(__name__)


class _MaaiModel(Protocol):
    result_dict_queue: Queue[dict[str, Any]]

    def process(self, ch1: np.ndarray, ch2: np.ndarray) -> None: ...

    def reset_runtime_state(self) -> None: ...

    def stop(self, wait: bool = True) -> None: ...


def _to_frame(result: Mapping[str, Any], at_s: float, user_channel: int) -> MaaiFrame:
    """MaaiMultiple 0.2.27 の mode label ごとの辞書を、この 1 か所で解釈する。"""
    def pair(values: Any) -> tuple[float, float] | None:
        if values is None:
            return None
        if len(values) != 2:
            raise ValueError("MaAI stereo output must have two channels")
        return float(values[user_channel]), float(values[1 - user_channel])

    vap = result.get("vap", {})
    vad = pair(vap.get("vad", result.get("vad", {}).get("vad")))
    bc = result.get("bc", {}).get("p_bc")
    bc_det = pair(result.get("bc_det", {}).get("p_bc_det"))
    return MaaiFrame(
        at_s=at_s, p_now=pair(vap.get("p_now")), p_future=pair(vap.get("p_future")),
        p_bc=None if bc is None else float(bc),
        p_bc_det_user=None if bc_det is None else bc_det[0],
        vad_user=None if vad is None else vad[0], vad_tomoko=None if vad is None else vad[1],
    )


class MaaiSensor:
    """マイクと声の投入は受信ループから行う。推論中は入力用のロックを保持しない。

    user_channel はモデルへ渡す入力のユーザー側 index（0 または 1）。bc の向きの
    比較にも使う。公開する 2 要素の値は、この設定によらず (ユーザー, Tomoko)。
    model_factory は作業スレッド内で 1 回呼ぶ。必ず新しい同期専用モデルを返すこと。
    """

    def __init__(
        self, model_factory: Callable[[], _MaaiModel], *, frame_samples: int = 1280,
        user_channel: int = 0, max_pending_frames: int = 4,
        on_frame: Callable[[MaaiFrame], None] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if not isinstance(frame_samples, int) or frame_samples <= 0:
            raise ValueError("frame_samples must be positive")
        if not isinstance(max_pending_frames, int) or max_pending_frames <= 0:
            raise ValueError("max_pending_frames must be positive")
        if user_channel not in (0, 1):
            raise ValueError("user_channel must be 0 or 1")
        self._factory, self._frame_samples = model_factory, frame_samples
        self._user_channel, self._max_pending = user_channel, max_pending_frames
        self._on_frame = on_frame
        self._loop = asyncio.get_running_loop() if on_frame is not None and loop is None else loop
        self._condition = threading.Condition()
        self._pending: deque[tuple[int, np.ndarray, np.ndarray]] = deque()
        self._rest = np.empty(0, dtype=np.float32)
        self._frame_end = 0
        self._tomoko = _TomokoAudio()
        self._latest: MaaiFrame | None = None
        self._error: Exception | None = None
        self._dropped_frames = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="maai-sensor", daemon=True)
        self._thread.start()

    def _check_open(self) -> None:
        if self._error is not None:
            raise RuntimeError("MaaiSensor failed") from self._error
        if self._closed:
            raise RuntimeError("MaaiSensor is closed")

    def feed_mic(self, samples: np.ndarray) -> int:
        """16 kHz mono を足す。推論を待たず、今回捨てた待機フレーム数を返す。"""
        samples = self._audio(samples)
        dropped = 0
        with self._condition:
            self._check_open()
            audio = np.concatenate((self._rest, samples))
            offset = 0
            while len(audio) - offset >= self._frame_samples:
                end = self._frame_end + self._frame_samples
                mic = audio[offset:offset + self._frame_samples].copy()
                tomoko = self._tomoko.take(self._frame_end, self._frame_samples)
                self._pending.append((end, mic, tomoko))
                self._frame_end, offset = end, offset + self._frame_samples
                if len(self._pending) > self._max_pending:
                    self._pending.popleft()
                    dropped += 1
            self._rest = audio[offset:].copy()
            self._dropped_frames += dropped
            total = self._dropped_frames
            self._condition.notify()
        if dropped:
            log.warning("maai dropped_frames=%d total_dropped_frames=%d", dropped, total)
        return dropped

    def add_tomoko_audio(self, samples_24k: np.ndarray, at_mic_s: float) -> None:
        """24 kHz mono を予約する。既存の声と重なる指定はその末尾へつなぐ。"""
        samples = self._audio(samples_24k)
        self._check_time(at_mic_s)
        with self._condition:
            self._check_open()
            self._tomoko.add(samples, at_mic_s)

    def stop_tomoko(self, at_mic_s: float) -> None:
        """指定時刻以後の声を消す。すでに推論中のフレームは変更しない。"""
        self._check_time(at_mic_s)
        cut = round(at_mic_s * IN_SR)
        with self._condition:
            self._check_open()
            self._tomoko.stop(at_mic_s)
            for end, _, tomoko in self._pending:
                tomoko[max(0, cut - (end - self._frame_samples)):] = 0

    def latest(self) -> MaaiFrame | None:
        """最終の予測。起動直後や初回の warmup 中は None。"""
        with self._condition:
            return self._latest

    @property
    def dropped_frames(self) -> int:
        """遅れによって捨てたフレーム数の累計。Trace 用。読み取りでリセットしない。"""
        with self._condition:
            return self._dropped_frames

    @property
    def error(self) -> Exception | None:
        """モデルの構築・推論・終了の最初の失敗。後続の失敗もログには残す。"""
        with self._condition:
            return self._error

    def close(self, timeout: float = 5.0) -> None:
        """待機分と未実行通知を破棄して終了を待つ。process が戻らなければ TimeoutError。"""
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("MaaiSensor worker did not stop before timeout")

    @staticmethod
    def _audio(samples: np.ndarray) -> np.ndarray:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim != 1:
            raise ValueError("audio must be mono (one dimensional)")
        return audio

    @staticmethod
    def _check_time(at_s: float) -> None:
        if not isfinite(at_s) or at_s < 0:
            raise ValueError("mic time must be finite and nonnegative")

    def _deliver(self, frame: MaaiFrame) -> None:
        with self._condition:
            if self._closed:
                return
        self._on_frame(frame)

    def _run(self) -> None:
        model = None
        log.info("maai sensor starting")
        try:
            model = self._factory()
            if getattr(model, "audio_samples_per_frame", self._frame_samples) != self._frame_samples:
                raise ValueError("sensor frame_samples does not match the model frame rate")
            previous_end = None
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending)
                    if self._closed:
                        break
                    end, mic, tomoko = self._pending.popleft()
                if previous_end is not None and end - previous_end != self._frame_samples:
                    # 欠けた音声の前後を隣接した履歴として encoder / KV に渡さない。
                    model.reset_runtime_state()
                    log.warning("maai context reset after input gap at_s=%.3f", end / IN_SR)
                previous_end = end
                ch1, ch2 = (mic, tomoko) if self._user_channel == 0 else (tomoko, mic)
                model.process(ch1, ch2)
                try:
                    result = model.result_dict_queue.get_nowait()
                except Empty:  # Mimi の最初の出力など。次の入力がなければ結果は来ない。
                    continue
                frame = _to_frame(result, end / IN_SR, self._user_channel)
                with self._condition:
                    if self._closed:
                        break
                    self._latest = frame
                if self._on_frame is not None:
                    self._loop.call_soon_threadsafe(self._deliver, frame)
        except Exception as exc:
            log.exception("maai sensor failed")
            with self._condition:
                self._error = exc
        finally:
            if model is not None:
                try:
                    model.stop(wait=True)
                except Exception as exc:
                    log.exception("maai sensor stop failed")
                    with self._condition:
                        if self._error is None:
                            self._error = exc
            with self._condition:
                self._closed = True
                self._pending.clear()
            log.info("maai sensor stopped")


def make_maai_multiple(
    modes: Sequence[str] = ("vap", "bc", "bc_det"), *, lang: str = "jp",
    frame_rate: float = 12.5, context_len_sec: int = 20, device: str = "cpu",
    torch_threads: int = 1,
) -> _MaaiModel:
    """共有 Mimi の同期専用モデルを作る。重みの読み込みはこの関数内だけで起きる。

    torch のプロセス全体の演算スレッド数も変更する。ONNX は intra を同数、inter を 1 に
    制限する。キャッシュになければ MaAI 自身が重みを取得する。start() は呼ばない。
    """
    if not modes or isinstance(modes, str) or len(set(modes)) != len(modes):
        raise ValueError("modes must be nonempty and unique")
    if not isinstance(torch_threads, int) or torch_threads < 1:
        raise ValueError("torch_threads must be positive")
    if not isfinite(frame_rate) or frame_rate <= 0 or context_len_sec <= 0:
        raise ValueError("frame rate and context length must be positive")
    import torch

    torch.set_num_threads(torch_threads)
    from maai import MaaiInput, MaaiMultiple

    return MaaiMultiple(
        configs=[{"mode": mode, "lang": lang} for mode in modes],
        audio_ch1=MaaiInput.Chunk(), audio_ch2=MaaiInput.Chunk(),
        model_type="normal-ver2", frame_rate=frame_rate, context_len_sec=context_len_sec,
        device=device, mimi_onnx_cpu_intra_threads=torch_threads, mimi_onnx_cpu_inter_threads=1,
    )


def _drain(results: Queue) -> None:
    while True:
        try:
            results.get_nowait()
        except Empty:
            return


class SharedMaai:
    """MaAI のモデルをプロセスに 1 つだけ持ち、接続ごとに貸し出す。

    読み込みに数秒かかるので、接続のたびには作らない（ベンチは 1 件ごとにつなぎ直す）。
    1 人用なので、同時に貸すのは 1 つだけ。貸すときと返すときに状態をリセットし、会話の文脈を混ぜない。
    MaaiSensor の model_factory に lease を渡す。センサーが閉じると、stop で返される。
    """

    def __init__(self, model: _MaaiModel, warm_frames: int = 4) -> None:
        self._model = model
        self._lock = threading.Lock()
        self.frame_samples = int(getattr(model, "audio_samples_per_frame", 1280))
        silence = np.zeros(self.frame_samples, dtype=np.float32)
        for _ in range(warm_frames):  # 最初の推論は遅いので、起動時に済ませておく
            model.process(silence, silence)
        model.reset_runtime_state()
        _drain(model.result_dict_queue)

    def lease(self) -> _Lease:
        self._lock.acquire()
        try:
            self._model.reset_runtime_state()
            _drain(self._model.result_dict_queue)
        except BaseException:
            self._lock.release()
            raise
        return _Lease(self._model, self._lock)


class _Lease:
    """貸し出したモデル。stop は返すだけで、モデルそのものは止めない。"""

    def __init__(self, model: _MaaiModel, lock: threading.Lock) -> None:
        self._model, self._lock = model, lock
        self.result_dict_queue = model.result_dict_queue
        self.audio_samples_per_frame = getattr(model, "audio_samples_per_frame", 1280)

    def process(self, ch1: np.ndarray, ch2: np.ndarray) -> None:
        self._model.process(ch1, ch2)

    def reset_runtime_state(self) -> None:
        self._model.reset_runtime_state()

    def stop(self, wait: bool = True) -> None:
        if self._lock is None:
            return
        lock, self._lock = self._lock, None
        try:
            self._model.reset_runtime_state()
            _drain(self.result_dict_queue)
        finally:
            lock.release()
