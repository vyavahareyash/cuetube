"""Adapters satisfying domain protocols."""

from .memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from .takeout import TakeoutArchiveAdapter, TakeoutCsvSource

__all__ = [
    "InMemoryCalendarTarget",
    "InMemoryIngestionSource",
    "InMemoryLlmClassifier",
    "InMemoryPlaylistTarget",
    "InMemoryTranscriptProvider",
    "TakeoutArchiveAdapter",
    "TakeoutCsvSource",
]
