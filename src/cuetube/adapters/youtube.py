"""YouTube Data API v3 intake and playlist mutation adapter."""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cuetube.domain.models import VideoItem

logger = logging.getLogger(__name__)

# ISO 8601 duration regex (e.g. PT1H2M10S, PT15M33S, PT45S, P1DT2H)
_ISO8601_DURATION_REGEX = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def _is_youtube_playlist_id(value: str) -> bool:
    """Check if a string matches a YouTube playlist ID pattern."""
    cleaned = value.strip()
    return bool(re.match(r"^(PL|UU|FL|RD)[A-Za-z0-9_-]{16,}$", cleaned)) or cleaned in ("WL", "LL")


def parse_iso8601_duration(raw_duration: str | None) -> int:
    """Parse ISO 8601 duration string into integer seconds."""
    if not raw_duration:
        return 0
    cleaned = raw_duration.strip().upper()
    match = _ISO8601_DURATION_REGEX.match(cleaned)
    if not match:
        return 0

    parts = match.groupdict()
    days = int(parts["days"] or 0)
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    seconds = int(parts["seconds"] or 0)

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class YouTubeApiError(Exception):
    """Base exception for YouTube API interactions."""


class YouTubeAuthError(YouTubeApiError):
    """Raised when OAuth authentication or token refresh fails."""


class YouTubeQuotaExceededError(YouTubeApiError):
    """Raised when YouTube Data API quota is exhausted."""


@dataclass
class OAuthCredentials:
    """OAuth 2.0 credentials supporting automatic token refresh."""

    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str | None = None
    token_expiry: datetime | None = None

    @classmethod
    def from_env(cls, env_prefix: str = "YOUTUBE_") -> "OAuthCredentials":
        """Load credentials from environment variables or local .env file."""
        cid = os.getenv(f"{env_prefix}CLIENT_ID")
        csecret = os.getenv(f"{env_prefix}CLIENT_SECRET")
        rtoken = os.getenv(f"{env_prefix}REFRESH_TOKEN")

        if not (cid and csecret and rtoken):
            env_file = Path(".env")
            if env_file.exists():
                with open(env_file, encoding="utf-8") as f:
                    for line in f:
                        if "=" in line and not line.strip().startswith("#"):
                            k, v = line.strip().split("=", 1)
                            clean_v = v.strip("\"'")
                            if k == f"{env_prefix}CLIENT_ID":
                                cid = clean_v
                            elif k == f"{env_prefix}CLIENT_SECRET":
                                csecret = clean_v
                            elif k == f"{env_prefix}REFRESH_TOKEN":
                                rtoken = clean_v

        if not cid or not csecret or not rtoken:
            raise YouTubeAuthError(
                f"Missing OAuth credentials. Provide {env_prefix}CLIENT_ID, "
                f"{env_prefix}CLIENT_SECRET, and {env_prefix}REFRESH_TOKEN."
            )

        return cls(client_id=cid, client_secret=csecret, refresh_token=rtoken)

    def get_valid_access_token(
        self,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> str:
        """Return valid access token, refreshing from Google OAuth endpoint if expired."""
        now = datetime.now(UTC)
        if (
            self.access_token
            and self.token_expiry
            and self.token_expiry > now + timedelta(seconds=60)
        ):
            return self.access_token

        # Refresh token exchange
        token_url = "https://oauth2.googleapis.com/token"
        body = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")

        req = urllib.request.Request(token_url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            if http_transport:
                resp_data = http_transport(req)
            else:
                with urllib.request.urlopen(req) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))

            self.access_token = resp_data["access_token"]
            expires_in = resp_data.get("expires_in", 3600)
            self.token_expiry = now + timedelta(seconds=expires_in)
            return self.access_token
        except urllib.error.HTTPError as exc:
            err_msg = exc.read().decode("utf-8", errors="replace")
            raise YouTubeAuthError(
                f"OAuth token refresh failed (HTTP {exc.code}): {err_msg}"
            ) from exc
        except Exception as exc:
            raise YouTubeAuthError(f"OAuth token refresh failed: {exc}") from exc


class YouTubeApiSource:
    """IngestionSource fetching backlog from custom intake playlist via YouTube Data API v3."""

    def __init__(
        self,
        credentials: OAuthCredentials,
        playlist_name: str = "Queue-Inbox",
        playlist_id: str | None = None,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
        max_results: int | None = None,
    ) -> None:
        self.credentials = credentials
        self.playlist_name = playlist_name
        self.playlist_id = playlist_id
        self.http_transport = http_transport
        self.max_results = max_results
        self.api_base = "https://www.googleapis.com/youtube/v3"

    def fetch_pending_videos(self) -> list[VideoItem]:
        """Fetch all items from intake playlist, page through results, and hydrate metadata."""
        target_playlist_id = self.playlist_id or self._resolve_playlist_id(self.playlist_name)
        if not target_playlist_id:
            logger.warning(
                "Intake playlist '%s' not found; returning empty list", self.playlist_name
            )
            return []

        # 1. Paged retrieval of playlist items
        raw_items = self._fetch_playlist_items(target_playlist_id)
        if not raw_items:
            return []

        # 2. Extract video IDs and hydrate details (duration, description) via videos.list batching
        video_ids = [
            it["snippet"]["resourceId"]["videoId"]
            for it in raw_items
            if it.get("snippet", {}).get("resourceId", {}).get("videoId")
        ]

        details_by_id = self._fetch_video_details(video_ids)

        # 3. Build domain VideoItem records
        video_items: list[VideoItem] = []
        for it in raw_items:
            vid = it.get("snippet", {}).get("resourceId", {}).get("videoId")
            if not vid:
                continue

            snippet = it.get("snippet", {})
            title = snippet.get("title", f"YouTube Video {vid}")
            channel = snippet.get("videoOwnerChannelTitle") or snippet.get(
                "channelTitle", "Unknown"
            )
            published_at = snippet.get("publishedAt", "")
            url = f"https://www.youtube.com/watch?v={vid}"

            detail = details_by_id.get(vid, {})
            duration_seconds = detail.get("duration_seconds", 0)
            description = detail.get("description", snippet.get("description", ""))

            try:
                item = VideoItem(
                    video_id=vid,
                    title=title,
                    channel=channel,
                    duration_seconds=duration_seconds,
                    published_at=published_at,
                    url=url,
                    description=description,
                    chapters=(),
                )
                video_items.append(item)
            except ValueError as exc:
                logger.warning("Skipping invalid video item %s: %s", vid, exc)

        return video_items

    def _execute_api_request(
        self,
        endpoint: str,
        params: dict[str, str],
        method: str = "GET",
        body_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute authenticated YouTube API request with quota & error translation."""
        access_token = self.credentials.get_valid_access_token(self.http_transport)
        query = urllib.parse.urlencode(params)
        url = f"{self.api_base}/{endpoint}?{query}" if query else f"{self.api_base}/{endpoint}"

        data_bytes = json.dumps(body_data).encode("utf-8") if body_data else None
        req = urllib.request.Request(url, data=data_bytes, method=method)
        req.add_header("Authorization", f"Bearer {access_token}")
        if data_bytes:
            req.add_header("Content-Type", "application/json")

        try:
            if self.http_transport:
                return self.http_transport(req)
            with urllib.request.urlopen(req) as resp:
                content = resp.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                if "quotaExceeded" in err_body or "rateLimitExceeded" in err_body:
                    raise YouTubeQuotaExceededError(
                        "YouTube API quota exceeded: 10,000 units exhausted"
                    ) from exc
            raise YouTubeApiError(f"YouTube API call failed (HTTP {exc.code}): {err_body}") from exc
        except Exception as exc:
            raise YouTubeApiError(f"YouTube API network error: {exc}") from exc

    def _resolve_playlist_id(self, playlist_title: str) -> str | None:
        """Query user's playlists to find playlist ID matching title."""
        if _is_youtube_playlist_id(playlist_title):
            return playlist_title

        next_page = None
        while True:
            params = {"part": "snippet", "mine": "true", "maxResults": "50"}
            if next_page:
                params["pageToken"] = next_page
            res = self._execute_api_request("playlists", params)
            for item in res.get("items", []):
                if (
                    item.get("snippet", {}).get("title", "").strip().lower()
                    == playlist_title.strip().lower()
                ):
                    return item["id"]
            next_page = res.get("nextPageToken")
            if not next_page:
                break

        return None

    def _fetch_playlist_items(self, playlist_id: str) -> list[dict[str, Any]]:
        """Page through playlistItems.list until all items are retrieved or max_results reached."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token

            res = self._execute_api_request("playlistItems", params)
            page_items = res.get("items", [])
            items.extend(page_items)

            if self.max_results and len(items) >= self.max_results:
                items = items[: self.max_results]
                break

            page_token = res.get("nextPageToken")
            if not page_token:
                break

        return items

    def _fetch_video_details(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch-query videos.list in chunks of 50 to extract duration and description."""
        details: dict[str, dict[str, Any]] = {}
        if not video_ids:
            return details

        # 50 video IDs per request (1 quota unit per batch)
        chunk_size = 50
        for i in range(0, len(video_ids), chunk_size):
            chunk = video_ids[i : i + chunk_size]
            params = {
                "part": "snippet,contentDetails",
                "id": ",".join(chunk),
                "maxResults": "50",
            }
            res = self._execute_api_request("videos", params)
            for v in res.get("items", []):
                vid = v.get("id")
                if not vid:
                    continue
                raw_duration = v.get("contentDetails", {}).get("duration")
                duration_seconds = parse_iso8601_duration(raw_duration)
                description = v.get("snippet", {}).get("description", "")
                details[vid] = {
                    "duration_seconds": duration_seconds,
                    "description": description,
                }

        return details


class YouTubePlaylistTarget:
    """PlaylistTarget implementation managing target additions and inbox removals."""

    def __init__(
        self,
        credentials: OAuthCredentials,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        self.credentials = credentials
        self.http_transport = http_transport
        self.api_base = "https://www.googleapis.com/youtube/v3"

        # Caches
        self._playlist_title_to_id: dict[str, str] = {}
        # (playlist_id, video_id) -> playlist_item_id
        self._item_id_map: dict[tuple[str, str], str] = {}

    def add_to_playlist(self, playlist_name: str, video_id: str) -> bool:
        """Insert video into destination playlist via playlistItems.insert (50 quota units)."""
        playlist_id = self._resolve_or_create_playlist(playlist_name)
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        }

        try:
            res = self._execute_api_request(
                "playlistItems", {"part": "snippet"}, method="POST", body_data=body
            )
            item_id = res.get("id")
            if item_id:
                self._item_id_map[(playlist_id, video_id)] = item_id
            return True
        except YouTubeQuotaExceededError:
            raise
        except Exception as exc:
            logger.error("Failed to add video %s to playlist %s: %s", video_id, playlist_name, exc)
            return False

    def remove_from_playlist(self, playlist_name: str, video_id: str) -> bool:
        """Delete video from intake playlist via playlistItems.delete (50 quota units)."""
        playlist_id = self._resolve_playlist_id(playlist_name)
        if not playlist_id:
            logger.warning(
                "Playlist '%s' not found for deletion; treating as completed", playlist_name
            )
            return True

        # Find playlist_item_id
        item_id = self._item_id_map.get((playlist_id, video_id))
        if not item_id:
            item_id = self._lookup_playlist_item_id(playlist_id, video_id)

        if not item_id:
            logger.debug(
                "Video %s not found in playlist %s; already purged", video_id, playlist_name
            )
            return True

        try:
            self._execute_api_request("playlistItems", {"id": item_id}, method="DELETE")
            self._item_id_map.pop((playlist_id, video_id), None)
            return True
        except YouTubeQuotaExceededError:
            raise
        except Exception as exc:
            logger.error(
                "Failed to remove video %s from playlist %s: %s", video_id, playlist_name, exc
            )
            return False

    def _execute_api_request(
        self,
        endpoint: str,
        params: dict[str, str],
        method: str = "GET",
        body_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute authenticated YouTube API request with quota & error translation."""
        access_token = self.credentials.get_valid_access_token(self.http_transport)
        query = urllib.parse.urlencode(params)
        url = f"{self.api_base}/{endpoint}?{query}" if query else f"{self.api_base}/{endpoint}"

        data_bytes = json.dumps(body_data).encode("utf-8") if body_data else None
        req = urllib.request.Request(url, data=data_bytes, method=method)
        req.add_header("Authorization", f"Bearer {access_token}")
        if data_bytes:
            req.add_header("Content-Type", "application/json")

        try:
            if self.http_transport:
                return self.http_transport(req)
            with urllib.request.urlopen(req) as resp:
                content = resp.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 403:
                if "quotaExceeded" in err_body or "rateLimitExceeded" in err_body:
                    raise YouTubeQuotaExceededError(
                        "YouTube API quota exceeded: 10,000 units exhausted"
                    ) from exc
            raise YouTubeApiError(f"YouTube API call failed (HTTP {exc.code}): {err_body}") from exc
        except Exception as exc:
            raise YouTubeApiError(f"YouTube API network error: {exc}") from exc

    def _resolve_playlist_id(self, playlist_title: str) -> str | None:
        """Resolve playlist title to playlist ID using cache or playlists.list."""
        norm_title = playlist_title.strip().lower()
        if norm_title in self._playlist_title_to_id:
            return self._playlist_title_to_id[norm_title]

        next_page = None
        while True:
            params = {"part": "snippet", "mine": "true", "maxResults": "50"}
            if next_page:
                params["pageToken"] = next_page
            res = self._execute_api_request("playlists", params)
            for item in res.get("items", []):
                title = item.get("snippet", {}).get("title", "")
                pid = item.get("id")
                if pid and title:
                    self._playlist_title_to_id[title.strip().lower()] = pid
            next_page = res.get("nextPageToken")
            if not next_page:
                break

        resolved = self._playlist_title_to_id.get(norm_title)
        if not resolved and _is_youtube_playlist_id(playlist_title):
            return playlist_title
        return resolved

    def _resolve_or_create_playlist(self, playlist_title: str) -> str:
        """Resolve existing playlist or create it via playlists.insert (50 quota units)."""
        pid = self._resolve_playlist_id(playlist_title)
        if pid:
            return pid

        # Create playlist
        body = {
            "snippet": {
                "title": playlist_title,
                "description": f"CueTube triaged playlist for {playlist_title}",
            },
            "status": {
                "privacyStatus": "private",
            },
        }
        res = self._execute_api_request(
            "playlists", {"part": "snippet,status"}, method="POST", body_data=body
        )
        new_id = res["id"]
        self._playlist_title_to_id[playlist_title.strip().lower()] = new_id
        return new_id

    def _lookup_playlist_item_id(self, playlist_id: str, video_id: str) -> str | None:
        """Find the playlist item ID corresponding to a video_id inside a playlist."""
        params = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": "50",
        }
        res = self._execute_api_request("playlistItems", params)
        for it in res.get("items", []):
            vid = it.get("snippet", {}).get("resourceId", {}).get("videoId")
            if vid == video_id:
                item_id = it.get("id")
                if item_id:
                    self._item_id_map[(playlist_id, video_id)] = item_id
                return item_id
        return None


class YouTubeDataApiAdapter(YouTubeApiSource, YouTubePlaylistTarget):
    """Unified adapter providing both IngestionSource and PlaylistTarget capabilities."""

    def __init__(
        self,
        credentials: OAuthCredentials,
        playlist_name: str = "Queue-Inbox",
        playlist_id: str | None = None,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
    ) -> None:
        YouTubeApiSource.__init__(
            self,
            credentials=credentials,
            playlist_name=playlist_name,
            playlist_id=playlist_id,
            http_transport=http_transport,
        )
        YouTubePlaylistTarget.__init__(
            self,
            credentials=credentials,
            http_transport=http_transport,
        )


# Alias per architecture documentation
CustomPlaylistAdapter = YouTubeDataApiAdapter
