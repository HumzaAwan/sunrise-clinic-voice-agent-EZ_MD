"""Chunked Faster-Whisper adapter. This is not a native streaming recognizer."""

from __future__ import annotations

import asyncio
import os
import site
from uuid import uuid4

import numpy as np
from faster_whisper import WhisperModel
from livekit.agents import stt
from livekit.agents.utils.audio import AudioBuffer, merge_frames

_model: WhisperModel | None = None


def _enable_cuda_dlls() -> None:
    """Windows only loads cuBLAS when its folder is on the DLL search path."""
    if os.name != "nt":
        return
    roots = [path for path in site.getsitepackages() if path]
    user_site = site.getusersitepackages()
    if user_site:
        roots.append(user_site)
    for root in roots:
        nvidia = os.path.join(root, "nvidia")
        if not os.path.isdir(nvidia):
            continue
        for dirpath, _dirs, filenames in os.walk(nvidia):
            if any(name.lower().endswith(".dll") and name.lower().startswith(("cublas", "cudnn", "cudart")) for name in filenames):
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(dirpath)
                os.environ["PATH"] = dirpath + os.pathsep + os.environ.get("PATH", "")


def load_model(model_size: str = "small", device: str = "cpu") -> WhisperModel:
    global _model
    if _model is None:
        if device == "cuda":
            _enable_cuda_dlls()
        compute = "int8" if device == "cpu" else "float16"
        _model = WhisperModel(model_size, device=device, compute_type=compute)
    return _model


def _to_16k(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == 16000:
        return pcm
    target = max(1, int(len(pcm) * 16000 / sample_rate))
    grid = np.linspace(0, len(pcm) - 1, target)
    return np.interp(grid, np.arange(len(pcm)), pcm).astype(np.float32)


class FasterWhisperSTT(stt.STT):
    def __init__(self, model_size: str = "small", device: str = "cpu") -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._model_size = model_size
        self._device = device

    @property
    def model(self) -> str:
        return self._model_size

    @property
    def provider(self) -> str:
        return "faster-whisper"

    async def _recognize_impl(self, buffer: AudioBuffer, *, language=None, conn_options=None) -> stt.SpeechEvent:
        frame = merge_frames(buffer)
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        audio = _to_16k(pcm, frame.sample_rate)
        engine = load_model(self._model_size, self._device)

        def _transcribe() -> str:
            segments, _info = engine.transcribe(audio, language="en", vad_filter=False, beam_size=1)
            return " ".join(segment.text.strip() for segment in segments).strip()

        started = asyncio.get_running_loop().time()
        try:
            text = await asyncio.wait_for(asyncio.to_thread(_transcribe), timeout=20)
        except TimeoutError:
            text = ""
        from app.infrastructure.turn_timing import record_stt

        record_stt((asyncio.get_running_loop().time() - started) * 1000)
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=str(uuid4()),
            alternatives=[stt.SpeechData(language="en", text=text, start_time=0.0, end_time=len(audio) / 16000)],
        )
