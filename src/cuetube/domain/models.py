"""Immutable domain models with strict validation."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CognitiveDemand(StrEnum):
    """Estimated cognitive demand of video content."""

    VERY_HIGH_ACTIVE = "Very High (active)"
    HIGH_ACTIVE = "High (active)"
    MEDIUM_REFLECTIVE = "Medium (reflective)"
    LOW_PASSIVE = "Low (passive)"
    VERY_LOW_PASSIVE = "Very Low (passive)"


class EngagementMode(StrEnum):
    """Viewing mode."""

    ACTIVE = "Active"
    REFLECTIVE = "Reflective"
    PASSIVE = "Passive"


class EnergyLevel(StrEnum):
    """Circadian energy window."""

    MORNING_FOCUS = "Morning Focus"
    AFTERNOON_MAINTENANCE = "Afternoon Maintenance"
    EVENING_UNWIND = "Evening Unwind"


@dataclass(frozen=True)
class VideoItem:
    """Normalized video record."""

    video_id: str
    title: str
    channel: str
    duration_seconds: int
    published_at: str
    url: str
    description: str = ""
    chapters: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.video_id or not self.video_id.strip():
            raise ValueError("video_id must not be empty")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")
        if not self.url or not self.url.strip():
            raise ValueError("url must not be empty")


@dataclass(frozen=True)
class TranscriptSegment:
    """Single timed transcript segment."""

    start_seconds: float
    duration_seconds: float
    text: str

    def __post_init__(self) -> None:
        if self.start_seconds < 0:
            raise ValueError("start_seconds must not be negative")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")


@dataclass(frozen=True)
class Transcript:
    """Extracted transcript text and segments."""

    video_id: str
    text: str
    segments: tuple[TranscriptSegment, ...] = field(default_factory=tuple)
    is_sampled: bool = False
    is_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.video_id or not self.video_id.strip():
            raise ValueError("video_id must not be empty")


@dataclass(frozen=True)
class TriageMetadata:
    """Structured LLM triage classification result."""

    video_id: str
    category: str
    cognitive_demand: CognitiveDemand
    engagement_mode: EngagementMode
    recommended_session_duration_minutes: int
    summary: str
    target_playlist: str

    def __post_init__(self) -> None:
        if not self.video_id or not self.video_id.strip():
            raise ValueError("video_id must not be empty")
        if not self.target_playlist or not self.target_playlist.strip():
            raise ValueError("target_playlist must not be empty")
        if self.recommended_session_duration_minutes < 0:
            raise ValueError("recommended_session_duration_minutes must not be negative")


@dataclass(frozen=True)
class TriagedVideo:
    """Composite item binding video, transcript, and triage classification."""

    item: VideoItem
    triage: TriageMetadata
    transcript: Transcript | None = None

    def __post_init__(self) -> None:
        if self.item.video_id != self.triage.video_id:
            raise ValueError("item.video_id must match triage.video_id")


@dataclass(frozen=True)
class TimeWindow:
    """Calendar time window with energy tier."""

    start_time: datetime
    end_time: datetime
    energy_level: EnergyLevel

    def __post_init__(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly after start_time")

    @property
    def duration_minutes(self) -> int:
        return int((self.end_time - self.start_time).total_seconds() // 60)


@dataclass(frozen=True)
class ScheduledSlot:
    """Assigned calendar viewing slot."""

    slot_id: str
    window: TimeWindow
    video_id: str
    title: str
    video_duration_minutes: int
    buffer_minutes: int
    target_playlist: str

    def __post_init__(self) -> None:
        if not self.slot_id or not self.slot_id.strip():
            raise ValueError("slot_id must not be empty")
        if self.video_duration_minutes + self.buffer_minutes > self.window.duration_minutes:
            raise ValueError("Video duration + buffer exceeds available time window")


@dataclass(frozen=True)
class SchedulePlan:
    """Complete allocation of triaged videos against calendar slots."""

    slots: tuple[ScheduledSlot, ...] = field(default_factory=tuple)
    unassigned_videos: tuple[TriagedVideo, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PipelineSummary:
    """Structured report of pipeline execution."""

    total_ingested: int
    total_classified: int
    total_scheduled: int
    total_relocated: int
    errors: tuple[dict[str, Any], ...] = field(default_factory=tuple)
