"""Adapters satisfying domain protocols."""

from .memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)

__all__ = [
    "InMemoryCalendarTarget",
    "InMemoryIngestionSource",
    "InMemoryLlmClassifier",
    "InMemoryPlaylistTarget",
    "InMemoryTranscriptProvider",
]
