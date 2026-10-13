"""Integration tests verifying TakeoutCsvSource through the pipeline runner highest seam."""

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from cuetube.adapters.memory import (
    InMemoryCalendarTarget,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from cuetube.adapters.takeout import TakeoutCsvSource
from cuetube.domain.models import EnergyLevel, TimeWindow
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestTakeoutPipelineIntegration(unittest.TestCase):
    """End-to-end integration tests using TakeoutCsvSource with run_pipeline."""

    def setUp(self) -> None:
        self.fixtures_dir = Path(__file__).parent / "fixtures"
        self.sample_file = self.fixtures_dir / "Coding-videos.csv"
        self.now = datetime(2026, 10, 13, 8, 0, 0)

        # Generous calendar windows to fit batches
        self.windows = [
            TimeWindow(
                start_time=self.now + timedelta(hours=i * 2),
                end_time=self.now + timedelta(hours=i * 2 + 1, minutes=30),
                energy_level=(
                    EnergyLevel.MORNING_FOCUS if i % 2 == 0 else EnergyLevel.EVENING_UNWIND
                ),
            )
            for i in range(30)
        ]

    def test_pipeline_runner_with_real_takeout_archive(self) -> None:
        if not self.sample_file.exists():
            self.skipTest("Sample fixture not found")

        # 1. Takeout adapter reads real CSV
        takeout_source = TakeoutCsvSource(self.sample_file)

        # 2. Pipeline harness components
        transcripts = InMemoryTranscriptProvider()
        classifier = InMemoryLlmClassifier()
        calendar = InMemoryCalendarTarget(self.windows)
        playlist = InMemoryPlaylistTarget({"Queue-Inbox": set()})

        cfg = PipelineConfig(
            ingestion_source=takeout_source,
            transcript_provider=transcripts,
            classifier=classifier,
            calendar_target=calendar,
            playlist_target=playlist,
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=7),
        )

        summary = run_pipeline(cfg)

        # Assert all 25 items ingested, triaged, and dispatched
        self.assertEqual(summary.total_ingested, 25)
        self.assertEqual(summary.total_classified, 25)
        self.assertEqual(summary.total_scheduled, 25)
        self.assertEqual(summary.total_relocated, 25)
        self.assertEqual(len(summary.errors), 0)

        # Assert calendar has 25 events
        self.assertEqual(len(calendar.inserted_events), 25)

        # Assert items placed into target playlists
        total_in_target_playlists = sum(
            len(items) for name, items in playlist.playlists.items() if name != "Queue-Inbox"
        )
        self.assertEqual(total_in_target_playlists, 25)

    def test_pipeline_runner_with_takeout_dedup_and_malformed_rows(self) -> None:
        raw_csv = """Video ID,Playlist Video Creation Timestamp
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
,,
_rwNTY5Mn40,2023-04-20T15:05:07+00:00
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
invalid id with spaces,2023-05-01T00:00:00Z
gb4EQQOsgQc,2023-04-21T18:42:15+00:00
"""
        # Exclude gb4EQQOsgQc as already processed
        already_processed = {"gb4EQQOsgQc"}
        takeout_source = TakeoutCsvSource(raw_csv, existing_ids=already_processed)

        calendar = InMemoryCalendarTarget(self.windows[:5])
        playlist = InMemoryPlaylistTarget({"Queue-Inbox": set()})

        cfg = PipelineConfig(
            ingestion_source=takeout_source,
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=calendar,
            playlist_target=playlist,
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=2),
        )

        summary = run_pipeline(cfg)

        # Raw CSV had 6 rows:
        # - 1 empty row skipped
        # - 1 malformed row skipped
        # - 1 duplicate row skipped
        # - 1 already processed filtered
        # -> exactly 2 unique new items: RPGpA5OLt7M, _rwNTY5Mn40
        self.assertEqual(summary.total_ingested, 2)
        self.assertEqual(summary.total_classified, 2)
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 0)

    def test_pipeline_runner_hydrates_takeout_csv_metadata_via_oembed(self) -> None:
        raw_csv = """Video ID,Playlist Video Creation Timestamp
RPGpA5OLt7M,2023-04-12T05:32:10+00:00
"""
        takeout_source = TakeoutCsvSource(raw_csv)

        def mock_oembed_transport(req):
            if "oembed" in req.full_url:
                return {
                    "title": "Learn Go in 100 Seconds",
                    "author_name": "Fireship",
                }
            return {}

        from cuetube.adapters.transcript import YouTubeTranscriptProvider

        transcripts = YouTubeTranscriptProvider(http_transport=mock_oembed_transport)
        calendar = InMemoryCalendarTarget(self.windows[:2])
        playlist = InMemoryPlaylistTarget({"Queue-Inbox": set()})

        cfg = PipelineConfig(
            ingestion_source=takeout_source,
            transcript_provider=transcripts,
            classifier=InMemoryLlmClassifier(),
            calendar_target=calendar,
            playlist_target=playlist,
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=2),
            hydrate_metadata=True,
        )

        summary = run_pipeline(cfg)
        self.assertEqual(summary.total_ingested, 1)
        self.assertEqual(summary.total_scheduled, 1)

        # Assert calendar slot has the hydrated real title!
        event = calendar.inserted_events[0]
        self.assertEqual(event.title, "Learn Go in 100 Seconds")


if __name__ == "__main__":
    unittest.main()
