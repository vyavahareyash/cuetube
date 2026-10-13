"""Unit tests for GoogleCalendarAdapter and interval arithmetic."""

import json
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from cuetube.adapters.calendar import (
    CalendarApiError,
    CalendarAuthError,
    GoogleCalendarAdapter,
    merge_busy_intervals,
    subtract_busy_from_window,
)
from cuetube.adapters.youtube import OAuthCredentials
from cuetube.domain.models import (
    EnergyLevel,
    ScheduledSlot,
    TimeWindow,
)


class TestCalendarIntervalArithmetic(unittest.TestCase):
    """Test pure interval merging and subtraction functions."""

    def setUp(self) -> None:
        self.base_date = datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC)

    def _dt(self, hour: int, minute: int = 0) -> datetime:
        return self.base_date.replace(hour=hour, minute=minute)

    def test_merge_busy_intervals_empty(self) -> None:
        self.assertEqual(merge_busy_intervals([]), [])

    def test_merge_busy_intervals_disjoint(self) -> None:
        iv1 = (self._dt(9, 0), self._dt(10, 0))
        iv2 = (self._dt(11, 0), self._dt(12, 0))
        merged = merge_busy_intervals([iv2, iv1])  # Unsorted input
        self.assertEqual(merged, [iv1, iv2])

    def test_merge_busy_intervals_overlapping_and_nested(self) -> None:
        iv1 = (self._dt(9, 0), self._dt(10, 30))
        iv2 = (self._dt(10, 0), self._dt(11, 0))  # overlaps iv1
        iv3 = (self._dt(10, 15), self._dt(10, 45))  # nested inside
        merged = merge_busy_intervals([iv1, iv2, iv3])
        self.assertEqual(merged, [(self._dt(9, 0), self._dt(11, 0))])

    def test_merge_busy_intervals_contiguous(self) -> None:
        iv1 = (self._dt(9, 0), self._dt(10, 0))
        iv2 = (self._dt(10, 0), self._dt(11, 0))
        merged = merge_busy_intervals([iv1, iv2])
        self.assertEqual(merged, [(self._dt(9, 0), self._dt(11, 0))])

    def test_subtract_busy_from_window_no_busy(self) -> None:
        free = subtract_busy_from_window(
            window_start=self._dt(9, 0),
            window_end=self._dt(12, 0),
            busy_intervals=[],
            min_duration_minutes=15,
        )
        self.assertEqual(free, [(self._dt(9, 0), self._dt(12, 0))])

    def test_subtract_busy_from_window_split_middle(self) -> None:
        busy = [(self._dt(10, 0), self._dt(11, 0))]
        free = subtract_busy_from_window(
            window_start=self._dt(9, 0),
            window_end=self._dt(12, 0),
            busy_intervals=busy,
            min_duration_minutes=15,
        )
        self.assertEqual(
            free,
            [
                (self._dt(9, 0), self._dt(10, 0)),
                (self._dt(11, 0), self._dt(12, 0)),
            ],
        )

    def test_subtract_busy_from_window_busy_overlaps_boundaries(self) -> None:
        # Busy starts before window and ends at 9:45
        busy = [(self._dt(8, 30), self._dt(9, 45))]
        free = subtract_busy_from_window(
            window_start=self._dt(9, 0),
            window_end=self._dt(12, 0),
            busy_intervals=busy,
            min_duration_minutes=15,
        )
        self.assertEqual(free, [(self._dt(9, 45), self._dt(12, 0))])

    def test_subtract_busy_from_window_prunes_short_segments(self) -> None:
        # Busy leaves a 10 min window (9:00 to 9:10) and a 50 min window (10:10 to 11:00)
        busy = [(self._dt(9, 10), self._dt(10, 10))]
        free = subtract_busy_from_window(
            window_start=self._dt(9, 0),
            window_end=self._dt(11, 0),
            busy_intervals=busy,
            min_duration_minutes=15,  # 10m is discarded
        )
        self.assertEqual(free, [(self._dt(10, 10), self._dt(11, 0))])

    def test_subtract_busy_from_window_completely_covered(self) -> None:
        busy = [(self._dt(8, 0), self._dt(13, 0))]
        free = subtract_busy_from_window(
            window_start=self._dt(9, 0),
            window_end=self._dt(12, 0),
            busy_intervals=busy,
            min_duration_minutes=15,
        )
        self.assertEqual(free, [])


class TestGoogleCalendarAdapter(unittest.TestCase):
    """Test GoogleCalendarAdapter external API interaction via mock transport."""

    def setUp(self) -> None:
        self.creds = OAuthCredentials(
            client_id="dummy-client-id",
            client_secret="dummy-client-secret",
            refresh_token="dummy-refresh-token",
            access_token="valid-test-access-token",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
        self.base_day = datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC)

    def test_query_availability_single_calendar_success(self) -> None:
        recorded_requests: list[urllib.request.Request] = []

        def mock_transport(req: urllib.request.Request) -> dict[str, Any]:
            recorded_requests.append(req)
            if "freeBusy" in req.full_url:
                return {
                    "calendars": {
                        "primary": {
                            "busy": [
                                {
                                    "start": "2026-10-14T10:00:00Z",
                                    "end": "2026-10-14T11:00:00Z",
                                }
                            ]
                        }
                    }
                }
            return {}

        adapter = GoogleCalendarAdapter(
            credentials=self.creds,
            calendar_ids=("primary",),
            http_transport=mock_transport,
        )

        start_time = self.base_day.replace(hour=8, minute=0)
        end_time = self.base_day.replace(hour=22, minute=0)

        windows = adapter.query_availability(start_time, end_time)

        self.assertEqual(len(recorded_requests), 1)
        req = recorded_requests[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer valid-test-access-token")

        req_body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(req_body["items"], [{"id": "primary"}])

        # Assert resulting TimeWindows
        # Morning Focus (09:00 - 12:00) with 10:00 - 11:00 busy splits into:
        # 1. 09:00 - 10:00 (MORNING_FOCUS)
        # 2. 11:00 - 12:00 (MORNING_FOCUS)
        # Afternoon Maintenance: 13:00 - 17:00 (AFTERNOON_MAINTENANCE)
        # Evening Unwind: 17:00 - 21:00 (EVENING_UNWIND)
        self.assertEqual(len(windows), 4)

        w1, w2, w3, w4 = windows
        self.assertEqual(w1.energy_level, EnergyLevel.MORNING_FOCUS)
        self.assertEqual(w1.start_time, self.base_day.replace(hour=9, minute=0))
        self.assertEqual(w1.end_time, self.base_day.replace(hour=10, minute=0))

        self.assertEqual(w2.energy_level, EnergyLevel.MORNING_FOCUS)
        self.assertEqual(w2.start_time, self.base_day.replace(hour=11, minute=0))
        self.assertEqual(w2.end_time, self.base_day.replace(hour=12, minute=0))

        self.assertEqual(w3.energy_level, EnergyLevel.AFTERNOON_MAINTENANCE)
        self.assertEqual(w3.start_time, self.base_day.replace(hour=13, minute=0))
        self.assertEqual(w3.end_time, self.base_day.replace(hour=17, minute=0))

        self.assertEqual(w4.energy_level, EnergyLevel.EVENING_UNWIND)
        self.assertEqual(w4.start_time, self.base_day.replace(hour=17, minute=0))
        self.assertEqual(w4.end_time, self.base_day.replace(hour=21, minute=0))

    def test_query_availability_multi_calendar_and_shared_calendar_discovery(self) -> None:
        recorded_requests: list[urllib.request.Request] = []

        def mock_transport(req: urllib.request.Request) -> dict[str, Any]:
            recorded_requests.append(req)
            if "calendarList" in req.full_url:
                return {
                    "items": [
                        {"id": "primary"},
                        {"id": "work-shared@company.com"},
                        {"id": "partner@gmail.com"},
                    ]
                }
            if "freeBusy" in req.full_url:
                return {
                    "calendars": {
                        "primary": {
                            "busy": [
                                {
                                    "start": "2026-10-14T09:00:00Z",
                                    "end": "2026-10-14T10:00:00Z",
                                }
                            ]
                        },
                        "work-shared@company.com": {
                            "busy": [
                                {
                                    "start": "2026-10-14T14:00:00Z",
                                    "end": "2026-10-14T15:00:00Z",
                                }
                            ]
                        },
                        "partner@gmail.com": {
                            "busy": [
                                {
                                    "start": "2026-10-14T18:00:00Z",
                                    "end": "2026-10-14T19:00:00Z",
                                }
                            ]
                        },
                    }
                }
            return {}

        adapter = GoogleCalendarAdapter(
            credentials=self.creds,
            calendar_ids=("primary",),
            include_all_accessible_calendars=True,
            http_transport=mock_transport,
        )

        start_time = self.base_day.replace(hour=8, minute=0)
        end_time = self.base_day.replace(hour=22, minute=0)

        windows = adapter.query_availability(start_time, end_time)

        # calendarList + freeBusy = 2 calls
        self.assertEqual(len(recorded_requests), 2)
        freebusy_body = json.loads(recorded_requests[1].data.decode("utf-8"))
        item_ids = [item["id"] for item in freebusy_body["items"]]
        self.assertIn("primary", item_ids)
        self.assertIn("work-shared@company.com", item_ids)
        self.assertIn("partner@gmail.com", item_ids)

        # Check collision avoidance across all 3 calendars:
        for w in windows:
            # Primary busy 09:00 - 10:00
            self.assertFalse(
                w.start_time < self.base_day.replace(hour=10)
                and w.end_time > self.base_day.replace(hour=9)
            )
            # Work busy 14:00 - 15:00
            self.assertFalse(
                w.start_time < self.base_day.replace(hour=15)
                and w.end_time > self.base_day.replace(hour=14)
            )
            # Partner busy 18:00 - 19:00
            self.assertFalse(
                w.start_time < self.base_day.replace(hour=19)
                and w.end_time > self.base_day.replace(hour=18)
            )

    def test_insert_event_success(self) -> None:
        recorded_requests: list[urllib.request.Request] = []

        def mock_transport(req: urllib.request.Request) -> dict[str, Any]:
            recorded_requests.append(req)
            return {
                "id": "event-12345",
                "status": "confirmed",
                "htmlLink": "https://calendar.google.com/event?eid=123",
            }

        adapter = GoogleCalendarAdapter(
            credentials=self.creds,
            http_transport=mock_transport,
        )

        window = TimeWindow(
            start_time=self.base_day.replace(hour=9, minute=0),
            end_time=self.base_day.replace(hour=9, minute=45),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )
        slot = ScheduledSlot(
            slot_id="slot-vid-1-1",
            window=window,
            video_id="vid-1",
            title="Advanced Rust Systems Programming",
            video_duration_minutes=30,
            buffer_minutes=15,
            target_playlist="Tech-Tutorials",
        )

        success = adapter.insert_event(slot)
        self.assertTrue(success)
        self.assertEqual(len(recorded_requests), 1)

        req = recorded_requests[0]
        self.assertIn("/calendars/primary/events", req.full_url)
        body = json.loads(req.data.decode("utf-8"))
        self.assertIn("CueTube: Advanced Rust Systems Programming", body["summary"])
        self.assertIn("https://youtube.com/watch?v=vid-1", body["description"])
        self.assertIn("30m", body["description"])
        self.assertIn("Buffer Padding: 15m", body["description"])
        self.assertEqual(body["start"]["dateTime"], "2026-10-14T09:00:00+00:00")
        self.assertEqual(body["end"]["dateTime"], "2026-10-14T09:45:00+00:00")

    def test_calendar_adapter_error_handling(self) -> None:
        import io

        def auth_error_transport(req: urllib.request.Request) -> Any:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=401,
                msg="Unauthorized",
                hdrs=urllib.response.addinfourl(None, {}, "").info(),  # type: ignore
                fp=io.BytesIO(b"Unauthorized"),
            )

        adapter = GoogleCalendarAdapter(
            credentials=self.creds,
            http_transport=auth_error_transport,
        )

        with self.assertRaises(CalendarAuthError):
            adapter.query_availability(
                self.base_day.replace(hour=8),
                self.base_day.replace(hour=18),
            )

        def server_error_transport(req: urllib.request.Request) -> Any:
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=500,
                msg="Internal Server Error",
                hdrs=urllib.response.addinfourl(None, {}, "").info(),  # type: ignore
                fp=io.BytesIO(b"Internal Server Error"),
            )

        adapter_server_err = GoogleCalendarAdapter(
            credentials=self.creds,
            http_transport=server_error_transport,
        )

        with self.assertRaises(CalendarApiError):
            adapter_server_err.query_availability(
                self.base_day.replace(hour=8),
                self.base_day.replace(hour=18),
            )


if __name__ == "__main__":
    unittest.main()
