"""Protocol contracts defining external module seams."""

from datetime import datetime
from typing import Protocol

from .models import (
    ScheduledSlot,
    TimeWindow,
    Transcript,
    TriageMetadata,
    VideoItem,
)


class IngestionSource(Protocol):
    """Seam for fetching pending backlog videos."""

    def fetch_pending_videos(self) -> list[VideoItem]: ...


class TranscriptProvider(Protocol):
    """Seam for extracting video subtitles and chapters."""

    def extract_transcript(self, video_id: str) -> Transcript | None: ...


class LlmClassifier(Protocol):
    """Seam for classifying video content and cognitive load."""

    def classify_video(self, item: VideoItem, transcript: Transcript | None) -> TriageMetadata: ...


class CalendarTarget(Protocol):
    """Seam for calendar queries and event insertion."""

    def query_availability(self, start_time: datetime, end_time: datetime) -> list[TimeWindow]: ...

    def insert_event(self, slot: ScheduledSlot) -> bool: ...


class PlaylistTarget(Protocol):
    """Seam for destination playlist mutations and inbox clearing."""

    def add_to_playlist(self, playlist_name: str, video_id: str) -> bool: ...

    def remove_from_playlist(self, playlist_name: str, video_id: str) -> bool: ...
