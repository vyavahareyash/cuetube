"""Adapters satisfying domain protocols."""

from .memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from .takeout import TakeoutArchiveAdapter, TakeoutCsvSource
from .transcript import (
    OfficialTranscriptAdapter,
    YouTubeTranscriptProvider,
    parse_chapter_markers,
    parse_timed_text,
    resolve_caption_track,
    sample_transcript,
)
from .youtube import (
    CustomPlaylistAdapter,
    OAuthCredentials,
    YouTubeApiError,
    YouTubeApiSource,
    YouTubeAuthError,
    YouTubeDataApiAdapter,
    YouTubePlaylistTarget,
    YouTubeQuotaExceededError,
    parse_iso8601_duration,
)

__all__ = [
    "CustomPlaylistAdapter",
    "InMemoryCalendarTarget",
    "InMemoryIngestionSource",
    "InMemoryLlmClassifier",
    "InMemoryPlaylistTarget",
    "InMemoryTranscriptProvider",
    "OAuthCredentials",
    "OfficialTranscriptAdapter",
    "TakeoutArchiveAdapter",
    "TakeoutCsvSource",
    "YouTubeApiError",
    "YouTubeApiSource",
    "YouTubeAuthError",
    "YouTubeDataApiAdapter",
    "YouTubePlaylistTarget",
    "YouTubeQuotaExceededError",
    "YouTubeTranscriptProvider",
    "parse_chapter_markers",
    "parse_iso8601_duration",
    "parse_timed_text",
    "resolve_caption_track",
    "sample_transcript",
]
