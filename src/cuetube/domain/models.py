"""Immutable domain models with strict validation."""

from dataclasses import dataclass, field
from datetime import datetime, time
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
class ChapterMarker:
    """Timestamped chapter marker within video."""

    title: str
    start_seconds: int
    end_seconds: int | None = None

    def __post_init__(self) -> None:
        if not self.title or not self.title.strip():
            raise ValueError("title must not be empty")
        if self.start_seconds < 0:
            raise ValueError("start_seconds must not be negative")
        if self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be greater than or equal to start_seconds")


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
    chapters: tuple[ChapterMarker, ...] = field(default_factory=tuple)

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
    is_generated: bool = False
    language: str = "en"
    chapters: tuple[ChapterMarker, ...] = field(default_factory=tuple)

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
class BlackoutWindow:
    """Manual recurring time-of-day blackout window where scheduling is prohibited."""

    start_time: time
    end_time: time
    days_of_week: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)  # 0=Mon .. 6=Sun

    def __post_init__(self) -> None:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly after start_time")
        for d in self.days_of_week:
            if not (0 <= d <= 6):
                raise ValueError("days_of_week entries must be integers within 0..6 (Mon..Sun)")

    @classmethod
    def from_string(
        cls,
        raw: str,
        days_of_week: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6),
    ) -> "BlackoutWindow":
        """Parse 'HH:MM-HH:MM' string into BlackoutWindow."""
        parts = raw.strip().split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid blackout window format '{raw}', expected 'HH:MM-HH:MM'")
        start_parts = [int(p) for p in parts[0].strip().split(":")]
        end_parts = [int(p) for p in parts[1].strip().split(":")]
        start_t = time(start_parts[0], start_parts[1] if len(start_parts) > 1 else 0)
        end_t = time(end_parts[0], end_parts[1] if len(end_parts) > 1 else 0)
        return cls(start_time=start_t, end_time=end_t, days_of_week=days_of_week)


def parse_blackout_windows(raw_str: str) -> tuple[BlackoutWindow, ...]:
    """Parse comma-separated blackout intervals (e.g. '10:00-12:00,14:00-16:30')."""
    if not raw_str or not raw_str.strip():
        return ()
    windows: list[BlackoutWindow] = []
    for item in raw_str.split(","):
        cleaned = item.strip()
        if cleaned:
            windows.append(BlackoutWindow.from_string(cleaned))
    return tuple(windows)


@dataclass(frozen=True)
class CircadianScheduleConfig:
    """Configurable daily circadian energy window bounds and manual blackouts."""

    morning_start_hour: int = 9
    morning_end_hour: int = 12
    afternoon_start_hour: int = 13
    afternoon_end_hour: int = 17
    evening_start_hour: int = 17
    evening_end_hour: int = 21
    min_slot_duration_minutes: int = 15
    blackout_windows: tuple[BlackoutWindow, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not (0 <= self.morning_start_hour < self.morning_end_hour <= 24):
            raise ValueError(
                "morning_start_hour must be strictly less than morning_end_hour and within 0..24"
            )
        if not (0 <= self.afternoon_start_hour < self.afternoon_end_hour <= 24):
            raise ValueError(
                "afternoon_start_hour must be strictly less than afternoon_end_hour "
                "and within 0..24"
            )
        if not (0 <= self.evening_start_hour < self.evening_end_hour <= 24):
            raise ValueError(
                "evening_start_hour must be strictly less than evening_end_hour and within 0..24"
            )
        if self.min_slot_duration_minutes <= 0:
            raise ValueError("min_slot_duration_minutes must be greater than zero")


@dataclass(frozen=True)
class PipelineSummary:
    """Structured report of pipeline execution."""

    total_ingested: int
    total_classified: int
    total_scheduled: int
    total_relocated: int
    errors: tuple[dict[str, Any], ...] = field(default_factory=tuple)
