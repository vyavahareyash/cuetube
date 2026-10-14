"""Full End-to-End Autonomous Pipeline Integration Tests.

Validates the complete autonomous loop:
  Intake Playlist -> Transcript Extraction -> LLM Triage -> Playlist Relocation -> GCal Dispatch
Verifies calendar event formatting (URL, summary, chapters, colorId) and transient error isolation.
"""

import json
import unittest
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from cuetube.adapters.calendar import GoogleCalendarAdapter
from cuetube.adapters.openrouter import OpenRouterClassifierAdapter, OpenRouterConfig
from cuetube.adapters.transcript import YouTubeTranscriptProvider
from cuetube.adapters.youtube import (
    OAuthCredentials,
    YouTubeApiSource,
    YouTubePlaylistTarget,
)
from cuetube.pipeline.runner import PipelineConfig, run_pipeline


class TestE2EAutonomousPipeline(unittest.TestCase):
    """End-to-end highest-seam integration test exercising the full autonomous pipeline."""

    def setUp(self) -> None:
        self.base_time = datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC)
        self.creds = OAuthCredentials(
            client_id="test-client-id",
            client_secret="test-client-secret",
            refresh_token="test-refresh-token",
            access_token="valid-test-access-token",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )

        # Mock API backend state
        self.playlists: dict[str, str] = {
            "queue-inbox": "pl-inbox",
            "tech-tutorials": "pl-tech",
            "background-audio": "pl-music",
        }
        self.inbox_videos = [
            {
                "videoId": "vid-systems",
                "title": "Distributed Systems Paxos Deep Dive",
                "channel": "MIT OpenCourseWare",
                "publishedAt": "2026-01-01T00:00:00Z",
                "duration": "PT30M",
                "description": (
                    "00:00 Intro\n05:00 Consensus\n20:00 Quorums\nPaxos consensus lecture."
                ),
            },
            {
                "videoId": "vid-lofi",
                "title": "Chill Lofi Coding Beats",
                "channel": "Lofi Girl",
                "publishedAt": "2026-01-02T00:00:00Z",
                "duration": "PT20M",
                "description": "Relaxing background audio for evening unwinding.",
            },
        ]
        self.inbox_items: dict[str, str] = {
            "vid-systems": "item-sys-1",
            "vid-lofi": "item-lofi-2",
        }
        self.destination_playlists: dict[str, set[str]] = {
            "pl-tech": set(),
            "pl-music": set(),
        }
        self.calendar_events_inserted: list[dict[str, Any]] = []

    def _mock_unified_transport(self, req: urllib.request.Request) -> Any:
        url = req.full_url
        method = req.get_method()

        # 1. OAuth token refresh
        if "oauth2.googleapis.com" in url:
            return {"access_token": "valid-test-access-token", "expires_in": 3600}

        # 2. YouTube Data API v3: Playlists
        if "youtube/v3/playlists" in url:
            items = [
                {"id": pid, "snippet": {"title": title}} for title, pid in self.playlists.items()
            ]
            return {"items": items}

        # 3. YouTube Data API v3: PlaylistItems
        if "youtube/v3/playlistItems" in url:
            if method == "GET":
                items = []
                for v in self.inbox_videos:
                    vid = v["videoId"]
                    if vid in self.inbox_items:
                        items.append(
                            {
                                "id": self.inbox_items[vid],
                                "snippet": {
                                    "title": v["title"],
                                    "channelTitle": v["channel"],
                                    "publishedAt": v["publishedAt"],
                                    "description": v["description"],
                                    "resourceId": {"videoId": vid},
                                },
                            }
                        )
                return {"items": items}
            if method == "POST":
                body = json.loads(req.data.decode("utf-8"))
                pl_id = body["snippet"]["playlistId"]
                vid = body["snippet"]["resourceId"]["videoId"]
                self.destination_playlists.setdefault(pl_id, set()).add(vid)
                return {"id": f"new-pl-item-{vid}"}
            if method == "DELETE":
                # Extract playlist item id from URL
                parsed = urllib.parse.urlparse(url)
                q = urllib.parse.parse_qs(parsed.query)
                item_id = q.get("id", [""])[0]
                for v_id, it_id in list(self.inbox_items.items()):
                    if it_id == item_id:
                        del self.inbox_items[v_id]
                return {}

        # 4. YouTube Data API v3: Videos detail batch
        if "youtube/v3/videos" in url:
            items = []
            for v in self.inbox_videos:
                items.append(
                    {
                        "id": v["videoId"],
                        "contentDetails": {"duration": v["duration"]},
                        "snippet": {
                            "title": v["title"],
                            "description": v["description"],
                        },
                    }
                )
            return {"items": items}

        # 5. InnerTube / Transcript
        if "youtubei/v1/player" in url:
            # Return empty caption tracks to trigger robust metadata/chapter fallback
            return {"captions": {}}

        # 6. OpenRouter Chat Completions
        if "openrouter.ai" in url:
            payload = json.loads(req.data.decode("utf-8"))
            user_content = payload["messages"][1]["content"]
            if "vid-systems" in user_content:
                triage_data = {
                    "video_id": "vid-systems",
                    "category": "Distributed Systems",
                    "cognitive_demand": "High (active)",
                    "engagement_mode": "Active",
                    "recommended_session_duration_minutes": 30,
                    "summary": "Deep architectural analysis of Paxos consensus protocol.",
                    "target_playlist": "Tech-Tutorials",
                }
            else:
                triage_data = {
                    "video_id": "vid-lofi",
                    "category": "Music & Audio",
                    "cognitive_demand": "Very Low (passive)",
                    "engagement_mode": "Passive",
                    "recommended_session_duration_minutes": 20,
                    "summary": "Relaxing background lofi music.",
                    "target_playlist": "Background-Audio",
                }
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(triage_data),
                        }
                    }
                ]
            }

        # 7. Google Calendar: freeBusy
        if "freeBusy" in url:
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

        # 8. Google Calendar: events.insert
        if "/calendars/primary/events" in url and method == "POST":
            event_body = json.loads(req.data.decode("utf-8"))
            self.calendar_events_inserted.append(event_body)
            return {
                "id": f"gcal-event-{len(self.calendar_events_inserted)}",
                "status": "confirmed",
            }

        return {}

    def test_full_autonomous_loop_success(self) -> None:
        """Verify full loop: ingestion -> transcript -> LLM -> playlist -> calendar."""
        transport = self._mock_unified_transport

        ingestion_source = YouTubeApiSource(
            credentials=self.creds,
            playlist_name="Queue-Inbox",
            http_transport=transport,
        )
        transcript_provider = YouTubeTranscriptProvider(http_transport=transport)
        classifier = OpenRouterClassifierAdapter(
            config=OpenRouterConfig(api_key="test-key"),
            http_transport=transport,
        )
        calendar_target = GoogleCalendarAdapter(
            credentials=self.creds,
            calendar_ids=("primary",),
            http_transport=transport,
        )
        playlist_target = YouTubePlaylistTarget(
            credentials=self.creds,
            http_transport=transport,
        )

        cfg = PipelineConfig(
            ingestion_source=ingestion_source,
            transcript_provider=transcript_provider,
            classifier=classifier,
            calendar_target=calendar_target,
            playlist_target=playlist_target,
            inbox_playlist_name="Queue-Inbox",
            buffer_minutes=15,
            planning_start=self.base_time,
            planning_end=self.base_time + timedelta(days=2),
            hydrate_metadata=False,
        )

        summary = run_pipeline(cfg)

        self.assertEqual(summary.total_ingested, 2)
        self.assertEqual(summary.total_classified, 2)
        self.assertEqual(summary.total_scheduled, 2)
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 0)

        # Verify Google Calendar event contents
        self.assertEqual(len(self.calendar_events_inserted), 2)
        sys_event = next(
            e for e in self.calendar_events_inserted if "vid-systems" in e["description"]
        )
        lofi_event = next(
            e for e in self.calendar_events_inserted if "vid-lofi" in e["description"]
        )

        # Check direct link, summary, chapter markers, and colorId in calendar dispatch
        self.assertIn("watch?v=vid-systems", sys_event["description"])
        self.assertIn("Summary:", sys_event["description"])
        self.assertIn("Deep architectural analysis of Paxos", sys_event["description"])
        self.assertIn("Chapters:", sys_event["description"])
        self.assertIn("05:00 Consensus", sys_event["description"])
        # High cognitive demand is scheduled into morning focus -> colorId '11' (Tomato)
        self.assertEqual(sys_event.get("colorId"), "11")

        # Lofi passive item scheduled into evening unwind -> colorId '2' (Sage)
        self.assertIn("watch?v=vid-lofi", lofi_event["description"])
        self.assertEqual(lofi_event.get("colorId"), "2")

        # Verify playlist mutations:
        # Added to destination playlists
        self.assertIn("vid-systems", self.destination_playlists["pl-tech"])
        self.assertIn("vid-lofi", self.destination_playlists["pl-music"])
        # Removed from intake playlist
        self.assertEqual(len(self.inbox_items), 0)

    def test_transient_failure_isolation(self) -> None:
        """Verify errors on one item (playlist error or calendar error) do not crash the batch."""
        failing_transport_calls = 0

        def flaky_transport(req: urllib.request.Request) -> Any:
            nonlocal failing_transport_calls
            # Fail calendar insert for vid-systems specifically
            if "/calendars/primary/events" in req.full_url:
                body = json.loads(req.data.decode("utf-8"))
                if "vid-systems" in body.get("description", ""):
                    failing_transport_calls += 1
                    raise urllib.error.HTTPError(
                        req.full_url,
                        500,
                        "Internal Server Error",
                        {},
                        None,  # type: ignore
                    )
            return self._mock_unified_transport(req)

        cfg = PipelineConfig(
            ingestion_source=YouTubeApiSource(
                credentials=self.creds, http_transport=flaky_transport
            ),
            transcript_provider=YouTubeTranscriptProvider(http_transport=flaky_transport),
            classifier=OpenRouterClassifierAdapter(
                config=OpenRouterConfig(api_key="test-key"), http_transport=flaky_transport
            ),
            calendar_target=GoogleCalendarAdapter(
                credentials=self.creds, http_transport=flaky_transport
            ),
            playlist_target=YouTubePlaylistTarget(
                credentials=self.creds, http_transport=flaky_transport
            ),
            inbox_playlist_name="Queue-Inbox",
            planning_start=self.base_time,
            planning_end=self.base_time + timedelta(days=2),
            hydrate_metadata=False,
        )

        summary = run_pipeline(cfg)

        self.assertEqual(summary.total_ingested, 2)
        self.assertEqual(summary.total_classified, 2)
        # vid-lofi scheduled (1), vid-systems failed calendar insert
        self.assertEqual(summary.total_scheduled, 1)
        # BUT both still successfully relocated in playlists!
        self.assertEqual(summary.total_relocated, 2)
        self.assertEqual(len(summary.errors), 1)
        self.assertEqual(summary.errors[0]["stage"], "calendar_dispatch")
        self.assertEqual(summary.errors[0]["video_id"], "vid-systems")


if __name__ == "__main__":
    unittest.main()
