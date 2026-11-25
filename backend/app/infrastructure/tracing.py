"""Langfuse traces for clinic turns. No-ops when keys are missing."""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings

log = logging.getLogger("clinic.trace")
_client = None
_failed = False


def _langfuse():
    global _client, _failed
    if _failed:
        return None
    if _client is not None:
        return _client
    settings = get_settings()
    if not (settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key):
        _failed = True
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host.rstrip("/"),
        )
        return _client
    except Exception:
        _failed = True
        log.info("langfuse client unavailable")
        return None


def trace_turn(
    *,
    session_id: str,
    phase: str,
    intent: str | None,
    elapsed_ms: float,
    user_line: str,
    agent_line: str,
) -> None:
    """Record one scrubbed turn. PHI must already be redacted in the lines."""
    client = _langfuse()
    if client is None:
        return
    try:
        trace = client.trace(
            name="clinic-turn",
            session_id=session_id,
            metadata={"phase": phase, "intent": intent or "", "elapsed_ms": round(elapsed_ms, 1)},
            input={"caller": user_line},
            output={"agent": agent_line},
        )
        trace.update(tags=["sunrise", phase])
        # Do not flush on the voice event loop — that blocked TTS for ~1s.
    except Exception:
        log.info("langfuse turn not recorded session=%s", session_id)


async def recent_traces(limit: int = 20) -> list[dict[str, Any]]:
    """Pull recent traces for the Observability page."""
    settings = get_settings()
    if not (settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key):
        return []
    try:
        import httpx

        url = settings.langfuse_host.rstrip("/") + "/api/public/traces"
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                url,
                params={"limit": limit},
                auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
            )
            response.raise_for_status()
            data = response.json()
        rows = data.get("data") or data.get("traces") or []
        out = []
        for row in rows[:limit]:
            meta = row.get("metadata") or {}
            out.append(
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "session_id": row.get("sessionId") or row.get("session_id"),
                    "phase": meta.get("phase") or "",
                    "elapsed_ms": meta.get("elapsed_ms"),
                    "timestamp": row.get("timestamp") or row.get("createdAt"),
                }
            )
        return out
    except Exception:
        return []
