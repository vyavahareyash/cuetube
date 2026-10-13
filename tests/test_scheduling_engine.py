"""Unit tests for chronobiological scheduling engine and slot packing."""

import unittest
from datetime import UTC, datetime

from cuetube.domain.models import (
    CognitiveDemand,
    EnergyLevel,
    EngagementMode,
    TimeWindow,
    TriagedVideo,
    TriageMetadata,
    VideoItem,
)
from cuetube.pipeline.runner import plan_schedule


class TestSchedulingEngine(unittest.TestCase):
    """Test slot packing, circadian energy alignment, and buffer constraint enforcement."""

    def setUp(self) -> None:
        self.base_day = datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC)

    def _make_video(
        self,
        vid_id: str,
        duration_minutes: int,
        demand: CognitiveDemand,
        category: str = "Tech",
        target_playlist: str = "Tech-Tutorials",
    ) -> TriagedVideo:
        item = VideoItem(
            video_id=vid_id,
            title=f"Video {vid_id}",
            channel="Channel",
            duration_seconds=duration_minutes * 60,
            published_at="2026-01-01T00:00:00Z",
            url=f"https://youtube.com/watch?v={vid_id}",
        )
        triage = TriageMetadata(
            video_id=vid_id,
            category=category,
            cognitive_demand=demand,
            engagement_mode=EngagementMode.ACTIVE,
            recommended_session_duration_minutes=duration_minutes,
            summary=f"Summary for {vid_id}",
            target_playlist=target_playlist,
        )
        return TriagedVideo(item=item, triage=triage)

    def test_plan_schedule_multi_video_packing(self) -> None:
        # 3-hour Morning Focus window (09:00 - 12:00 = 180 min)
        morning_window = TimeWindow(
            start_time=self.base_day.replace(hour=9, minute=0),
            end_time=self.base_day.replace(hour=12, minute=0),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )

        v1 = self._make_video("v1", 30, CognitiveDemand.HIGH_ACTIVE)  # 30 + 15 = 45m
        v2 = self._make_video("v2", 45, CognitiveDemand.HIGH_ACTIVE)  # 45 + 15 = 60m
        v3 = self._make_video("v3", 20, CognitiveDemand.HIGH_ACTIVE)  # 20 + 15 = 35m
        # Total needed: 45 + 60 + 35 = 140m <= 180m

        plan = plan_schedule(
            triaged_videos=[v1, v2, v3],
            available_windows=[morning_window],
            buffer_minutes=15,
            min_slot_duration_minutes=15,
        )

        self.assertEqual(len(plan.slots), 3)
        self.assertEqual(len(plan.unassigned_videos), 0)

        # Check chronological contiguous allocation without overlap
        s1, s2, s3 = plan.slots
        self.assertEqual(s1.window.start_time, self.base_day.replace(hour=9, minute=0))
        self.assertEqual(s1.window.end_time, self.base_day.replace(hour=9, minute=45))

        self.assertEqual(s2.window.start_time, self.base_day.replace(hour=9, minute=45))
        self.assertEqual(s2.window.end_time, self.base_day.replace(hour=10, minute=45))

        self.assertEqual(s3.window.start_time, self.base_day.replace(hour=10, minute=45))
        self.assertEqual(s3.window.end_time, self.base_day.replace(hour=11, minute=20))

    def test_plan_schedule_buffer_constraint(self) -> None:
        # Window is 40 minutes
        window_40m = TimeWindow(
            start_time=self.base_day.replace(hour=9, minute=0),
            end_time=self.base_day.replace(hour=9, minute=40),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )

        # 30 min video with 15 min buffer requires 45m > 40m -> cannot fit
        v = self._make_video("v-tight", 30, CognitiveDemand.HIGH_ACTIVE)

        plan_buf15 = plan_schedule(
            triaged_videos=[v],
            available_windows=[window_40m],
            buffer_minutes=15,
        )
        self.assertEqual(len(plan_buf15.slots), 0)
        self.assertEqual(len(plan_buf15.unassigned_videos), 1)

        # With 10 min buffer, 30 + 10 = 40m <= 40m -> fits!
        plan_buf10 = plan_schedule(
            triaged_videos=[v],
            available_windows=[window_40m],
            buffer_minutes=10,
        )
        self.assertEqual(len(plan_buf10.slots), 1)
        self.assertEqual(len(plan_buf10.unassigned_videos), 0)
        self.assertEqual(plan_buf10.slots[0].buffer_minutes, 10)
        self.assertEqual(plan_buf10.slots[0].video_duration_minutes, 30)

    def test_plan_schedule_circadian_alignment_and_priority(self) -> None:
        # Windows across the day
        morning = TimeWindow(
            start_time=self.base_day.replace(hour=9, minute=0),
            end_time=self.base_day.replace(hour=10, minute=0),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )
        afternoon = TimeWindow(
            start_time=self.base_day.replace(hour=14, minute=0),
            end_time=self.base_day.replace(hour=15, minute=0),
            energy_level=EnergyLevel.AFTERNOON_MAINTENANCE,
        )
        evening = TimeWindow(
            start_time=self.base_day.replace(hour=19, minute=0),
            end_time=self.base_day.replace(hour=20, minute=0),
            energy_level=EnergyLevel.EVENING_UNWIND,
        )

        v_deep = self._make_video("deep", 30, CognitiveDemand.VERY_HIGH_ACTIVE)
        v_expl = self._make_video("expl", 25, CognitiveDemand.MEDIUM_REFLECTIVE)
        v_chill = self._make_video("chill", 15, CognitiveDemand.VERY_LOW_PASSIVE)

        # Pass in reverse order to verify demand priority sorting
        plan = plan_schedule(
            triaged_videos=[v_chill, v_expl, v_deep],
            available_windows=[afternoon, evening, morning],
            buffer_minutes=15,
        )

        self.assertEqual(len(plan.slots), 3)
        self.assertEqual(len(plan.unassigned_videos), 0)

        slot_deep = next(s for s in plan.slots if s.video_id == "deep")
        slot_expl = next(s for s in plan.slots if s.video_id == "expl")
        slot_chill = next(s for s in plan.slots if s.video_id == "chill")

        # Deep active matched to Morning Focus
        self.assertEqual(slot_deep.window.energy_level, EnergyLevel.MORNING_FOCUS)
        # Explainer matched to Afternoon Maintenance
        self.assertEqual(slot_expl.window.energy_level, EnergyLevel.AFTERNOON_MAINTENANCE)
        # Chill audio matched to Evening Unwind
        self.assertEqual(slot_chill.window.energy_level, EnergyLevel.EVENING_UNWIND)

    def test_plan_schedule_overflow_handling(self) -> None:
        # 1 slot of 45m available
        single_window = TimeWindow(
            start_time=self.base_day.replace(hour=9, minute=0),
            end_time=self.base_day.replace(hour=9, minute=45),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )

        # 2 videos requiring 45m each
        v1 = self._make_video("v1", 30, CognitiveDemand.HIGH_ACTIVE)
        v2 = self._make_video("v2", 30, CognitiveDemand.HIGH_ACTIVE)

        plan = plan_schedule(
            triaged_videos=[v1, v2],
            available_windows=[single_window],
            buffer_minutes=15,
        )

        self.assertEqual(len(plan.slots), 1)
        self.assertEqual(len(plan.unassigned_videos), 1)
        self.assertEqual(plan.slots[0].video_id, "v1")
        self.assertEqual(plan.unassigned_videos[0].item.video_id, "v2")


if __name__ == "__main__":
    unittest.main()
