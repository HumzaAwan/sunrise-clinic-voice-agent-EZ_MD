"""Sunrise Family Clinic API. Binds to localhost for the synthetic demo."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.routes import router
from app.config import get_settings
from app.database.seed import seed_if_empty
from app.database.session import get_factory, init_database
from app.services.office_hours import load_startup_clock


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    await init_database(settings.database_url)
    load_startup_clock()
    async with get_factory()() as session:
        await seed_if_empty(session)
        await session.commit()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Sunrise Family Clinic", lifespan=lifespan, docs_url="/api/docs")
    origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def localhost_only(request: Request, call_next):
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"detail": "localhost only"}, status_code=403)
        return await call_next(request)

    app.include_router(router, prefix="/api")
    return app


app = create_app()
