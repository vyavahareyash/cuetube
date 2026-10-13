"""Unit tests for immutable domain models and invariants."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

from cuetube.domain.models import (
    CognitiveDemand,
    EnergyLevel,
    EngagementMode,
    ScheduledSlot,
    TimeWindow,
    TriagedVideo,
    TriageMetadata,
    VideoItem,
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


if __name__ == "__main__":
    unittest.main()
