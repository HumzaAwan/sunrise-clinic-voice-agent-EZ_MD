"""Localhost API for the caller desk and the admin screens."""

import asyncio
from datetime import datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import (
    Appointment,
    CallbackRequest,
    EmailConfirmation,
    EvaluationResult,
    EvaluationRun,
    Patient,
    PatientMessage,
    RefillRequest,
)
from app.database.session import get_session
from app.evaluation.suite import run_suite
from app.infrastructure.metrics import metrics
from app.infrastructure.sessions import directory
from app.infrastructure.tracing import recent_traces
from app.services.office_hours import HOURS_LINE, get_clock
from app.services.records import open_slots, when_spoken

router = APIRouter()


class ClockBody(BaseModel):
    override: str | None = None


async def _probe(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=0.6) as client:
            response = await client.get(url)
        return "up" if response.status_code < 500 else "down"
    except Exception:
        return "down"


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    await session.scalar(select(Patient.patient_code).limit(1))
    settings = get_settings()
    ollama, kokoro = await asyncio.gather(
        _probe(settings.ollama_base_url.removesuffix("/v1") + "/api/tags"),
        _probe(settings.kokoro_base_url.removesuffix("/v1") + "/v1/models"),
    )
    livekit = "up" if settings.livekit_api_key and await _probe(settings.livekit_url.replace("ws://", "http://").replace("wss://", "https://")) == "up" else "down"
    return {
        "status": "ok",
        "database": "ok",
        "session_store": "redis" if settings.redis_url else "memory",
        "office_open": get_clock().is_open(),
        "voice": {
            "livekit": livekit,
            "ollama": ollama,
            "ollama_model": settings.ollama_model or "not_selected",
            "kokoro": kokoro,
            "whisper_model": settings.whisper_model,
            "whisper_device": settings.whisper_device,
        },
    }


@router.get("/observability")
async def observability() -> dict:
    settings = get_settings()
    langfuse_url = (settings.langfuse_host or "http://127.0.0.1:3000").rstrip("/")
    langfuse = await _probe(f"{langfuse_url}/api/public/health")
    redis_status = "down"
    if settings.redis_url:
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(settings.redis_url, socket_timeout=0.4)
            redis_status = "up" if await client.ping() else "down"
            await client.aclose()
        except Exception:
            redis_status = "down"
    traces = await recent_traces(20) if langfuse == "up" else []
    return {
        "session_store": "redis" if settings.redis_url else "memory",
        "whisper_model": settings.whisper_model,
        "whisper_device": settings.whisper_device,
        "metrics": metrics.snapshot(),
        "traces": traces,
        "services": [
            {"name": "Langfuse", "status": langfuse, "url": langfuse_url},
            {"name": "Redis", "status": redis_status, "url": settings.redis_url or ""},
        ],
    }


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict:
    patients = list(await session.scalars(select(Patient).order_by(Patient.patient_code)))
    appointments = list(await session.scalars(select(Appointment)))
    slots = await open_slots(session)
    next_by_patient: dict[str, Appointment] = {}
    for appointment in appointments:
        current = next_by_patient.get(appointment.patient_id)
        if current is None or (appointment.slot_date, appointment.slot_time) < (current.slot_date, current.slot_time):
            next_by_patient[appointment.patient_id] = appointment
    return {
        "hours": HOURS_LINE,
        "office_open": get_clock().is_open(),
        "now": get_clock().now().isoformat(),
        "active_sessions": len(directory.live),
        "patients": [
            {
                "code": patient.patient_code,
                "name": f"{patient.first_name} {patient.last_name}",
                "lab_status": patient.lab_status,
                "balance": patient.balance_due,
                "next_appointment": (
                    f"{when_spoken(nxt.slot_date, nxt.slot_time)} with {nxt.doctor}"
                    if (nxt := next_by_patient.get(patient.id))
                    else None
                ),
            }
            for patient in patients
        ],
        "open_slots": [
            {"doctor": slot.doctor, "when": when_spoken(slot.slot_date, slot.slot_time)}
            for slot in slots
        ],
    }


@router.get("/requests")
async def requests(session: AsyncSession = Depends(get_session)) -> dict:
    refills = list(await session.scalars(select(RefillRequest).order_by(RefillRequest.created_at.desc())))
    callbacks = list(await session.scalars(select(CallbackRequest).order_by(CallbackRequest.created_at.desc())))
    emails = list(await session.scalars(select(EmailConfirmation).order_by(EmailConfirmation.created_at.desc())))
    notes = list(await session.scalars(select(PatientMessage).order_by(PatientMessage.created_at.desc())))
    return {
        "refills": [
            {
                "id": row.id,
                "medication": row.medication_name,
                "pharmacy": row.pharmacy,
                "status": row.status,
                "summary": row.summary,
                "created_at": row.created_at.isoformat(),
            }
            for row in refills
        ],
        "callbacks": [
            {
                "id": row.id,
                "name": row.caller_name,
                "number": row.callback_number,
                "reason": row.reason,
                "kind": row.kind,
                "summary": row.summary,
                "created_at": row.created_at.isoformat(),
            }
            for row in callbacks
        ],
        "doctor_notes": [
            {
                "id": row.id,
                "category": row.category,
                "summary": row.summary,
                "created_at": row.created_at.isoformat(),
            }
            for row in notes
        ],
        "confirmations": [
            {
                "id": row.id,
                "to": row.to_address,
                "subject": row.subject,
                "created_at": row.created_at.isoformat(),
            }
            for row in emails
        ],
    }


@router.get("/sessions")
async def sessions(session: AsyncSession = Depends(get_session)) -> dict:
    import json

    from app.database.models import CallSessionRow, Transcript

    rows = (
        await session.scalars(select(CallSessionRow).order_by(CallSessionRow.started_at.desc()).limit(40))
    ).all()
    payload = []
    for row in rows:
        stored = (
            await session.scalars(
                select(Transcript).where(Transcript.session_id == row.id).order_by(Transcript.created_at)
            )
        ).all()
        lines = []
        for item in stored:
            try:
                parsed = json.loads(item.content_redacted)
            except json.JSONDecodeError:
                parsed = {"who": "agent", "text": item.content_redacted}
            lines.append({"who": parsed.get("who", "agent"), "text": parsed.get("text", ""), "at": item.created_at.isoformat()})
        payload.append(
            {
                "session_id": row.id,
                "phase": row.phase,
                "verified": row.verified,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "lines": lines,
            }
        )
    return {"sessions": payload}


@router.post("/sessions")
async def open_session() -> dict:
    """Mint a localhost LiveKit token. The worker joins the room and speaks."""
    settings = get_settings()
    if not (settings.livekit_api_key and settings.livekit_api_secret):
        return {"available": False, "reason": "LiveKit keys are not configured."}
    from livekit.api import AccessToken, VideoGrants

    room = f"clinic-{uuid4().hex[:12]}"
    identity = f"caller-{uuid4().hex[:8]}"
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name("Caller")
        .with_grants(VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )
    state = directory.create()
    return {
        "available": True,
        "url": settings.livekit_url,
        "token": token,
        "room": room,
        "session": state.public(),
    }


@router.get("/metrics/summary")
async def metrics_summary() -> dict:
    return metrics.snapshot()


@router.post("/admin/clock")
async def set_clock(body: ClockBody) -> dict:
    if body.override:
        parsed = datetime.fromisoformat(body.override)
        get_clock().set_override(parsed)
    else:
        get_clock().set_override(None)
    return {"office_open": get_clock().is_open(), "now": get_clock().now().isoformat()}


@router.post("/evaluations/run")
async def run_evaluations(session: AsyncSession = Depends(get_session)) -> dict:
    results = await run_suite()
    passed = sum(1 for item in results if item["passed"])
    run = EvaluationRun(name="assignment", passed=passed, failed=len(results) - passed)
    session.add(run)
    await session.flush()
    for item in results:
        session.add(
            EvaluationResult(
                run_id=run.id,
                scenario_id=item["id"],
                passed=item["passed"],
                detail=item["detail"][:255],
            )
        )
    await session.commit()
    return {"run_id": run.id, "passed": passed, "failed": len(results) - passed, "results": results}


@router.get("/evaluations")
async def list_evaluations(session: AsyncSession = Depends(get_session)) -> dict:
    runs = list(await session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc())))
    payload = []
    for run in runs[:10]:
        rows = list(await session.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
        payload.append(
            {
                "id": run.id,
                "name": run.name,
                "passed": run.passed,
                "failed": run.failed,
                "created_at": run.created_at.isoformat(),
                "results": [
                    {"scenario_id": row.scenario_id, "passed": row.passed, "detail": row.detail}
                    for row in rows
                ],
            }
        )
    return {"runs": payload}


@router.get("/settings")
async def settings_view() -> dict:
    settings = get_settings()
    return {
        "timezone": settings.clinic_timezone,
        "recording_retention_days": settings.recording_retention_days,
        "transcript_retention_days": settings.transcript_retention_days,
        "office_open": get_clock().is_open(),
        "now": get_clock().now().isoformat(),
        "database": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
        "docker": "local",
    }

