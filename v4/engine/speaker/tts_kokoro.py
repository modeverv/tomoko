"""TTS（Kokoro）。句を声にする。MLX を使うので、MLX 用の 1 本のスレッドから呼ぶ。

v1 の経験: misaki の既定（unidic）は辞書なしで入ることがあり、文字は出るのに音が出ない。
日本語の音素化を pyopenjtalk に差し替える。
"""

import numpy as np

from engine.dto import OUT_SR


class KokoroTts:
    def __init__(self, voice: str) -> None:
        from kokoro_mlx import KokoroTTS
        from kokoro_mlx.phonemize import Phonemizer
        from misaki import ja

        self._voice = voice
        self._tts = KokoroTTS.from_pretrained()
        phonemizer = object.__new__(Phonemizer)
        phonemizer._vocab = self._tts._config.vocab
        phonemizer._language = "ja"
        phonemizer._g2p = ja.JAG2P(version="pyopenjtalk")
        self._tts._phonemizers["ja"] = phonemizer

    def warm(self) -> None:
        self.synthesize("こんにちは。")

    def synthesize(self, text: str) -> np.ndarray:
        chunks = [np.asarray(c, dtype=np.float32).reshape(-1) for c in self._tts.generate_stream(
            text, voice=self._voice, speed=1.0, sample_rate=OUT_SR, language="ja")]
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
