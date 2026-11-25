"""Cancellation and barge-in rules. Stale audio must not play after an interruption."""


class GenerationGuard:
    def __init__(self) -> None:
        self._current = 0
        self._cancelled: set[int] = set()

    def start(self) -> int:
        self._current += 1
        return self._current

    def cancel(self, generation: int | None = None) -> int:
        generation = self._current if generation is None else generation
        self._cancelled.add(generation)
        return generation

    def is_stale(self, generation: int) -> bool:
        return generation in self._cancelled or generation != self._current

    def allow_playback(self, generation: int) -> bool:
        return not self.is_stale(generation)


_BACKCHANNELS = {"yeah", "yep", "uh-huh", "uh huh", "mm-hmm", "mm hmm", "okay", "ok"}


def should_interrupt(*, speech_ms: int, transcript: str, min_ms: int = 250) -> bool:
    """Ignore short backchannels. Real speech above the minimum duration interrupts."""
    words = transcript.strip().lower()
    if words in _BACKCHANNELS:
        return False
    return speech_ms >= min_ms


def on_interrupt(*, emergency: bool, notice_delivered: bool, guard: GenerationGuard) -> str | None:
    """Cancel the current generation. Repeat only the emergency instruction."""
    guard.cancel()
    if emergency:
        return "This sounds like an emergency. Please hang up and call nine-one-one, or go to the nearest emergency room."
    if not notice_delivered:
        return None
    return None
