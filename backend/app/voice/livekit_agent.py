"""Browser call worker. The clinic graph decides every spoken line."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    StopResponse,
    cli,
    get_job_context,
)
from livekit.plugins import openai, silero

from app.agent.phrases import CLOSED, GREETING_OPEN
from app.agent.runtime import record_agent_line, run_turn
from app.agent.state import CallState
from app.config import get_settings
from app.database.seed import seed_if_empty
from app.database.session import get_factory, init_database
from app.infrastructure.turn_timing import (
    current as current_timing,
    set_first_audio,
    set_think,
)
from app.providers.stt.faster_whisper import FasterWhisperSTT
from app.services.office_hours import get_clock, load_startup_clock
from app.voice.control import GenerationGuard, on_interrupt

log = logging.getLogger("clinic.voice")
server = AgentServer()


def _apply_livekit_env() -> None:
    settings = get_settings()
    os.environ["LIVEKIT_URL"] = settings.livekit_url
    os.environ["LIVEKIT_API_KEY"] = settings.livekit_api_key
    os.environ["LIVEKIT_API_SECRET"] = settings.livekit_api_secret


async def _publish_latency() -> None:
    timing = current_timing()
    if timing is None:
        return
    payload = timing.as_log()
    log.info(
        "latency eou→audio=%sms (stt=%s think=%s llm=%s tts_ttfa=%s)",
        payload["eou_to_audio_ms"],
        payload["stt_ms"],
        payload["think_ms"],
        payload["llm_ms"],
        payload["tts_ttfa_ms"],
    )
    try:
        room = get_job_context().room
        await room.local_participant.publish_data(
            json.dumps(payload).encode("utf-8"),
            reliable=True,
        )
    except Exception:
        log.info("latency publish skipped")


async def _wait_first_audio(session: AgentSession, say_started: float) -> float:
    """Return ms from say() until the first TTS audio frame plays."""
    audio = session.output.audio
    if audio is None:
        return (time.perf_counter() - say_started) * 1000
    import asyncio

    first = asyncio.Event()

    def _on_started(_ev=None) -> None:
        first.set()

    audio.on("playback_started", _on_started)
    try:
        await asyncio.wait_for(first.wait(), timeout=20)
    except TimeoutError:
        pass
    finally:
        with contextlib.suppress(Exception):
            audio.off("playback_started", _on_started)
    return (time.perf_counter() - say_started) * 1000


class ClinicVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are the Sunrise Family Clinic phone shell. "
                "Never invent patient facts, never give medical advice, and never read a Social Security number."
            ),
        )
        self.call = CallState()
        self.guard = GenerationGuard()

    async def on_enter(self) -> None:
        open_now = get_clock().is_open()
        greeting = GREETING_OPEN if open_now else (
            "Thank you for calling Sunrise Family Clinic. This call may be recorded. " + CLOSED
        )
        gen = self.guard.start()
        handle = self.session.say(greeting, allow_interruptions=True)
        await handle.wait_for_playout()
        if handle.interrupted or self.guard.is_stale(gen):
            # Do not restart the greeting or play a second "recorded" line.
            # The opening already includes the notice; answer the caller next.
            on_interrupt(emergency=False, notice_delivered=True, guard=self.guard)
        self.call.recording_notice_delivered = True
        await record_agent_line(self.call, greeting)

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = (new_message.text_content or "").strip()
        plain = text.lower().strip(".! ")
        words = re.findall(r"[a-z']+", plain)
        digit_count = len(re.sub(r"\D", "", text))
        # Pure number turns (phones, last-four) have no letter-words — do not treat as filler.
        filler = digit_count < 4 and (
            not words
            or all(
                word in {"thank", "you", "thanks", "uh", "um", "uhh", "hmm", "okay", "ok", "hello", "hi", "hey"}
                for word in words
            )
        )
        if filler:
            # Mid-form: re-ask the pending question instead of going silent.
            pending = (self.call.say or "").strip()
            if pending and self.call.phase not in {"listen", ""}:
                handle = self.session.say(pending, allow_interruptions=not self.call.emergency)
                await handle.wait_for_playout()
            raise StopResponse()
        if self.call.say and plain in self.call.say.lower():
            raise StopResponse()

        # Turn is committed here: caller stopped speaking and STT already finished.
        eou_at = time.perf_counter()

        # Barge-in: invalidate any prior generation so late audio cannot play.
        replay = on_interrupt(
            emergency=self.call.emergency,
            notice_delivered=self.call.recording_notice_delivered,
            guard=self.guard,
        )
        gen = self.guard.start()

        factory = get_factory()
        async with factory() as session:
            await run_turn(session, self.call, text)
        set_think((time.perf_counter() - eou_at) * 1000)

        if self.guard.is_stale(gen):
            raise StopResponse()

        spoken = replay if (replay and self.call.emergency) else self.call.say
        if not spoken:
            set_first_audio(tts_ttfa_ms=0, eou_to_audio_ms=(time.perf_counter() - eou_at) * 1000)
            await _publish_latency()
            raise StopResponse()

        say_started = time.perf_counter()
        handle = self.session.say(spoken, allow_interruptions=not self.call.emergency)
        tts_ttfa = await _wait_first_audio(self.session, say_started)
        set_first_audio(
            tts_ttfa_ms=tts_ttfa,
            eou_to_audio_ms=(time.perf_counter() - eou_at) * 1000,
        )
        await _publish_latency()
        await handle.wait_for_playout()
        if handle.interrupted and not self.call.emergency:
            on_interrupt(
                emergency=self.call.emergency,
                notice_delivered=self.call.recording_notice_delivered,
                guard=self.guard,
            )
        raise StopResponse()


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    settings = get_settings()
    await ctx.connect()
    session = AgentSession(
        vad=silero.VAD.load(
            # Mild noise rejection without killing barge-in.
            min_speech_duration=0.12,
            min_silence_duration=0.55,
            activation_threshold=0.55,
        ),
        stt=FasterWhisperSTT(settings.whisper_model, settings.whisper_device),
        tts=openai.TTS(
            model="kokoro",
            voice=settings.kokoro_voice,
            base_url=settings.kokoro_base_url,
            api_key="not-needed",
            response_format="mp3",
        ),
        turn_handling={
            "endpointing": {"mode": "fixed", "min_delay": 0.5, "max_delay": 0.8},
            "interruption": {
                "enabled": True,
                "mode": "vad",
                # min_words must stay 0 — STT is not ready yet when barge-in starts.
                "min_duration": 0.5,
                "min_words": 0,
                "false_interruption_timeout": 1.2,
                "resume_false_interruption": True,
            },
        },
    )
    await session.start(room=ctx.room, agent=ClinicVoiceAgent())


async def prepare_models() -> None:
    settings = get_settings()
    await init_database(settings.database_url)
    async with get_factory()() as session:
        await seed_if_empty(session)
        await session.commit()
    from app.providers.stt.faster_whisper import load_model

    load_model(settings.whisper_model, settings.whisper_device)
    silero.VAD.load(min_speech_duration=0.12, min_silence_duration=0.55, activation_threshold=0.55)
    load_startup_clock()


def main() -> None:
    import asyncio

    _apply_livekit_env()
    settings = get_settings()
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise SystemExit("LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required.")
    asyncio.run(prepare_models())
    cli.run_app(server)


if __name__ == "__main__":
    main()
