"""Unit tests for immutable domain models and invariants."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, time, timedelta

from cuetube.domain.models import (
    BlackoutWindow,
    ChapterMarker,
    CircadianScheduleConfig,
    CognitiveDemand,
    EnergyLevel,
    EngagementMode,
    ScheduledSlot,
    TimeWindow,
    Transcript,
    TranscriptSegment,
    TriagedVideo,
    TriageMetadata,
    VideoItem,
    parse_blackout_windows,
)


class TestDomainModels(unittest.TestCase):
    """Verify domain entity validation and immutability."""

    def test_video_item_immutability_and_validation(self) -> None:
        item = VideoItem(
            video_id="v-1",
            title="Clean Architecture",
            channel="Tech Channel",
            duration_seconds=600,
            published_at="2026-01-01T00:00:00Z",
            url="https://youtube.com/watch?v=v-1",
        )
        self.assertEqual(item.video_id, "v-1")

        # Immutability check
        with self.assertRaises(FrozenInstanceError):
            item.title = "New Title"  # type: ignore

        # Validation checks
        with self.assertRaises(ValueError):
            VideoItem(
                video_id="",
                title="Invalid",
                channel="Ch",
                duration_seconds=10,
                published_at="2026",
                url="url",
            )

        with self.assertRaises(ValueError):
            VideoItem(
                video_id="v-1",
                title="Invalid",
                channel="Ch",
                duration_seconds=-10,
                published_at="2026",
                url="url",
            )

    def test_time_window_duration_and_ordering(self) -> None:
        start = datetime(2026, 1, 1, 10, 0)
        end = start + timedelta(minutes=45)

        window = TimeWindow(start_time=start, end_time=end, energy_level=EnergyLevel.MORNING_FOCUS)
        self.assertEqual(window.duration_minutes, 45)

        with self.assertRaises(ValueError):
            TimeWindow(start_time=end, end_time=start, energy_level=EnergyLevel.MORNING_FOCUS)

    def test_scheduled_slot_buffer_constraint(self) -> None:
        start = datetime(2026, 1, 1, 10, 0)
        window = TimeWindow(
            start_time=start,
            end_time=start + timedelta(minutes=60),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )

        # Valid: 40 + 15 = 55 <= 60
        slot = ScheduledSlot(
            slot_id="s-1",
            window=window,
            video_id="v-1",
            title="Lecture",
            video_duration_minutes=40,
            buffer_minutes=15,
            target_playlist="Study-Queue",
        )
        self.assertEqual(slot.video_duration_minutes, 40)

        # Exceeds: 50 + 15 = 65 > 60 -> ValueError
        with self.assertRaises(ValueError):
            ScheduledSlot(
                slot_id="s-2",
                window=window,
                video_id="v-1",
                title="Lecture",
                video_duration_minutes=50,
                buffer_minutes=15,
                target_playlist="Study-Queue",
            )

    def test_triaged_video_id_consistency(self) -> None:
        item = VideoItem(
            video_id="v-1",
            title="Vid",
            channel="Ch",
            duration_seconds=100,
            published_at="2026",
            url="url",
        )
        triage_matching = TriageMetadata(
            video_id="v-1",
            category="Tech",
            cognitive_demand=CognitiveDemand.HIGH_ACTIVE,
            engagement_mode=EngagementMode.ACTIVE,
            recommended_session_duration_minutes=2,
            summary="Sum",
            target_playlist="PL",
        )
        triaged = TriagedVideo(item=item, triage=triage_matching)
        self.assertEqual(triaged.item.video_id, triaged.triage.video_id)

        triage_mismatch = TriageMetadata(
            video_id="v-2",
            category="Tech",
            cognitive_demand=CognitiveDemand.HIGH_ACTIVE,
            engagement_mode=EngagementMode.ACTIVE,
            recommended_session_duration_minutes=2,
            summary="Sum",
            target_playlist="PL",
        )
        with self.assertRaises(ValueError):
            TriagedVideo(item=item, triage=triage_mismatch)

    def test_chapter_marker_validation(self) -> None:
        marker = ChapterMarker(title="Introduction", start_seconds=0, end_seconds=120)
        self.assertEqual(marker.title, "Introduction")
        self.assertEqual(marker.start_seconds, 0)
        self.assertEqual(marker.end_seconds, 120)

        # Empty title -> ValueError
        with self.assertRaises(ValueError):
            ChapterMarker(title="", start_seconds=0)

        # Negative start_seconds -> ValueError
        with self.assertRaises(ValueError):
            ChapterMarker(title="Intro", start_seconds=-1)

        # end_seconds < start_seconds -> ValueError
        with self.assertRaises(ValueError):
            ChapterMarker(title="Intro", start_seconds=100, end_seconds=50)

    def test_transcript_model_with_chapters_and_generation_flag(self) -> None:
        chapter = ChapterMarker(title="Intro", start_seconds=0, end_seconds=60)
        seg = TranscriptSegment(start_seconds=0.0, duration_seconds=5.0, text="Hello")
        transcript = Transcript(
            video_id="v-1",
            text="Hello world",
            segments=(seg,),
            is_sampled=True,
            is_fallback=False,
            is_generated=True,
            language="en-US",
            chapters=(chapter,),
        )
        self.assertEqual(transcript.video_id, "v-1")
        self.assertTrue(transcript.is_sampled)
        self.assertTrue(transcript.is_generated)
        self.assertEqual(transcript.language, "en-US")
        self.assertEqual(len(transcript.chapters), 1)

    def test_circadian_schedule_config_validation(self) -> None:
        cfg = CircadianScheduleConfig()
        self.assertEqual(cfg.morning_start_hour, 9)
        self.assertEqual(cfg.morning_end_hour, 12)
        self.assertEqual(cfg.afternoon_start_hour, 13)
        self.assertEqual(cfg.afternoon_end_hour, 17)
        self.assertEqual(cfg.evening_start_hour, 17)
        self.assertEqual(cfg.evening_end_hour, 21)
        self.assertEqual(cfg.min_slot_duration_minutes, 15)

        # Invalid hour order -> ValueError
        with self.assertRaises(ValueError):
            CircadianScheduleConfig(morning_start_hour=12, morning_end_hour=9)

        # Invalid hour out of bounds -> ValueError
        with self.assertRaises(ValueError):
            CircadianScheduleConfig(morning_start_hour=-1)

        # Invalid min slot duration -> ValueError
        with self.assertRaises(ValueError):
            CircadianScheduleConfig(min_slot_duration_minutes=0)

    def test_blackout_window_validation_and_parsing(self) -> None:
        bw = BlackoutWindow(start_time=time(10, 0), end_time=time(12, 0))
        self.assertEqual(bw.start_time, time(10, 0))
        self.assertEqual(bw.end_time, time(12, 0))
        self.assertEqual(bw.days_of_week, (0, 1, 2, 3, 4, 5, 6))

        # End time before start time -> ValueError
        with self.assertRaises(ValueError):
            BlackoutWindow(start_time=time(12, 0), end_time=time(10, 0))

        # Invalid day of week -> ValueError
        with self.assertRaises(ValueError):
            BlackoutWindow(
                start_time=time(10, 0),
                end_time=time(12, 0),
                days_of_week=(0, 7),
            )

        # Parse from string
        parsed = BlackoutWindow.from_string("14:30-16:00")
        self.assertEqual(parsed.start_time, time(14, 30))
        self.assertEqual(parsed.end_time, time(16, 0))

        # Parse multiple comma-separated windows
        windows = parse_blackout_windows("10:00-12:00, 14:00-16:30")
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].start_time, time(10, 0))
        self.assertEqual(windows[0].end_time, time(12, 0))
        self.assertEqual(windows[1].start_time, time(14, 0))
        self.assertEqual(windows[1].end_time, time(16, 30))

        # Empty string
        self.assertEqual(parse_blackout_windows(""), ())


if __name__ == "__main__":
    unittest.main()
