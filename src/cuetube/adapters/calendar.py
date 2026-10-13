"""Google Calendar API v3 availability querying and event insertion adapter."""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

from cuetube.adapters.youtube import OAuthCredentials
from cuetube.domain.models import (
    CircadianScheduleConfig,
    EnergyLevel,
    ScheduledSlot,
    TimeWindow,
    parse_blackout_windows,
)

logger = logging.getLogger(__name__)


class CalendarApiError(Exception):
    """Base exception for Google Calendar API interactions."""


class CalendarAuthError(CalendarApiError):
    """Raised when Calendar authentication or token refresh fails."""


def _ensure_aware(dt: datetime) -> datetime:
    """Ensure datetime object is timezone-aware (defaulting to UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_rfc3339(raw_str: str) -> datetime:
    """Parse RFC 3339 / ISO 8601 datetime string from Google Calendar API."""
    cleaned = raw_str.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    return _ensure_aware(dt)


def _format_rfc3339(dt: datetime) -> str:
    """Format datetime into RFC 3339 string expected by Google Calendar API."""
    return _ensure_aware(dt).isoformat()


def merge_busy_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or contiguous busy intervals into disjoint sorted intervals."""
    if not intervals:
        return []

    sorted_intervals = sorted(intervals, key=lambda iv: iv[0])
    merged: list[tuple[datetime, datetime]] = [sorted_intervals[0]]

    for current_start, current_end in sorted_intervals[1:]:
        last_start, last_end = merged[-1]
        if current_start <= last_end:
            # Overlapping or contiguous; extend end time if further
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            merged.append((current_start, current_end))

    return merged


def subtract_busy_from_window(
    window_start: datetime,
    window_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    min_duration_minutes: int = 15,
) -> list[tuple[datetime, datetime]]:
    """Subtract busy intervals from a time window, returning available sub-intervals."""
    current_start = window_start
    free_intervals: list[tuple[datetime, datetime]] = []

    for busy_start, busy_end in busy_intervals:
        if busy_end <= current_start or busy_start >= window_end:
            continue

        # Clip busy interval to window boundaries
        effective_busy_start = max(current_start, busy_start)
        effective_busy_end = min(window_end, busy_end)

        # Gap between current_start and effective_busy_start
        if effective_busy_start > current_start:
            gap_mins = (effective_busy_start - current_start).total_seconds() / 60.0
            if gap_mins >= min_duration_minutes:
                free_intervals.append((current_start, effective_busy_start))

        current_start = max(current_start, effective_busy_end)
        if current_start >= window_end:
            break

    if current_start < window_end:
        tail_mins = (window_end - current_start).total_seconds() / 60.0
        if tail_mins >= min_duration_minutes:
            free_intervals.append((current_start, window_end))

    return free_intervals


class GoogleCalendarAdapter:
    """Calendar adapter interfacing with Google Calendar API (v3) freebusy and events endpoints."""

    def __init__(
        self,
        credentials: OAuthCredentials | None = None,
        calendar_ids: tuple[str, ...] = ("primary",),
        schedule_config: CircadianScheduleConfig | None = None,
        include_all_accessible_calendars: bool = False,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self.credentials = credentials
        self.calendar_ids = calendar_ids
        self.schedule_config = schedule_config or CircadianScheduleConfig()
        self.include_all_accessible_calendars = include_all_accessible_calendars
        self.http_transport = http_transport

    @classmethod
    def from_env(
        cls,
        calendar_ids: tuple[str, ...] = ("primary",),
        schedule_config: CircadianScheduleConfig | None = None,
        include_all_accessible_calendars: bool = False,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> "GoogleCalendarAdapter":
        """Factory initializing credentials from environment with prefix fallbacks."""
        creds = None
        for prefix in ("CALENDAR_", "GOOGLE_", "YOUTUBE_"):
            try:
                creds = OAuthCredentials.from_env(env_prefix=prefix)
                break
            except Exception:
                continue

        if creds is None:
            raise CalendarAuthError(
                "Could not find valid OAuth credentials with CALENDAR_, GOOGLE_, "
                "or YOUTUBE_ prefix."
            )

        if schedule_config is None:
            raw_bo = os.getenv("CUETUBE_BLACKOUT_WINDOWS") or os.getenv("BLACKOUT_WINDOWS") or ""
            blackouts = parse_blackout_windows(raw_bo) if raw_bo else ()
            m_start = int(os.getenv("CUETUBE_MORNING_START", "9"))
            m_end = int(os.getenv("CUETUBE_MORNING_END", "12"))
            a_start = int(os.getenv("CUETUBE_AFTERNOON_START", "13"))
            a_end = int(os.getenv("CUETUBE_AFTERNOON_END", "17"))
            e_start = int(os.getenv("CUETUBE_EVENING_START", "17"))
            e_end = int(os.getenv("CUETUBE_EVENING_END", "21"))
            schedule_config = CircadianScheduleConfig(
                morning_start_hour=m_start,
                morning_end_hour=m_end,
                afternoon_start_hour=a_start,
                afternoon_end_hour=a_end,
                evening_start_hour=e_start,
                evening_end_hour=e_end,
                blackout_windows=blackouts,
            )

        return cls(
            credentials=creds,
            calendar_ids=calendar_ids,
            schedule_config=schedule_config,
            include_all_accessible_calendars=include_all_accessible_calendars,
            http_transport=http_transport,
        )

    def _execute_request(self, req: urllib.request.Request) -> dict[str, Any]:
        """Execute HTTP request with authentication header injection."""
        if self.credentials:
            token = self.credentials.get_valid_access_token(http_transport=self.http_transport)
            req.add_header("Authorization", f"Bearer {token}")

        if not req.has_header("Content-Type"):
            req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        try:
            if self.http_transport:
                res = self.http_transport(req)
                if isinstance(res, dict):
                    return res
                if isinstance(res, (bytes, str)):
                    return json.loads(res)
                return {}

            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8", errors="replace")
            if exc.code in (401, 403):
                raise CalendarAuthError(
                    f"Google Calendar authorization error (HTTP {exc.code}): {err_msg}"
                ) from exc
            raise CalendarApiError(
                f"Google Calendar API HTTP error (HTTP {exc.code}): {err_msg}"
            ) from exc
        except Exception as exc:
            raise CalendarApiError(f"Google Calendar API request failed: {exc}") from exc

    def list_accessible_calendar_ids(self) -> list[str]:
        """Discover all user-visible calendar IDs via calendarList.list."""
        url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
        req = urllib.request.Request(url, method="GET")
        data = self._execute_request(req)

        items = data.get("items", [])
        calendar_ids: list[str] = []
        for item in items:
            cid = item.get("id")
            if cid:
                calendar_ids.append(cid)
        return calendar_ids or ["primary"]

    def query_availability(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[TimeWindow]:
        """Query freebusy status and build circadian TimeWindows."""
        start_dt = _ensure_aware(start_time)
        end_dt = _ensure_aware(end_time)

        if end_dt <= start_dt:
            return []

        # Determine target calendar IDs
        target_calendar_ids = list(self.calendar_ids)
        if self.include_all_accessible_calendars:
            discovered = self.list_accessible_calendar_ids()
            target_calendar_ids = list(dict.fromkeys(target_calendar_ids + discovered))

        # 1. Query freebusy endpoint
        freebusy_url = "https://www.googleapis.com/calendar/v3/freeBusy"
        payload = {
            "timeMin": _format_rfc3339(start_dt),
            "timeMax": _format_rfc3339(end_dt),
            "items": [{"id": cid} for cid in target_calendar_ids],
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(freebusy_url, data=body, method="POST")

        data = self._execute_request(req)
        calendars = data.get("calendars", {})

        raw_busy: list[tuple[datetime, datetime]] = []
        for _cid, cdata in calendars.items():
            for b in cdata.get("busy", []):
                b_start = _parse_rfc3339(b["start"])
                b_end = _parse_rfc3339(b["end"])
                if b_end > b_start:
                    raw_busy.append((b_start, b_end))

        # 2. Iterate each day across span and build candidate circadian windows
        cfg = self.schedule_config
        tz = start_dt.tzinfo
        cur_day = start_dt.date()
        last_day = end_dt.date()

        # Inject manual blackout windows for all dates matching day_of_week
        day_cursor = cur_day
        while day_cursor <= last_day:
            for blackout in cfg.blackout_windows:
                if day_cursor.weekday() in blackout.days_of_week:
                    bo_start = datetime.combine(day_cursor, blackout.start_time, tzinfo=tz)
                    bo_end = datetime.combine(day_cursor, blackout.end_time, tzinfo=tz)
                    if bo_end > bo_start:
                        raw_busy.append((bo_start, bo_end))
            day_cursor += timedelta(days=1)

        merged_busy = merge_busy_intervals(raw_busy)

        day_windows: list[tuple[datetime, datetime, EnergyLevel]] = []

        while cur_day <= last_day:
            day_candidates = [
                (
                    datetime.combine(cur_day, time(cfg.morning_start_hour, 0), tzinfo=tz),
                    datetime.combine(cur_day, time(cfg.morning_end_hour, 0), tzinfo=tz),
                    EnergyLevel.MORNING_FOCUS,
                ),
                (
                    datetime.combine(cur_day, time(cfg.afternoon_start_hour, 0), tzinfo=tz),
                    datetime.combine(cur_day, time(cfg.afternoon_end_hour, 0), tzinfo=tz),
                    EnergyLevel.AFTERNOON_MAINTENANCE,
                ),
                (
                    datetime.combine(cur_day, time(cfg.evening_start_hour, 0), tzinfo=tz),
                    datetime.combine(cur_day, time(cfg.evening_end_hour, 0), tzinfo=tz),
                    EnergyLevel.EVENING_UNWIND,
                ),
            ]

            for cand_start, cand_end, energy in day_candidates:
                # Clip candidate window to [start_dt, end_dt]
                effective_start = max(cand_start, start_dt)
                effective_end = min(cand_end, end_dt)

                if effective_end > effective_start:
                    dur_mins = (effective_end - effective_start).total_seconds() / 60.0
                    if dur_mins >= cfg.min_slot_duration_minutes:
                        day_windows.append((effective_start, effective_end, energy))

            cur_day += timedelta(days=1)

        # 3. Subtract merged busy intervals from candidate windows
        available_time_windows: list[TimeWindow] = []
        for win_start, win_end, energy in day_windows:
            free_slices = subtract_busy_from_window(
                window_start=win_start,
                window_end=win_end,
                busy_intervals=merged_busy,
                min_duration_minutes=cfg.min_slot_duration_minutes,
            )
            for slice_start, slice_end in free_slices:
                available_time_windows.append(
                    TimeWindow(
                        start_time=slice_start,
                        end_time=slice_end,
                        energy_level=energy,
                    )
                )

        return sorted(available_time_windows, key=lambda w: w.start_time)

    def insert_event(self, slot: ScheduledSlot, calendar_id: str = "primary") -> bool:
        """Create confirmed event for scheduled slot in Google Calendar."""
        event_start = slot.window.start_time
        event_end = slot.window.start_time + timedelta(
            minutes=slot.video_duration_minutes + slot.buffer_minutes
        )

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{urllib.parse.quote(calendar_id, safe='')}/events"
        )
        description_lines = [
            f"Video: https://youtube.com/watch?v={slot.video_id}",
            f"Playlist: {slot.target_playlist}",
            f"Watch Duration: {slot.video_duration_minutes}m",
            f"Buffer Padding: {slot.buffer_minutes}m",
        ]

        payload = {
            "summary": f"CueTube: {slot.title}",
            "description": "\n".join(description_lines),
            "start": {"dateTime": _format_rfc3339(event_start)},
            "end": {"dateTime": _format_rfc3339(event_end)},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")

        try:
            res = self._execute_request(req)
            return bool(res.get("id") or res.get("status") in ("confirmed", "tentative"))
        except Exception as exc:
            logger.error(
                "Failed to insert Google Calendar event for slot %s: %s",
                slot.slot_id,
                exc,
            )
            return False
