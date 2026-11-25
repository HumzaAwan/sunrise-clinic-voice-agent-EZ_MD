"""One clinic turn. The raw utterance is not written into the saved transcript during verification."""

import logging
import re
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import GRAPH, call_ctx, db_ctx, text_ctx
from app.agent.state import CallState
from app.infrastructure.metrics import metrics
from app.security.redaction import redact_text, verification_placeholder
from app.security.sensitive_capture import parse_identity

log = logging.getLogger("clinic.turn")
_FULL_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def should_redact_user(phase: str, text: str) -> bool:
    if phase in {"verify", "book_dob", "book_contact", "book_phone"}:
        return True
    if _FULL_SSN.search(text):
        return True
    return parse_identity(text) is not None


async def run_turn(session: AsyncSession, call: CallState, text: str) -> CallState:
    from app.infrastructure.turn_timing import begin_turn

    started = time.perf_counter()
    begin_turn()
    phase_at_start = call.phase
    user_line = ""
    agent_line = ""
    tokens = (
        call_ctx.set(call),
        db_ctx.set(session),
        text_ctx.set(text),
    )
    try:
        await GRAPH.ainvoke({"branch": "start"})
        if call.redact_user or should_redact_user(phase_at_start, text):
            user_line = verification_placeholder()
        else:
            user_line = redact_text(text)
        call.lines.append(("caller", user_line))
        agent_line = redact_text(call.say)
        call.lines.append(("agent", agent_line))
        await _save_history(session, call, user_line, agent_line)
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("turn failed session=%s", call.session_id)
        call.say = "I'm sorry, something went wrong on our side. Please try that again, or ask for a staff member."
        raise
    finally:
        call.redact_user = False
        call.just_verified = False
        call_ctx.reset(tokens[0])
        db_ctx.reset(tokens[1])
        text_ctx.reset(tokens[2])
    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics.observe(elapsed_ms)
    metrics.inc("turns")
    await _mirror_session(call)
    from app.infrastructure.tracing import trace_turn

    trace_turn(
        session_id=call.session_id,
        phase=call.phase,
        intent=call.intent,
        elapsed_ms=elapsed_ms,
        user_line=user_line,
        agent_line=agent_line,
    )
    log.info("turn session=%s phase=%s ms=%.0f", call.session_id, call.phase, elapsed_ms)
    return call


async def record_agent_line(call: CallState, text: str) -> None:
    """Save the greeting before the caller has spoken."""
    spoken = redact_text(text)
    call.lines.append(("agent", spoken))
    from app.database.session import get_factory

    async with get_factory()() as db:
        await _save_history(db, call, None, spoken)
        await db.commit()


async def _save_history(session: AsyncSession, call: CallState, user_line: str | None, agent_line: str) -> None:
    import json
    from datetime import timedelta

    from app.config import get_settings
    from app.database.models import CallSessionRow, Transcript, utcnow

    row = await session.get(CallSessionRow, call.session_id)
    if row is None:
        row = CallSessionRow(
            id=call.session_id,
            phase=call.phase,
            verified=call.verified_patient_id is not None,
        )
        session.add(row)
    else:
        row.phase = call.phase
        row.verified = call.verified_patient_id is not None
    retain = utcnow() + timedelta(days=get_settings().transcript_retention_days)
    pairs = [("agent", agent_line)] if user_line is None else [("caller", user_line), ("agent", agent_line)]
    for who, line in pairs:
        session.add(
            Transcript(
                session_id=call.session_id,
                content_redacted=json.dumps({"who": who, "text": line}),
                retain_until=retain,
            )
        )


_redis_down = False


async def _mirror_session(call: CallState) -> None:
    """Store the scrubbed session view in Redis. Raw verification values are not on that view."""
    global _redis_down
    from app.config import get_settings

    url = get_settings().redis_url
    if not url or _redis_down:
        return
    try:
        import json

        from redis.asyncio import Redis

        client = Redis.from_url(url, socket_timeout=0.4)
        await client.set(f"sunrise:call:{call.session_id}", json.dumps(call.redis_view()), ex=3600)
        await client.aclose()
    except Exception:
        _redis_down = True
        log.info("redis mirror unavailable session=%s", call.session_id)
