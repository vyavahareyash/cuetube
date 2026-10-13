"""Integration tests connecting real GoogleCalendarAdapter through run_pipeline seam."""

import json
import unittest
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from cuetube.adapters.calendar import GoogleCalendarAdapter
from cuetube.adapters.memory import (
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from cuetube.adapters.youtube import OAuthCredentials
from cuetube.domain.models import VideoItem
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestCalendarPipelineIntegration(unittest.TestCase):
    """Integration test with GoogleCalendarAdapter wired through run_pipeline."""

    def setUp(self) -> None:
        self.base_day = datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC)
        self.creds = OAuthCredentials(
            client_id="test-client-id",
            client_secret="test-client-secret",
            refresh_token="test-refresh-token",
            access_token="valid-test-access-token",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )

        self.videos = [
            VideoItem(
                video_id="vid-deep-dive",
                title="Distributed Systems and Paxos Lecture",
                channel="Tech Academy",
                duration_seconds=1800,  # 30m
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=vid-deep-dive",
            ),
            VideoItem(
                video_id="vid-explainer",
                title="Understanding Raft Consensus in Plain English",
                channel="Explainers",
                duration_seconds=900,  # 15m
                published_at="2026-01-02T00:00:00Z",
                url="https://youtube.com/watch?v=vid-explainer",
            ),
            VideoItem(
                video_id="vid-ambient",
                title="Ambient Lofi Beats to Code To",
                channel="Chill Beats",
                duration_seconds=600,  # 10m
                published_at="2026-01-03T00:00:00Z",
                url="https://youtube.com/watch?v=vid-ambient",
            ),
        ]

    def test_run_pipeline_with_google_calendar_adapter(self) -> None:
        inserted_calendar_events: list[dict[str, Any]] = []

        def mock_transport(req: urllib.request.Request) -> dict[str, Any]:
            if "freeBusy" in req.full_url:
                # Calendar availability: morning 10:00-11:00 busy,
                # leaving 09:00-10:00 and 11:00-12:00 free
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
            if "/events" in req.full_url:
                event_data = json.loads(req.data.decode("utf-8"))
                inserted_calendar_events.append(event_data)
                return {
                    "id": f"gcal-event-{len(inserted_calendar_events)}",
                    "status": "confirmed",
                }
            return {}

        calendar_adapter = GoogleCalendarAdapter(
            credentials=self.creds,
            calendar_ids=("primary",),
            http_transport=mock_transport,
        )

        inbox_playlist = InMemoryPlaylistTarget({"Queue-Inbox": {v.video_id for v in self.videos}})

        cfg = PipelineConfig(
            ingestion_source=InMemoryIngestionSource(self.videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=calendar_adapter,
            playlist_target=inbox_playlist,
            buffer_minutes=15,
            planning_start=self.base_day.replace(hour=8),
            planning_end=self.base_day.replace(hour=22),
            hydrate_metadata=False,
        )

        summary = run_pipeline(cfg)

        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 3)
        self.assertEqual(summary.total_scheduled, 3)
        self.assertEqual(summary.total_relocated, 3)
        self.assertEqual(len(summary.errors), 0)

        # Confirm 3 calendar events inserted via Google Calendar adapter
        self.assertEqual(len(inserted_calendar_events), 3)

        # Confirm event summaries and descriptions
        summaries = [e["summary"] for e in inserted_calendar_events]
        self.assertTrue(any("Paxos Lecture" in s for s in summaries))
        self.assertTrue(any("Raft Consensus" in s for s in summaries))
        self.assertTrue(any("Lofi Beats" in s for s in summaries))

        # Destination playlists populated and Queue-Inbox cleared
        self.assertIn("vid-deep-dive", inbox_playlist.playlists["Tech-Tutorials"])
        self.assertIn("vid-explainer", inbox_playlist.playlists["Explanations-Queue"])
        self.assertIn("vid-ambient", inbox_playlist.playlists["Background-Audio"])
        self.assertEqual(len(inbox_playlist.playlists["Queue-Inbox"]), 0)


if __name__ == "__main__":
    unittest.main()
