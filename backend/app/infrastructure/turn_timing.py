"""Latency from end of user speech to first TTS audio, with a stage breakdown."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class TurnTiming:
    # Stages after the caller stops talking.
    stt_ms: float = 0.0  # Whisper (usually finished just before the turn handler)
    think_ms: float = 0.0  # graph + any LLM work until say()
    llm_ms: float = 0.0  # subset of think: Ollama wall time
    llm_calls: int = 0
    tts_ttfa_ms: float = 0.0  # say() → first audio frame
    eou_to_audio_ms: float = 0.0  # end of user speech → first audio
    started: float = field(default_factory=perf_counter)

    def as_log(self) -> dict:
        return {
            "type": "latency",
            "stt_ms": round(self.stt_ms),
            "think_ms": round(self.think_ms),
            "llm_ms": round(self.llm_ms),
            "llm_calls": self.llm_calls,
            "tts_ttfa_ms": round(self.tts_ttfa_ms),
            "eou_to_audio_ms": round(self.eou_to_audio_ms),
        }


_timing: ContextVar[TurnTiming | None] = ContextVar("turn_timing", default=None)
_last_stt_ms: float = 0.0


def begin_turn() -> TurnTiming:
    timing = TurnTiming(stt_ms=_last_stt_ms)
    _timing.set(timing)
    return timing


def current() -> TurnTiming | None:
    return _timing.get()


def record_stt(ms: float) -> None:
    global _last_stt_ms
    _last_stt_ms = ms


def add_llm(ms: float) -> None:
    timing = _timing.get()
    if timing is None:
        return
    timing.llm_ms += ms
    timing.llm_calls += 1


def set_think(ms: float) -> None:
    timing = _timing.get()
    if timing is None:
        return
    timing.think_ms = ms


def set_first_audio(*, tts_ttfa_ms: float, eou_to_audio_ms: float) -> None:
    timing = _timing.get()
    if timing is None:
        return
    timing.tts_ttfa_ms = tts_ttfa_ms
    timing.eou_to_audio_ms = eou_to_audio_ms
