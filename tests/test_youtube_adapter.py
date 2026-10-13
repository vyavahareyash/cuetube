"""Unit tests for YouTube Data API v3 intake and playlist mutation adapter."""

import json
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cuetube.adapters.youtube import (
    CustomPlaylistAdapter,
    OAuthCredentials,
    YouTubeApiSource,
    YouTubeAuthError,
    YouTubeDataApiAdapter,
    YouTubePlaylistTarget,
    YouTubeQuotaExceededError,
    parse_iso8601_duration,
)


class TestYouTubeAdapter(unittest.TestCase):
    """Test suite for YouTube Data API v3 adapter."""

    def test_parse_iso8601_duration(self) -> None:
        self.assertEqual(parse_iso8601_duration("PT15M33S"), 933)
        self.assertEqual(parse_iso8601_duration("PT1H2M10S"), 3730)
        self.assertEqual(parse_iso8601_duration("PT45S"), 45)
        self.assertEqual(parse_iso8601_duration("PT1H"), 3600)
        self.assertEqual(parse_iso8601_duration("P1DT2H"), 93600)
        self.assertEqual(parse_iso8601_duration(""), 0)
        self.assertEqual(parse_iso8601_duration(None), 0)
        self.assertEqual(parse_iso8601_duration("invalid"), 0)

    def test_oauth_credentials_existing_valid_token(self) -> None:
        now = datetime.now(UTC)
        creds = OAuthCredentials(
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
            access_token="existing_valid_token",
            token_expiry=now + timedelta(hours=1),
        )
        token = creds.get_valid_access_token()
        self.assertEqual(token, "existing_valid_token")

    def test_oauth_credentials_refresh_token(self) -> None:
        def mock_refresh_transport(req: urllib.request.Request) -> dict:
            self.assertEqual(req.full_url, "https://oauth2.googleapis.com/token")
            return {"access_token": "newly_refreshed_token", "expires_in": 3600}

        creds = OAuthCredentials(
            client_id="cid",
            client_secret="csec",
            refresh_token="rtoken",
            access_token=None,
            token_expiry=None,
        )
        token = creds.get_valid_access_token(http_transport=mock_refresh_transport)
        self.assertEqual(token, "newly_refreshed_token")
        self.assertIsNotNone(creds.token_expiry)

    def test_youtube_api_source_paged_ingestion_and_metadata_hydration(self) -> None:
        calls = []

        def mock_api_transport(req: urllib.request.Request) -> dict:
            calls.append(req.full_url)
            if "oauth2.googleapis.com" in req.full_url:
                return {"access_token": "mock_token", "expires_in": 3600}

            if "youtube/v3/playlists" in req.full_url:
                return {
                    "items": [
                        {"id": "pl-inbox-id", "snippet": {"title": "Queue-Inbox"}},
                        {"id": "pl-other-id", "snippet": {"title": "Other Playlist"}},
                    ]
                }

            if "youtube/v3/playlistItems" in req.full_url:
                if "pageToken=page2" in req.full_url:
                    return {
                        "items": [
                            {
                                "id": "pi-2",
                                "snippet": {
                                    "title": "Advanced Distributed Systems",
                                    "channelTitle": "Tech Academy",
                                    "publishedAt": "2026-01-02T00:00:00Z",
                                    "resourceId": {"videoId": "vid-tech-2"},
                                },
                            }
                        ]
                    }
                return {
                    "nextPageToken": "page2",
                    "items": [
                        {
                            "id": "pi-1",
                            "snippet": {
                                "title": "Understanding Raft Consensus",
                                "channelTitle": "Distributed Systems Club",
                                "publishedAt": "2026-01-01T00:00:00Z",
                                "resourceId": {"videoId": "vid-raft-1"},
                            },
                        }
                    ],
                }

            if "youtube/v3/videos" in req.full_url:
                return {
                    "items": [
                        {
                            "id": "vid-raft-1",
                            "contentDetails": {"duration": "PT15M"},
                            "snippet": {"description": "Raft consensus explainer"},
                        },
                        {
                            "id": "vid-tech-2",
                            "contentDetails": {"duration": "PT45M30S"},
                            "snippet": {"description": "Distributed systems deep dive"},
                        },
                    ]
                }

            return {}

        creds = OAuthCredentials("cid", "csec", "rtoken", access_token="mock_token")
        source = YouTubeApiSource(
            creds, playlist_name="Queue-Inbox", http_transport=mock_api_transport
        )
        videos = source.fetch_pending_videos()

        self.assertEqual(len(videos), 2)
        # Check first video
        self.assertEqual(videos[0].video_id, "vid-raft-1")
        self.assertEqual(videos[0].title, "Understanding Raft Consensus")
        self.assertEqual(videos[0].duration_seconds, 900)
        self.assertEqual(videos[0].channel, "Distributed Systems Club")
        self.assertEqual(videos[0].description, "Raft consensus explainer")

        # Check second video (retrieved from page 2)
        self.assertEqual(videos[1].video_id, "vid-tech-2")
        self.assertEqual(videos[1].duration_seconds, 2730)

    def test_youtube_playlist_target_insert_and_delete(self) -> None:
        posted_bodies = []
        deleted_ids = []

        def mock_mutation_transport(req: urllib.request.Request) -> dict:
            if "oauth2.googleapis.com" in req.full_url:
                return {"access_token": "mock_token", "expires_in": 3600}

            if "youtube/v3/playlists" in req.full_url:
                if req.get_method() == "POST":
                    body = json.loads(req.data.decode("utf-8"))
                    return {"id": f"new-pl-{body['snippet']['title']}"}
                return {"items": [{"id": "pl-target-id", "snippet": {"title": "Tech-Tutorials"}}]}

            if "youtube/v3/playlistItems" in req.full_url:
                if req.get_method() == "POST":
                    body = json.loads(req.data.decode("utf-8"))
                    posted_bodies.append(body)
                    return {"id": "inserted-item-123"}
                if req.get_method() == "DELETE":
                    query = urllib.parse.urlparse(req.full_url).query
                    item_id = urllib.parse.parse_qs(query).get("id", [""])[0]
                    deleted_ids.append(item_id)
                    return {}

            return {}

        creds = OAuthCredentials("cid", "csec", "rtoken", access_token="mock_token")
        target = YouTubePlaylistTarget(creds, http_transport=mock_mutation_transport)

        # 1. Add video to target playlist
        added = target.add_to_playlist("Tech-Tutorials", "v-1")
        self.assertTrue(added)
        self.assertEqual(len(posted_bodies), 1)
        self.assertEqual(posted_bodies[0]["snippet"]["playlistId"], "pl-target-id")
        self.assertEqual(posted_bodies[0]["snippet"]["resourceId"]["videoId"], "v-1")

        # 2. Remove video from inbox
        target._item_id_map[("pl-target-id", "v-1")] = "inserted-item-123"
        removed = target.remove_from_playlist("Tech-Tutorials", "v-1")
        self.assertTrue(removed)
        self.assertEqual(deleted_ids, ["inserted-item-123"])

    def test_quota_exceeded_error_translation(self) -> None:
        class QuotaTransportMock:
            def __call__(self, req: urllib.request.Request) -> dict:
                fp = urllib.error.HTTPError(
                    req.full_url,
                    403,
                    "Forbidden",
                    hdrs=None,
                    fp=None,
                )
                fp.read = lambda: b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'
                raise fp

        creds = OAuthCredentials(
            "cid",
            "csec",
            "rtoken",
            access_token="mock_token",
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
        )
        source = YouTubeApiSource(
            creds, playlist_name="Queue-Inbox", http_transport=QuotaTransportMock()
        )

        with self.assertRaises(YouTubeQuotaExceededError):
            source._execute_api_request("playlists", {})

    def test_alias_and_unified_adapter(self) -> None:
        self.assertIs(CustomPlaylistAdapter, YouTubeDataApiAdapter)
        creds = OAuthCredentials("cid", "csec", "rtoken", access_token="mock_token")
        adapter = YouTubeDataApiAdapter(creds)
        self.assertTrue(hasattr(adapter, "fetch_pending_videos"))
        self.assertTrue(hasattr(adapter, "add_to_playlist"))
        self.assertTrue(hasattr(adapter, "remove_from_playlist"))

    def test_live_connection_if_env_configured(self) -> None:
        import os

        if os.getenv("RUN_LIVE_TESTS") != "1":
            self.skipTest("Set RUN_LIVE_TESTS=1 to run against live YouTube API")

        env_file = Path(".env")
        if not env_file.exists():
            self.skipTest("No .env file found; skipping live test")

        try:
            creds = OAuthCredentials.from_env()
        except YouTubeAuthError:
            self.skipTest("OAuth credentials incomplete in .env; skipping live test")

        token = creds.get_valid_access_token()
        self.assertTrue(len(token) > 10)


if __name__ == "__main__":
    unittest.main()
