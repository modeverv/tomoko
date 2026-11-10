"""STT。切り出した発話を文字にする。MLX を使うので、MLX 用の 1 本のスレッドから呼ぶ。"""

import numpy as np


class WhisperStt:
    def __init__(self, model: str) -> None:
        self._model = model

    def warm(self) -> None:
        self.transcribe(np.zeros(16_000, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        import mlx_whisper

        result = mlx_whisper.transcribe(audio, path_or_hf_repo=self._model, language="ja",
                                        condition_on_previous_text=False)
        return result["text"].strip()
