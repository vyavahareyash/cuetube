"""Integration tests exercising transcript extraction through the highest pipeline seam."""

import json
import unittest
import urllib.request
from datetime import datetime, timedelta

from cuetube.adapters.memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
)
from cuetube.adapters.transcript import YouTubeTranscriptProvider
from cuetube.domain.models import EnergyLevel, TimeWindow, VideoItem
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestTranscriptPipelineIntegration(unittest.TestCase):
    """End-to-end integration tests combining YouTubeTranscriptProvider with run_pipeline."""

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

        # 1. Long lecture with manual captions and description chapters (>2000 words)
        self.long_lecture = VideoItem(
            video_id="vid-lecture-long",
            title="Advanced Distributed Consensus Systems Lecture",
            channel="Tech Academy",
            duration_seconds=3600,
            published_at="2026-01-01T00:00:00Z",
            url="https://youtube.com/watch?v=vid-lecture-long",
            description="""
            Deep technical dive into Paxos and Raft consensus algorithms.
            00:00 Introduction
            05:00 Leader Election
            25:00 Log Replication
            50:00 Safety Invariants
            """,
        )

        # 2. Conceptual explainer with auto-generated ASR captions (<2000 words)
        self.explainer = VideoItem(
            video_id="vid-explainer-short",
            title="Understanding Vector Clocks in 10 Minutes",
            channel="Systems Explained",
            duration_seconds=600,
            published_at="2026-01-02T00:00:00Z",
            url="https://youtube.com/watch?v=vid-explainer-short",
            description="Brief conceptual introduction to vector clocks and causal ordering.",
        )

        # 3. Uncaptioned music video (fallback to title/description)
        self.music_video = VideoItem(
            video_id="vid-music-ambient",
            title="Lofi Ambient Beats for Coding",
            channel="Chill Lofi",
            duration_seconds=1200,
            published_at="2026-01-03T00:00:00Z",
            url="https://youtube.com/watch?v=vid-music-ambient",
            description="Instrumental ambient music. Relax and code.",
        )

        self.videos = [self.long_lecture, self.explainer, self.music_video]

    def _mock_transport(self, req: urllib.request.Request) -> dict:
        url = req.full_url

        if "youtubei/v1/player" in url:
            body = json.loads(req.data.decode("utf-8"))
            vid = body.get("videoId")

            if vid == "vid-lecture-long":
                # Manual English captions
                return {
                    "captions": {
                        "playerCaptionsTracklistRenderer": {
                            "captionTracks": [
                                {
                                    "languageCode": "en",
                                    "baseUrl": "https://timedtext.youtube.com/lecture",
                                }
                            ]
                        }
                    }
                }

            if vid == "vid-explainer-short":
                # Auto-generated (ASR) captions
                return {
                    "captions": {
                        "playerCaptionsTracklistRenderer": {
                            "captionTracks": [
                                {
                                    "languageCode": "en",
                                    "kind": "asr",
                                    "baseUrl": "https://timedtext.youtube.com/explainer",
                                }
                            ]
                        }
                    }
                }

            if vid == "vid-music-ambient":
                # No captions available
                return {"captions": {}}

        if "timedtext.youtube.com/lecture" in url:
            # Generate 2500 words across segments
            events = []
            for i in range(25):
                words = " ".join([f"lectureterm{i * 100 + j}" for j in range(100)])
                events.append(
                    {
                        "tStartMs": i * 60000,
                        "dDurationMs": 50000,
                        "segs": [{"utf8": words}],
                    }
                )
            return {"events": events}

        if "timedtext.youtube.com/explainer" in url:
            # Short 30 words
            return {
                "events": [
                    {
                        "tStartMs": 0,
                        "dDurationMs": 10000,
                        "segs": [
                            {
                                "utf8": (
                                    "Vector clocks track causality across distributed "
                                    "nodes through integer tuples."
                                )
                            }
                        ],
                    }
                ]
            }

        return {}

    def test_run_pipeline_end_to_end_with_transcript_provider(self) -> None:
        ingestion = InMemoryIngestionSource(self.videos)
        transcript_provider = YouTubeTranscriptProvider(
            http_transport=self._mock_transport,
            max_words=2000,
            intro_words=1500,
            outro_words=500,
        )
        classifier = InMemoryLlmClassifier()
        calendar = InMemoryCalendarTarget(self.windows)
        playlist = InMemoryPlaylistTarget(
            {
                "Queue-Inbox": {v.video_id for v in self.videos},
            }
        )

        cfg = PipelineConfig(
            ingestion_source=ingestion,
            transcript_provider=transcript_provider,
            classifier=classifier,
            calendar_target=calendar,
            playlist_target=playlist,
            buffer_minutes=15,
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg)

        # 1. Assert summary metrics
        self.assertEqual(summary.total_ingested, 3)
        self.assertEqual(summary.total_classified, 3)
        self.assertEqual(summary.total_scheduled, 3)
        self.assertEqual(summary.total_relocated, 3)
        self.assertEqual(len(summary.errors), 0)

        # 2. Destination playlists populated
        self.assertIn("vid-lecture-long", playlist.playlists["Tech-Tutorials"])
        self.assertIn("vid-explainer-short", playlist.playlists["Explanations-Queue"])
        self.assertIn("vid-music-ambient", playlist.playlists["Background-Audio"])

        # 3. Intake queue cleared
        self.assertEqual(len(playlist.playlists["Queue-Inbox"]), 0)

        # 4. Calendar events inserted
        self.assertEqual(len(calendar.inserted_events), 3)

        # 5. Long lecture scheduled into Morning Focus slot
        lecture_slot = next(s for s in calendar.inserted_events if s.video_id == "vid-lecture-long")
        self.assertEqual(lecture_slot.window.energy_level, EnergyLevel.MORNING_FOCUS)


if __name__ == "__main__":
    unittest.main()
