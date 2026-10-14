"""CueTube: Autonomous YouTube Backlog Triage and Calendar Scheduling."""

from cuetube.adapters.openrouter import OpenRouterConfig
from cuetube.adapters.youtube import OAuthCredentials
from cuetube.domain.models import (
    BlackoutWindow,
    ChapterMarker,
    CircadianScheduleConfig,
    CognitiveDemand,
    EnergyLevel,
    EngagementMode,
    PipelineSummary,
    ScheduledSlot,
    SchedulePlan,
    TimeWindow,
    Transcript,
    TranscriptSegment,
    TriagedVideo,
    TriageMetadata,
    VideoItem,
    parse_blackout_windows,
)
from cuetube.domain.protocols import (
    CalendarTarget,
    IngestionSource,
    LlmClassifier,
    PlaylistTarget,
    TranscriptProvider,
)
from cuetube.pipeline.runner import (
    PipelineConfig,
    format_draft_schedule_table,
    plan_schedule,
    run_pipeline,
)

__version__ = "0.1.0"

__all__ = [
    "BlackoutWindow",
    "CalendarTarget",
    "ChapterMarker",
    "CircadianScheduleConfig",
    "CognitiveDemand",
    "EnergyLevel",
    "EngagementMode",
    "IngestionSource",
    "LlmClassifier",
    "OAuthCredentials",
    "OpenRouterConfig",
    "PipelineConfig",
    "PipelineSummary",
    "PlaylistTarget",
    "SchedulePlan",
    "ScheduledSlot",
    "TimeWindow",
    "Transcript",
    "TranscriptProvider",
    "TranscriptSegment",
    "TriageMetadata",
    "TriagedVideo",
    "VideoItem",
    "__version__",
    "format_draft_schedule_table",
    "parse_blackout_windows",
    "plan_schedule",
    "run_pipeline",
]
