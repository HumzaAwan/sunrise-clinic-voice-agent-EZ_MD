"""Clinic clock in America/New_York, including daylight-saving changes.

The API and the LiveKit worker are separate processes. When Redis is configured,
the override is shared under sunrise:clinic:clock so Settings → closed reaches
the voice agent on the next turn.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.config import get_settings

_REDIS_KEY = "sunrise:clinic:clock"


class ClinicClock:
    def __init__(self, timezone_name: str | None = None, *, shared: bool = False) -> None:
        name = timezone_name or get_settings().clinic_timezone
        self.zone = ZoneInfo(name)
        self.override: datetime | None = None
        self.shared = shared

    def set_override(self, moment: datetime | None) -> None:
        if moment is None:
            self.override = None
        else:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=self.zone)
            self.override = moment.astimezone(self.zone)
        if self.shared:
            self._write_store()

    def refresh_shared(self) -> None:
        if self.shared:
            self._read_store()

    def now(self) -> datetime:
        self.refresh_shared()
        if self.override is not None:
            return self.override
        return datetime.now(self.zone)

    def is_open(self, moment: datetime | None = None) -> bool:
        current = moment.astimezone(self.zone) if moment else self.now()
        if current.weekday() >= 5:
            return False
        start = datetime.combine(current.date(), time(9, 0), tzinfo=self.zone)
        end = datetime.combine(current.date(), time(17, 0), tzinfo=self.zone)
        return start <= current < end

    def _redis(self):
        url = get_settings().redis_url
        if not url:
            return None
        try:
            import redis

            return redis.Redis.from_url(url, socket_timeout=0.4, decode_responses=True)
        except Exception:
            return None

    def _write_store(self) -> None:
        client = self._redis()
        if client is None:
            return
        try:
            if self.override is None:
                client.delete(_REDIS_KEY)
            else:
                client.set(_REDIS_KEY, self.override.isoformat())
        except Exception:
            return

    def _read_store(self) -> None:
        client = self._redis()
        if client is None:
            return
        try:
            raw = client.get(_REDIS_KEY)
        except Exception:
            return
        if not raw:
            self.override = None
            return
        try:
            moment = datetime.fromisoformat(str(raw))
        except ValueError:
            self.override = None
            return
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.zone)
        self.override = moment.astimezone(self.zone)


_clock = ClinicClock(shared=True)


def get_clock() -> ClinicClock:
    return _clock


def load_startup_clock() -> None:
    """Prefer a Settings/Redis override; fall back to CLOCK_OVERRIDE from the environment."""
    clock = get_clock()
    clock.refresh_shared()
    if clock.override is not None:
        return
    raw = get_settings().clock_override
    if raw:
        clock.set_override(datetime.fromisoformat(raw))


HOURS_LINE = "Monday through Friday, 9:00 AM to 5:00 PM Eastern Time."
