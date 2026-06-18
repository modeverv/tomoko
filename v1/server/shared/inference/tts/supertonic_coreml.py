from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from huggingface_hub import hf_hub_download, snapshot_download

from server.shared.config import BackendSpec
from server.shared.inference.tts.base import TTSBackend
from server.shared.inference.tts.irodori_mlx import _wav_bytes_from_audio
from server.shared.models import AudioChunkOut, TTSInput

REPO_ID = "FluidInference/supertonic-3-coreml"
VOICE_STYLE_REPO_ID = "Reza2kn/supertonic-3-coreml"
DEFAULT_MODEL_DIR = Path("models/supertonic-3-coreml")


class SupertonicCoreMLBackend(TTSBackend):
    name = "supertonic_coreml"

    def __init__(
        self,
        *,
        model: str = REPO_ID,
        model_dir: Path = DEFAULT_MODEL_DIR,
        voice: str = "F1",
        lang: str = "ja",
        total_step: int = 8,
        speed: float = 1.05,
        compute_units: str = "CPU_AND_NE",
        tts: Any | None = None,
    ) -> None:
        self.model = model
        self.model_dir = model_dir
        self.voice = voice
        self.lang = lang
        self.total_step = total_step
        self.speed = speed
        self.compute_units = compute_units
        self._tts = tts

    @classmethod
    def from_spec(cls, spec: BackendSpec) -> SupertonicCoreMLBackend:
        return cls(
            model=spec.model or REPO_ID,
            model_dir=Path(spec.model_path or DEFAULT_MODEL_DIR),
            voice=spec.voice or "F1",
            lang=spec.language or "ja",
            total_step=spec.total_step or 8,
            speed=spec.speed or 1.05,
            compute_units=spec.compute_units or "CPU_AND_NE",
        )

    async def warm_up(self) -> None:
        async for _ in self.synthesize(TTSInput(text="あ。", style="neutral")):
            return

    async def synthesize(self, tts_input: TTSInput):
        text = tts_input.text.strip()
        if not text:
            return

        audio, sample_rate = await asyncio.to_thread(self._synthesize_sync, text, tts_input)
        yield AudioChunkOut(
            data=_wav_bytes_from_audio(audio, sample_rate=sample_rate),
            sequence=0,
            is_last=True,
        )

    def _synthesize_sync(self, text: str, tts_input: TTSInput) -> tuple[np.ndarray, int]:
        tts = self._load_tts()
        voice = tts_input.voice or self.voice
        voice_style_path = self._ensure_voice_style(voice)
        wav, _duration = tts.synthesize(
            text,
            voice_style_path,
            lang=self.lang,
            total_step=self.total_step,
            speed=self.speed,
        )
        return np.asarray(wav, dtype=np.float32), int(getattr(tts, "sample_rate", 24000))

    def _load_tts(self) -> Any:
        if self._tts is not None:
            return self._tts

        model_dir = self._prepare_model_dir()
        infer = _load_infer_module(model_dir / "infer.py")
        compute_units = getattr(infer.ct.ComputeUnit, self.compute_units)
        self._tts = infer.Supertonic3TTS(model_dir, compute_units)
        return self._tts

    def _prepare_model_dir(self) -> Path:
        if (self.model_dir / "infer.py").exists():
            return self.model_dir

        snapshot = Path(
            snapshot_download(
                self.model,
                allow_patterns=[
                    "*.mlpackage/*",
                    "tts.json",
                    "unicode_indexer.json",
                    "voice_styles/M1.json",
                    "infer.py",
                ],
            )
        )
        self.model_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot, self.model_dir, symlinks=False, dirs_exist_ok=True)
        return self.model_dir

    def _ensure_voice_style(self, voice: str) -> Path:
        voice_style_path = self.model_dir / "voice_styles" / f"{voice}.json"
        if voice_style_path.exists():
            return voice_style_path

        source = Path(
            hf_hub_download(VOICE_STYLE_REPO_ID, f"voice_styles/{voice}.json")
        )
        voice_style_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, voice_style_path)
        return voice_style_path


def _load_infer_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("supertonic_coreml_infer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Supertonic infer module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
