"""Tests for schedule plan JSON and iCalendar RFC 5545 export adapter."""

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cuetube.adapters.export import (
    export_schedule_plan,
    export_schedule_to_ics,
    export_schedule_to_json,
    schedule_plan_to_dict,
)
from cuetube.domain.models import (
    ChapterMarker,
    CognitiveDemand,
    EnergyLevel,
    EngagementMode,
    ScheduledSlot,
    SchedulePlan,
    TimeWindow,
    TriagedVideo,
    TriageMetadata,
    VideoItem,
)


class TestExportAdapter(unittest.TestCase):
    """Test suite verifying export_schedule_to_json and export_schedule_to_ics."""

    def setUp(self) -> None:
        self.base_time = datetime(2026, 10, 14, 9, 0, 0, tzinfo=UTC)
        win1 = TimeWindow(
            start_time=self.base_time,
            end_time=self.base_time + timedelta(minutes=45),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )
        win2 = TimeWindow(
            start_time=self.base_time + timedelta(hours=10),
            end_time=self.base_time + timedelta(hours=10, minutes=30),
            energy_level=EnergyLevel.EVENING_UNWIND,
        )

        chapters1 = (
            ChapterMarker(title="Introduction", start_seconds=0, end_seconds=300),
            ChapterMarker(title="Core Architecture", start_seconds=300, end_seconds=1800),
        )

        slot1 = ScheduledSlot(
            slot_id="slot-vid-1-1",
            window=win1,
            video_id="vid-1",
            title="Distributed Systems Paxos Deep Dive",
            video_duration_minutes=30,
            buffer_minutes=15,
            target_playlist="Tech-Tutorials",
            summary="A deep dive into Paxos consensus algorithms.",
            chapters=chapters1,
            video_url="https://youtube.com/watch?v=vid-1",
        )

        slot2 = ScheduledSlot(
            slot_id="slot-vid-2-2",
            window=win2,
            video_id="vid-2",
            title="Lofi Coding Beats",
            video_duration_minutes=20,
            buffer_minutes=10,
            target_playlist="Background-Audio",
            summary="Relaxing lofi tracks for evening focus.",
            chapters=(),
            video_url="https://youtube.com/watch?v=vid-2",
        )

        unassigned_video = TriagedVideo(
            item=VideoItem(
                video_id="vid-overflow",
                title="Overflow Video Title",
                channel="Channel",
                duration_seconds=3600,
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=vid-overflow",
            ),
            triage=TriageMetadata(
                video_id="vid-overflow",
                category="Overflow Category",
                cognitive_demand=CognitiveDemand.HIGH_ACTIVE,
                engagement_mode=EngagementMode.ACTIVE,
                recommended_session_duration_minutes=60,
                summary="Overflow summary",
                target_playlist="Study-Queue",
            ),
        )

        self.plan = SchedulePlan(
            slots=(slot1, slot2),
            unassigned_videos=(unassigned_video,),
        )

    def test_schedule_plan_to_dict_and_json(self) -> None:
        data = schedule_plan_to_dict(self.plan)
        self.assertEqual(data["total_slots"], 2)
        self.assertEqual(data["total_unassigned"], 1)

        slot0 = data["slots"][0]
        self.assertEqual(slot0["video_id"], "vid-1")
        self.assertEqual(slot0["energy_level"], "Morning Focus")
        self.assertEqual(len(slot0["chapters"]), 2)
        self.assertEqual(slot0["summary"], "A deep dive into Paxos consensus algorithms.")

        json_output = export_schedule_to_json(self.plan)
        parsed = json.loads(json_output)
        self.assertEqual(parsed["total_slots"], 2)

    def test_export_schedule_to_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "schedule.json"
            export_schedule_to_json(self.plan, output_path=out_file)
            self.assertTrue(out_file.exists())
            content = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(content["total_slots"], 2)

    def test_export_schedule_to_ics_format(self) -> None:
        ics_text = export_schedule_to_ics(self.plan, calendar_name="My Custom Calendar")

        self.assertIn("BEGIN:VCALENDAR", ics_text)
        self.assertIn("VERSION:2.0", ics_text)
        self.assertIn("X-WR-CALNAME:My Custom Calendar", ics_text)
        self.assertIn("BEGIN:VEVENT", ics_text)
        self.assertIn("SUMMARY:CueTube: Distributed Systems Paxos Deep Dive", ics_text)
        self.assertIn("CATEGORIES:Tech-Tutorials", ics_text)
        self.assertIn("URL:https://youtube.com/watch?v=vid-1", ics_text)
        self.assertIn("Energy Tier: Morning Focus", ics_text)
        self.assertIn("Chapters:", ics_text)
        self.assertIn("05:00 Core Architecture", ics_text)
        self.assertIn("SUMMARY:CueTube: Lofi Coding Beats", ics_text)
        self.assertIn("END:VCALENDAR", ics_text)

    def test_export_schedule_plan_auto_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ics_path = Path(tmp_dir) / "export.ics"
            json_path = Path(tmp_dir) / "export.json"

            export_schedule_plan(self.plan, ics_path)
            export_schedule_plan(self.plan, json_path)

            self.assertTrue(ics_path.exists())
            self.assertIn("BEGIN:VCALENDAR", ics_path.read_text(encoding="utf-8"))

            self.assertTrue(json_path.exists())
            self.assertIn('"total_slots": 2', json_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
