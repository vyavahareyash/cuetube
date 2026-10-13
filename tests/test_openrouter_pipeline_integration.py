"""Integration tests exercising OpenRouterClassifierAdapter through the highest pipeline seam."""

import json
import unittest
import urllib.request
from datetime import datetime, timedelta

from cuetube.adapters.memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from cuetube.adapters.openrouter import (
    OpenRouterClassifierAdapter,
    OpenRouterConfig,
)
from cuetube.domain.models import (
    EnergyLevel,
    TimeWindow,
    Transcript,
    VideoItem,
)
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestOpenRouterPipelineIntegration(unittest.TestCase):
    """End-to-end tests exercising OpenRouterClassifierAdapter within run_pipeline."""

    def setUp(self) -> None:
        self.now = datetime(2026, 10, 13, 8, 0, 0)
        self.windows = [
            TimeWindow(
                start_time=self.now.replace(hour=9, minute=0),
                end_time=self.now.replace(hour=10, minute=30),  # 90m
                energy_level=EnergyLevel.MORNING_FOCUS,
            ),
            TimeWindow(
                start_time=self.now.replace(hour=14, minute=0),
                end_time=self.now.replace(hour=15, minute=0),  # 60m
                energy_level=EnergyLevel.AFTERNOON_MAINTENANCE,
            ),
            TimeWindow(
                start_time=self.now.replace(hour=19, minute=0),
                end_time=self.now.replace(hour=20, minute=0),  # 60m
                energy_level=EnergyLevel.EVENING_UNWIND,
            ),
        ]

        self.videos = [
            VideoItem(
                video_id="v-systems",
                title="Advanced Distributed Storage Systems",
                channel="MIT OpenCourseWare",
                duration_seconds=2400,  # 40 mins
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=v-systems",
                description="LSM-trees and replication consensus.",
            ),
            VideoItem(
                video_id="v-explainer",
                title="How Modern Garbage Collectors Work",
                channel="Computerphile",
                duration_seconds=900,  # 15 mins
                published_at="2026-01-02T00:00:00Z",
                url="https://youtube.com/watch?v=v-explainer",
                description="Generational GC conceptual walkthrough.",
            ),
            VideoItem(
                video_id="v-music",
                title="Coding Lofi Beats to Study to",
                channel="Lofi Girl",
                duration_seconds=1800,  # 30 mins
                published_at="2026-01-03T00:00:00Z",
                url="https://youtube.com/watch?v=v-music",
                description="Relaxing background focus music.",
            ),
        ]

        self.transcripts = {
            "v-systems": Transcript(
                video_id="v-systems",
                text="Lecture discussing LSM-trees and Write Ahead Logging.",
                is_sampled=False,
                is_fallback=False,
            ),
            "v-explainer": Transcript(
                video_id="v-explainer",
                text="Garbage collection automates memory management via generations.",
                is_sampled=False,
                is_fallback=False,
            ),
            # v-music will fallback via InMemoryTranscriptProvider default fallback
        }

    def _mock_openrouter_backend(self, req: urllib.request.Request) -> dict:
        payload = json.loads(req.data.decode("utf-8"))
        user_prompt = payload["messages"][1]["content"]

        if "v-systems" in user_prompt:
            content = {
                "video_id": "v-systems",
                "category": "Academic Lecture",
                "cognitive_demand": "Very High (active)",
                "engagement_mode": "Active",
                "recommended_session_duration_minutes": 45,
                "summary": "MIT lecture on distributed storage and LSM-tree architecture.",
                "target_playlist": "Study-Queue",
            }
        elif "v-explainer" in user_prompt:
            content = {
                "video_id": "v-explainer",
                "category": "Conceptual Explainer",
                "cognitive_demand": "Medium (reflective)",
                "engagement_mode": "Reflective",
                "recommended_session_duration_minutes": 20,
                "summary": "High-level overview of generational garbage collection mechanics.",
                "target_playlist": "Explanations-Queue",
            }
        elif "v-music" in user_prompt:
            content = {
                "video_id": "v-music",
                "category": "Music / Focus",
                "cognitive_demand": "Very Low (passive)",
                "engagement_mode": "Passive",
                "recommended_session_duration_minutes": 30,
                "summary": "Lofi focus music track suited for background listening.",
                "target_playlist": "Background-Audio",
            }
        else:
            raise ValueError(f"Unmatched mock prompt: {user_prompt}")

        return {
            "id": "gen-mock",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}],
        }

    def test_run_pipeline_with_openrouter_classifier(self) -> None:
        config = OpenRouterConfig(api_key="mock-api-key")
        classifier = OpenRouterClassifierAdapter(
            config=config,
            http_transport=self._mock_openrouter_backend,
        )

        inbox_map = {"Queue-Inbox": {v.video_id for v in self.videos}}
        playlist_target = InMemoryPlaylistTarget(inbox_map)
        calendar_target = InMemoryCalendarTarget(self.windows)

        pipe_cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(self.transcripts, default_fallback=True),
            classifier=classifier,
            calendar_target=calendar_target,
            playlist_target=playlist_target,
            buffer_minutes=10,
            hydrate_metadata=False,  # Skip oEmbed network call in unit/integration test
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(pipe_cfg)

        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 3)
        self.assertEqual(summary.total_scheduled, 3)
        self.assertEqual(summary.total_relocated, 3)
        self.assertEqual(len(summary.errors), 0)

        # Verify playlist movements
        self.assertEqual(len(playlist_target.playlists["Queue-Inbox"]), 0)
        self.assertIn("v-systems", playlist_target.playlists["Study-Queue"])
        self.assertIn("v-explainer", playlist_target.playlists["Explanations-Queue"])
        self.assertIn("v-music", playlist_target.playlists["Background-Audio"])

        # Verify chronobiological calendar alignment
        events_by_vid = {e.video_id: e for e in calendar_target.inserted_events}
        # Very high demand -> Morning Focus window
        self.assertEqual(events_by_vid["v-systems"].window.energy_level, EnergyLevel.MORNING_FOCUS)
        # Medium demand -> Afternoon Maintenance window
        self.assertEqual(
            events_by_vid["v-explainer"].window.energy_level, EnergyLevel.AFTERNOON_MAINTENANCE
        )
        # Very low demand -> Evening Unwind window
        self.assertEqual(events_by_vid["v-music"].window.energy_level, EnergyLevel.EVENING_UNWIND)

    def test_run_pipeline_survives_single_item_classification_failure(self) -> None:
        call_count = 0

        def failing_backend(req: urllib.request.Request) -> dict:
            nonlocal call_count
            call_count += 1
            payload = json.loads(req.data.decode("utf-8"))
            if "v-systems" in payload["messages"][1]["content"]:
                raise RuntimeError("LLM inference failed for v-systems")
            return self._mock_openrouter_backend(req)

        config = OpenRouterConfig(api_key="mock-api-key")
        classifier = OpenRouterClassifierAdapter(
            config=config,
            http_transport=failing_backend,
        )

        inbox_map = {"Queue-Inbox": {v.video_id for v in self.videos}}
        playlist_target = InMemoryPlaylistTarget(inbox_map)
        calendar_target = InMemoryCalendarTarget(self.windows)

        pipe_cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(self.transcripts, default_fallback=True),
            classifier=classifier,
            calendar_target=calendar_target,
            playlist_target=playlist_target,
            buffer_minutes=10,
            hydrate_metadata=False,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(pipe_cfg)

        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 2)
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 1)
        self.assertEqual(summary.errors[0]["video_id"], "v-systems")
        self.assertEqual(summary.errors[0]["stage"], "triage")


if __name__ == "__main__":
    unittest.main()
