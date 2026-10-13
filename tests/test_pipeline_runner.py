"""Integration tests exercising highest seam via run_pipeline with mock adapters."""

import unittest
from datetime import datetime, timedelta

from cuetube.adapters.memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from cuetube.domain.models import (
    EnergyLevel,
    TimeWindow,
    VideoItem,
)
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestPipelineRunnerHighestSeam(unittest.TestCase):
    """End-to-end tests exercising the outer pipeline seam."""

    def setUp(self) -> None:
        self.now = datetime(2026, 10, 13, 8, 0, 0)
        self.morning_slot = TimeWindow(
            start_time=self.now.replace(hour=9, minute=0),
            end_time=self.now.replace(hour=10, minute=0),  # 60m
            energy_level=EnergyLevel.MORNING_FOCUS,
        )
        self.afternoon_slot = TimeWindow(
            start_time=self.now.replace(hour=14, minute=0),
            end_time=self.now.replace(hour=14, minute=45),  # 45m
            energy_level=EnergyLevel.AFTERNOON_MAINTENANCE,
        )
        self.evening_slot = TimeWindow(
            start_time=self.now.replace(hour=19, minute=0),
            end_time=self.now.replace(hour=19, minute=40),  # 40m
            energy_level=EnergyLevel.EVENING_UNWIND,
        )

        self.videos = [
            VideoItem(
                video_id="v-tech",
                title="Advanced Distributed Systems Deep Dive Lecture",
                channel="Tech Academy",
                duration_seconds=1800,  # 30 mins
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=v-tech",
            ),
            VideoItem(
                video_id="v-explain",
                title="Understanding Raft Consensus in 15 Minutes",
                channel="Explainers",
                duration_seconds=900,  # 15 mins
                published_at="2026-01-02T00:00:00Z",
                url="https://youtube.com/watch?v=v-explain",
            ),
            VideoItem(
                video_id="v-music",
                title="Lofi Ambient Chill Beats",
                channel="Chill",
                duration_seconds=600,  # 10 mins
                published_at="2026-01-03T00:00:00Z",
                url="https://youtube.com/watch?v=v-music",
            ),
        ]

    def test_run_pipeline_successful_end_to_end(self) -> None:
        ingestion = InMemoryIngestionSource(self.videos)
        transcripts = InMemoryTranscriptProvider()
        classifier = InMemoryLlmClassifier()
        calendar = InMemoryCalendarTarget(
            [
                self.morning_slot,
                self.afternoon_slot,
                self.evening_slot,
            ]
        )
        playlist = InMemoryPlaylistTarget(
            {
                "Queue-Inbox": {"v-tech", "v-explain", "v-music"},
            }
        )

        cfg = PipelineConfig(
            ingestion_source=ingestion,
            transcript_provider=transcripts,
            classifier=classifier,
            calendar_target=calendar,
            playlist_target=playlist,
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg)

        # Assert summary counts
        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 3)
        self.assertEqual(summary.total_scheduled, 3)
        self.assertEqual(summary.total_relocated, 3)
        self.assertEqual(len(summary.errors), 0)

        # Assert destination playlist migrations
        self.assertIn("v-tech", playlist.playlists["Tech-Tutorials"])
        self.assertIn("v-explain", playlist.playlists["Explanations-Queue"])
        self.assertIn("v-music", playlist.playlists["Background-Audio"])

        # Assert Queue-Inbox cleared
        self.assertEqual(len(playlist.playlists["Queue-Inbox"]), 0)

        # Assert calendar slots scheduled
        self.assertEqual(len(calendar.inserted_events), 3)

        # Verify chronobiological alignment:
        # Tech video (High Active) placed in Morning Focus
        tech_event = next(e for e in calendar.inserted_events if e.video_id == "v-tech")
        self.assertEqual(tech_event.window.energy_level, EnergyLevel.MORNING_FOCUS)
        self.assertEqual(tech_event.video_duration_minutes, 30)
        self.assertEqual(tech_event.buffer_minutes, 15)

    def test_run_pipeline_empty_queue(self) -> None:
        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource([]),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=InMemoryCalendarTarget([self.morning_slot]),
            playlist_target=InMemoryPlaylistTarget({"Queue-Inbox": set()}),
        )

        summary = run_pipeline(cfg)
        self.assertEqual(summary.total_ingested, 0)
        self.assertEqual(summary.total_classified, 0)
        self.assertEqual(summary.total_scheduled, 0)
        self.assertEqual(summary.total_relocated, 0)
        self.assertEqual(len(summary.errors), 0)

    def test_run_pipeline_calendar_overflow_preserves_playlist_relocation(self) -> None:
        # Only 1 morning slot available, but 3 videos queued
        calendar = InMemoryCalendarTarget([self.morning_slot])
        playlist = InMemoryPlaylistTarget(
            {
                "Queue-Inbox": {"v-tech", "v-explain", "v-music"},
            }
        )

        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=calendar,
            playlist_target=playlist,
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg)

        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 3)
        self.assertEqual(summary.total_scheduled, 1)  # Only 1 could fit
        self.assertEqual(summary.total_relocated, 3)  # All 3 safely relocated to target playlists!
        self.assertEqual(len(playlist.playlists["Queue-Inbox"]), 0)
        self.assertEqual(len(calendar.inserted_events), 1)

    def test_run_pipeline_transient_failure_isolation(self) -> None:
        class FlakyClassifier(InMemoryLlmClassifier):
            def classify_video(self, item, transcript):
                if item.video_id == "v-explain":
                    raise RuntimeError("Temporary LLM timeout")
                return super().classify_video(item, transcript)

        playlist = InMemoryPlaylistTarget(
            {
                "Queue-Inbox": {"v-tech", "v-explain", "v-music"},
            }
        )

        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=FlakyClassifier(),
            calendar_target=InMemoryCalendarTarget([self.morning_slot, self.evening_slot]),
            playlist_target=playlist,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg)

        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 2)  # v-explain skipped
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 1)
        self.assertEqual(summary.errors[0]["video_id"], "v-explain")
        self.assertIn("Temporary LLM timeout", summary.errors[0]["error"])

        # Failed item remains in inbox
        self.assertIn("v-explain", playlist.playlists["Queue-Inbox"])


if __name__ == "__main__":
    unittest.main()
