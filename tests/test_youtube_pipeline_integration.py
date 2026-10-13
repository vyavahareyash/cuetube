"""Integration tests exercising YouTube Data API adapters through the highest pipeline seam."""

import json
import unittest
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from cuetube.adapters.memory import (
    InMemoryCalendarTarget,
    InMemoryLlmClassifier,
    InMemoryTranscriptProvider,
)
from cuetube.adapters.youtube import (
    OAuthCredentials,
    YouTubeApiSource,
    YouTubePlaylistTarget,
)
from cuetube.domain.models import EnergyLevel, TimeWindow
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestYouTubePipelineIntegration(unittest.TestCase):
    """Integration tests for YouTubeApiSource & YouTubePlaylistTarget with run_pipeline."""

    def setUp(self) -> None:
        self.now = datetime(2026, 10, 13, 8, 0, 0)
        self.windows = [
            TimeWindow(
                start_time=self.now.replace(hour=9, minute=0),
                end_time=self.now.replace(hour=10, minute=30),  # 90m
                energy_level=EnergyLevel.MORNING_FOCUS,
            ),
            TimeWindow(
                start_time=self.now.replace(hour=19, minute=0),
                end_time=self.now.replace(hour=20, minute=0),  # 60m
                energy_level=EnergyLevel.EVENING_UNWIND,
            ),
        ]

        # In-memory mock YouTube API backend state
        self.playlists: dict[str, str] = {
            "queue-inbox": "pl-inbox",
            "tech-tutorials": "pl-tech",
            "background-audio": "pl-music",
        }
        self.inbox_items: dict[str, str] = {
            "vid-lecture": "item-1",
            "vid-music": "item-2",
        }
        self.target_playlist_contents: dict[str, set[str]] = {
            "pl-tech": set(),
            "pl-music": set(),
        }
        self.mutation_log: list[str] = []

    def _mock_transport(self, req: urllib.request.Request) -> dict:
        url = req.full_url
        method = req.get_method()

        # Token refresh
        if "oauth2.googleapis.com" in url:
            return {"access_token": "mock_token", "expires_in": 3600}

        # 1. playlists.list
        if "youtube/v3/playlists" in url:
            items = [
                {"id": pid, "snippet": {"title": name}} for name, pid in self.playlists.items()
            ]
            return {"items": items}

        # 2. playlistItems
        if "youtube/v3/playlistItems" in url:
            if method == "GET":
                # Return items in inbox
                items = [
                    {
                        "id": item_id,
                        "snippet": {
                            "title": "Lecture on Distributed Systems"
                            if vid == "vid-lecture"
                            else "Lofi Ambient Beats",
                            "channelTitle": "Academy" if vid == "vid-lecture" else "Music Studio",
                            "publishedAt": "2026-01-01T00:00:00Z",
                            "resourceId": {"videoId": vid},
                        },
                    }
                    for vid, item_id in self.inbox_items.items()
                ]
                return {"items": items}

            if method == "POST":
                # Insert item into destination playlist
                body = json.loads(req.data.decode("utf-8"))
                pl_id = body["snippet"]["playlistId"]
                vid = body["snippet"]["resourceId"]["videoId"]
                self.target_playlist_contents[pl_id].add(vid)
                self.mutation_log.append(f"INSERT:{pl_id}:{vid}")
                return {"id": f"new-item-{vid}"}

            if method == "DELETE":
                # Delete item from inbox
                query = urllib.parse.urlparse(url).query
                item_id = urllib.parse.parse_qs(query).get("id", [""])[0]
                # Find matching video
                for v, i in list(self.inbox_items.items()):
                    if i == item_id:
                        del self.inbox_items[v]
                        self.mutation_log.append(f"DELETE:{v}")
                        break
                return {}

        # 3. videos.list metadata hydration
        if "youtube/v3/videos" in url:
            return {
                "items": [
                    {
                        "id": "vid-lecture",
                        "contentDetails": {"duration": "PT30M"},
                        "snippet": {"description": "Distributed systems deep dive lecture"},
                    },
                    {
                        "id": "vid-music",
                        "contentDetails": {"duration": "PT20M"},
                        "snippet": {"description": "Ambient focus audio"},
                    },
                ]
            }

        return {}

    def test_run_pipeline_end_to_end_with_youtube_adapters(self) -> None:
        creds = OAuthCredentials("cid", "csec", "rtoken", access_token="mock_token")
        source = YouTubeApiSource(
            credentials=creds,
            playlist_name="Queue-Inbox",
            http_transport=self._mock_transport,
        )
        target = YouTubePlaylistTarget(
            credentials=creds,
            http_transport=self._mock_transport,
        )

        cfg = PipelineConfig(
            ingestion_source=source,
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=InMemoryCalendarTarget(self.windows),
            playlist_target=target,
            buffer_minutes=15,
            inbox_playlist_name="Queue-Inbox",
            planning_start=self.now,
            planning_end=self.now + timedelta(days=1),
        )

        summary = run_pipeline(cfg)

        # 1. Assert summary metrics
        self.assertEqual(summary.total_ingested, 2)
        self.assertEqual(summary.total_classified, 2)
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 0)

        # 2. Assert destination playlists received the videos
        self.assertIn("vid-lecture", self.target_playlist_contents["pl-tech"])
        self.assertIn("vid-music", self.target_playlist_contents["pl-music"])

        # 3. Assert intake queue was cleared
        self.assertEqual(len(self.inbox_items), 0)

        # 4. Verify safe mutation ordering: INSERT before DELETE for each video
        lecture_insert_idx = next(
            i for i, log in enumerate(self.mutation_log) if "INSERT:pl-tech:vid-lecture" in log
        )
        lecture_delete_idx = next(
            i for i, log in enumerate(self.mutation_log) if "DELETE:vid-lecture" in log
        )
        self.assertLess(lecture_insert_idx, lecture_delete_idx)

        music_insert_idx = next(
            i for i, log in enumerate(self.mutation_log) if "INSERT:pl-music:vid-music" in log
        )
        music_delete_idx = next(
            i for i, log in enumerate(self.mutation_log) if "DELETE:vid-music" in log
        )
        self.assertLess(music_insert_idx, music_delete_idx)


if __name__ == "__main__":
    unittest.main()
