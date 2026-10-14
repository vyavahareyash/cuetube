"""Integration tests exercising highest seam via run_pipeline with mock adapters."""

import unittest
from datetime import datetime, timedelta
from typing import Any

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

    def test_run_pipeline_controlled_concurrency(self) -> None:
        # Create 6 items to exercise worker pool concurrency
        videos = [
            VideoItem(
                video_id=f"v-{i}",
                title=f"Video Title {i}",
                channel="Channel",
                duration_seconds=600,
                published_at="2026-01-01T00:00:00Z",
                url=f"https://youtube.com/watch?v=v-{i}",
            )
            for i in range(6)
        ]
        inbox_map = {
            "Queue-Inbox": {v.video_id for v in videos},
            "Tech-Tutorials": set(),
            "Explanations-Queue": set(),
            "Background-Audio": set(),
        }
        windows = [
            TimeWindow(
                start_time=self.now + timedelta(hours=i),
                end_time=self.now + timedelta(hours=i + 1),
                energy_level=EnergyLevel.MORNING_FOCUS,
            )
            for i in range(6)
        ]
        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=InMemoryCalendarTarget(windows),
            playlist_target=InMemoryPlaylistTarget(inbox_map),
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
            concurrency=4,
        )

        progress_events = []

        def _track_progress(stage: str, current: int, total: int, msg: str) -> None:
            progress_events.append((stage, current, total, msg))

        summary = run_pipeline(cfg, progress_callback=_track_progress)
        self.assertEqual(summary.total_ingested, 6)
        self.assertEqual(summary.total_classified, 6)
        self.assertEqual(summary.total_scheduled, 6)
        self.assertEqual(summary.total_relocated, 6)
        self.assertEqual(len(summary.errors), 0)
        # Verify progress events were emitted
        triage_events = [e for e in progress_events if e[0] == "triage"]
        self.assertGreater(len(triage_events), 0)

    def test_run_pipeline_concurrency_error_isolation(self) -> None:
        class FlakyMultiClassifier(InMemoryLlmClassifier):
            def classify_video(self, item: VideoItem, transcript: object = None) -> Any:
                if item.video_id == "v-fail":
                    raise RuntimeError("LLM rate limit error")
                return super().classify_video(item, transcript)

        videos = [
            VideoItem(
                video_id="v-ok-1",
                title="OK 1",
                channel="C",
                duration_seconds=600,
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=v-ok-1",
            ),
            VideoItem(
                video_id="v-fail",
                title="Fail Video",
                channel="C",
                duration_seconds=600,
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=v-fail",
            ),
            VideoItem(
                video_id="v-ok-2",
                title="OK 2",
                channel="C",
                duration_seconds=600,
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=v-ok-2",
            ),
        ]
        inbox_map = {
            "Queue-Inbox": {v.video_id for v in videos},
            "Tech-Tutorials": set(),
            "Explanations-Queue": set(),
            "Background-Audio": set(),
        }
        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=FlakyMultiClassifier(),
            calendar_target=InMemoryCalendarTarget([self.morning_slot, self.evening_slot]),
            playlist_target=InMemoryPlaylistTarget(inbox_map),
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
            concurrency=3,
        )

        summary = run_pipeline(cfg)
        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 2)
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 1)
        self.assertEqual(summary.errors[0]["video_id"], "v-fail")

    def test_pipeline_run_skip_playlist_relocation(self) -> None:
        """When relocate_playlists=False, events are scheduled without playlist mutations."""
        inbox_map = {
            "Queue-Inbox": {v.video_id for v in self.videos},
            "Tech-Tutorials": set(),
        }
        playlist_target = InMemoryPlaylistTarget(inbox_map)
        calendar_target = InMemoryCalendarTarget([self.morning_slot, self.afternoon_slot])

        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=calendar_target,
            playlist_target=playlist_target,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg, relocate_playlists=False)
        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 3)
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 0)
        self.assertEqual(len(summary.errors), 0)
        # Calendar events were still created
        self.assertEqual(len(calendar_target.inserted_events), 2)
        # Playlists were not modified
        self.assertEqual(len(playlist_target.playlists["Queue-Inbox"]), 3)
        self.assertEqual(len(playlist_target.playlists["Tech-Tutorials"]), 0)

    def test_pipeline_run_skips_already_scheduled_videos(self) -> None:
        """Videos already present on calendar are deduplicated and skipped before triage."""
        from cuetube.domain.models import ScheduledSlot

        inbox_map = {
            "Queue-Inbox": {v.video_id for v in self.videos},
            "Tech-Tutorials": set(),
        }
        playlist_target = InMemoryPlaylistTarget(inbox_map)
        calendar_target = InMemoryCalendarTarget([self.afternoon_slot, self.evening_slot])

        # Pre-seed calendar with v-tech already scheduled
        pre_existing_slot = ScheduledSlot(
            slot_id="slot-existing",
            window=self.morning_slot,
            video_id="v-tech",
            title="Advanced Distributed Systems Deep Dive Lecture",
            video_duration_minutes=30,
            buffer_minutes=15,
            target_playlist="Tech-Tutorials",
        )
        calendar_target.insert_event(pre_existing_slot)

        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=calendar_target,
            playlist_target=playlist_target,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg)
        # 3 ingested, but 1 (v-tech) already scheduled -> only 2 classified & scheduled
        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 2)
        self.assertEqual(summary.total_scheduled, 2)

    def test_pipeline_config_create_with_direct_params_no_env(self) -> None:
        """PipelineConfig.create initializes properly via direct parameters without ENV."""
        cfg = PipelineConfig.create(
            openrouter_api_key="sk-test-key-12345",
            google_client_id="test-client-id",
            google_client_secret="test-client-secret",
            google_refresh_token="test-refresh-token",
            inbox_playlist_name="Custom-Inbox",
            target_calendar="Custom-Calendar",
            buffer_minutes=25,
            planning_days=10,
            concurrency=2,
            load_env=False,
        )
        self.assertEqual(cfg.inbox_playlist_name, "Custom-Inbox")
        self.assertEqual(cfg.buffer_minutes, 25)
        self.assertEqual(cfg.concurrency, 2)
        self.assertEqual(cfg.classifier.config.api_key, "sk-test-key-12345")
        self.assertEqual(cfg.calendar_target.target_calendar, "Custom-Calendar")

    def test_pipeline_config_create_with_oauth_credentials_object(self) -> None:
        """PipelineConfig.create accepts OAuthCredentials instance directly."""
        from cuetube.adapters.youtube import OAuthCredentials

        creds = OAuthCredentials(
            client_id="cid",
            client_secret="csec",
            refresh_token="cref",
        )
        cfg = PipelineConfig.create(
            openrouter_api_key="sk-test-key",
            google_credentials=creds,
            load_env=False,
        )
        self.assertEqual(cfg.ingestion_source.credentials.client_id, "cid")
        self.assertEqual(cfg.calendar_target.credentials.client_secret, "csec")

    def test_pipeline_config_create_missing_credentials_raises(self) -> None:
        """PipelineConfig.create raises ValueError when credentials missing and load_env=False."""
        with self.assertRaises(ValueError) as ctx:
            PipelineConfig.create(load_env=False)
        self.assertIn("Missing Google/YouTube credentials", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
