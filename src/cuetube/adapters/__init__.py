"""Adapters satisfying domain protocols."""

from .memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from .takeout import TakeoutArchiveAdapter, TakeoutCsvSource
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
    "TakeoutArchiveAdapter",
    "TakeoutCsvSource",
    "YouTubeApiError",
    "YouTubeApiSource",
    "YouTubeAuthError",
    "YouTubeDataApiAdapter",
    "YouTubePlaylistTarget",
    "YouTubeQuotaExceededError",
    "parse_iso8601_duration",
]
