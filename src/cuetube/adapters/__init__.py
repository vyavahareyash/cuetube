"""Adapters satisfying domain protocols."""

from .calendar import (
    CalendarApiError,
    CalendarAuthError,
    GoogleCalendarAdapter,
)
from .memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from .openrouter import (
    ClassificationError,
    ClassificationSchemaError,
    OpenRouterApiError,
    OpenRouterAuthError,
    OpenRouterClassifierAdapter,
    OpenRouterConfig,
    OpenRouterRateLimitError,
)
from .takeout import TakeoutArchiveAdapter, TakeoutCsvSource
from .transcript import (
    OfficialTranscriptAdapter,
    YouTubeTranscriptProvider,
    fetch_video_oembed,
    hydrate_video_item,
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

# Architectural aliases
GeminiClassifierAdapter = OpenRouterClassifierAdapter
GCalAdapter = GoogleCalendarAdapter

__all__ = [
    "CalendarApiError",
    "CalendarAuthError",
    "ClassificationError",
    "ClassificationSchemaError",
    "CustomPlaylistAdapter",
    "GCalAdapter",
    "GeminiClassifierAdapter",
    "GoogleCalendarAdapter",
    "InMemoryCalendarTarget",
    "InMemoryIngestionSource",
    "InMemoryLlmClassifier",
    "InMemoryPlaylistTarget",
    "InMemoryTranscriptProvider",
    "OAuthCredentials",
    "OfficialTranscriptAdapter",
    "OpenRouterApiError",
    "OpenRouterAuthError",
    "OpenRouterClassifierAdapter",
    "OpenRouterConfig",
    "OpenRouterRateLimitError",
    "TakeoutArchiveAdapter",
    "TakeoutCsvSource",
    "YouTubeApiError",
    "YouTubeApiSource",
    "YouTubeAuthError",
    "YouTubeDataApiAdapter",
    "YouTubePlaylistTarget",
    "YouTubeQuotaExceededError",
    "YouTubeTranscriptProvider",
    "fetch_video_oembed",
    "hydrate_video_item",
    "parse_chapter_markers",
    "parse_iso8601_duration",
    "parse_timed_text",
    "resolve_caption_track",
    "sample_transcript",
]
