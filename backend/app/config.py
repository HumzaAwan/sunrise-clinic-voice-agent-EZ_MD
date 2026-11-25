"""Runtime settings. Secrets stay in the environment, never in source."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
SEED_PATH = REPO_ROOT / "database" / "seeds" / "synthetic_clinic_data.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "sqlite+aiosqlite:///./data/sunrise.db"
    redis_url: str = ""
    clinic_timezone: str = "America/New_York"
    clock_override: str = ""
    recording_retention_days: int = 7
    transcript_retention_days: int = 30
    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = ""
    kokoro_base_url: str = "http://127.0.0.1:8880/v1"
    kokoro_voice: str = "af_heart"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
