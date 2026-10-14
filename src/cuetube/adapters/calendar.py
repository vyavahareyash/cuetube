"""Google Calendar API v3 availability querying and event insertion adapter."""

import json
import logging
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


def _format_seconds(sec: int) -> str:
    """Format integer seconds into MM:SS or HH:MM:SS string."""
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


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
        target_calendar: str = "primary",
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self.credentials = credentials
        self.calendar_ids = calendar_ids
        self.schedule_config = schedule_config or CircadianScheduleConfig()
        self.include_all_accessible_calendars = include_all_accessible_calendars
        self.target_calendar = (target_calendar or "primary").strip()
        self.http_transport = http_transport
        self._resolved_target_calendar_id: str | None = None

    @classmethod
    def from_env(
        cls,
        calendar_ids: tuple[str, ...] = ("primary",),
        schedule_config: CircadianScheduleConfig | None = None,
        include_all_accessible_calendars: bool = False,
        target_calendar: str | None = None,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> "GoogleCalendarAdapter":
        """Factory initializing credentials from environment with prefix fallbacks."""
        from cuetube.config import get_env_str

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
            schedule_config = CircadianScheduleConfig.from_env()

        effective_target = (
            target_calendar
            if target_calendar is not None
            else (
                get_env_str("CUETUBE_TARGET_CALENDAR", None)
                or get_env_str("CUETUBE_TARGET_CALENDAR_ID", "primary")
                or "primary"
            )
        )

        return cls(
            credentials=creds,
            calendar_ids=calendar_ids,
            schedule_config=schedule_config,
            include_all_accessible_calendars=include_all_accessible_calendars,
            target_calendar=effective_target,
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

    def find_calendar_id_by_name(self, calendar_name: str) -> str | None:
        """Find a calendar ID matching a summary/name from users/me/calendarList."""
        name = calendar_name.strip()
        if not name or name.lower() == "primary":
            return "primary"
        if "@" in name:
            return name

        url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
        req = urllib.request.Request(url, method="GET")
        try:
            data = self._execute_request(req)
            for item in data.get("items", []):
                summary = item.get("summary", "").strip()
                if summary.lower() == name.lower():
                    return item.get("id")
        except Exception as exc:
            logger.warning("Could not search calendarList for '%s': %s", name, exc)
        return None

    def create_calendar(self, calendar_name: str, description: str = "") -> str:
        """Create a new secondary calendar via calendars endpoint."""
        url = "https://www.googleapis.com/calendar/v3/calendars"
        payload = {
            "summary": calendar_name.strip(),
            "description": description or f"CueTube scheduled sessions for {calendar_name}",
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        data = self._execute_request(req)
        cid = data.get("id")
        if not cid:
            raise CalendarApiError(
                f"Failed to create secondary calendar '{calendar_name}': response missing id"
            )
        logger.info("Created secondary Google Calendar '%s' (ID: %s)", calendar_name, cid)
        return str(cid)

    def resolve_target_calendar_id(self, create_if_missing: bool = False) -> str:
        """Resolve target calendar name to ID (creating if missing when requested)."""
        if self._resolved_target_calendar_id:
            return self._resolved_target_calendar_id

        target = (self.target_calendar or "primary").strip()
        if not target or target.lower() == "primary":
            self._resolved_target_calendar_id = "primary"
            return "primary"

        if "@" in target:
            self._resolved_target_calendar_id = target
            return target

        # 1. Check if calendar already exists by summary in calendarList
        found_id = self.find_calendar_id_by_name(target)
        if found_id:
            self._resolved_target_calendar_id = found_id
            return found_id

        # 2. If not found and create_if_missing is True, create it
        if create_if_missing:
            try:
                created_id = self.create_calendar(
                    calendar_name=target,
                    description="CueTube scheduled study and video watch sessions",
                )
                self._resolved_target_calendar_id = created_id
                return created_id
            except Exception as exc:
                logger.error("Failed to create secondary Google Calendar '%s': %s", target, exc)
                return target

        return target

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
        else:
            # Check existing target calendar to prevent double-booking prior slots
            existing_target = self.resolve_target_calendar_id(create_if_missing=False)
            if existing_target and (existing_target == "primary" or "@" in existing_target):
                if existing_target not in target_calendar_ids:
                    target_calendar_ids.append(existing_target)

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

    def insert_event(self, slot: ScheduledSlot, calendar_id: str | None = None) -> bool:
        """Create confirmed event for scheduled slot in Google Calendar."""
        cal_id = calendar_id or self.resolve_target_calendar_id(create_if_missing=True)
        event_start = slot.window.start_time
        event_end = slot.window.start_time + timedelta(
            minutes=slot.video_duration_minutes + slot.buffer_minutes
        )

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{urllib.parse.quote(cal_id, safe='')}/events"
        )
        video_link = slot.video_url or f"https://youtube.com/watch?v={slot.video_id}"
        description_lines = [
            f"Video: {video_link}",
            f"Playlist: {slot.target_playlist}",
            f"Watch Duration: {slot.video_duration_minutes}m",
            f"Buffer Padding: {slot.buffer_minutes}m",
        ]
        if slot.summary and slot.summary.strip():
            description_lines.extend(["", "Summary:", slot.summary.strip()])
        if slot.chapters:
            description_lines.extend(["", "Chapters:"])
            for ch in slot.chapters:
                description_lines.append(f"- {_format_seconds(ch.start_seconds)} {ch.title}")

        color_id = self.schedule_config.energy_colors.get(slot.window.energy_level)
        event_prefix = getattr(self.schedule_config, "event_prefix", "CueTube: ")

        payload: dict[str, Any] = {
            "summary": f"{event_prefix}{slot.title}",
            "description": "\n".join(description_lines),
            "start": {"dateTime": _format_rfc3339(event_start)},
            "end": {"dateTime": _format_rfc3339(event_end)},
        }
        if color_id:
            payload["colorId"] = color_id

        payload["extendedProperties"] = {
            "private": {
                "cuetube_video_id": slot.video_id,
                "cuetube_slot_id": slot.slot_id,
            }
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

    def get_scheduled_video_ids(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> set[str]:
        """Fetch video IDs of events already scheduled on target calendar."""
        import re

        target_cal = self.resolve_target_calendar_id(create_if_missing=False)
        if not target_cal or ("@" not in target_cal and target_cal != "primary"):
            return set()

        params: dict[str, str] = {"singleEvents": "true", "maxResults": "250"}
        if start_time:
            params["timeMin"] = _format_rfc3339(_ensure_aware(start_time))
        if end_time:
            params["timeMax"] = _format_rfc3339(_ensure_aware(end_time))

        query = urllib.parse.urlencode(params)
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/"
            f"{urllib.parse.quote(target_cal, safe='')}/events?{query}"
        )
        req = urllib.request.Request(url, method="GET")
        try:
            data = self._execute_request(req)
        except Exception as exc:
            logger.warning("Could not query existing events from %s: %s", target_cal, exc)
            return set()

        video_ids: set[str] = set()
        for item in data.get("items", []):
            ext_vid = item.get("extendedProperties", {}).get("private", {}).get("cuetube_video_id")
            if ext_vid:
                video_ids.add(ext_vid)
                continue

            desc = item.get("description", "")
            match = re.search(r"watch\?v=([a-zA-Z0-9_-]+)", desc)
            if match:
                video_ids.add(match.group(1))

        return video_ids
