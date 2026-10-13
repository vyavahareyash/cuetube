"""Pipeline runner and chronobiological schedule planner."""

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from cuetube.adapters.memory import (
    InMemoryCalendarTarget,
    InMemoryIngestionSource,
    InMemoryLlmClassifier,
    InMemoryPlaylistTarget,
    InMemoryTranscriptProvider,
)
from cuetube.adapters.transcript import hydrate_video_item
from cuetube.domain.models import (
    CognitiveDemand,
    EnergyLevel,
    PipelineSummary,
    ScheduledSlot,
    SchedulePlan,
    TimeWindow,
    TriagedVideo,
    VideoItem,
)
from cuetube.domain.protocols import (
    CalendarTarget,
    IngestionSource,
    LlmClassifier,
    PlaylistTarget,
    TranscriptProvider,
)


@dataclass
class PipelineConfig:
    """Configuration for pipeline execution."""

    ingestion_source: IngestionSource
    transcript_provider: TranscriptProvider
    classifier: LlmClassifier
    calendar_target: CalendarTarget
    playlist_target: PlaylistTarget
    buffer_minutes: int = 15
    inbox_playlist_name: str = "Queue-Inbox"
    planning_start: datetime | None = None
    planning_end: datetime | None = None
    hydrate_metadata: bool = True

    @classmethod
    def create_mock_default(
        cls,
        videos: list[VideoItem] | None = None,
        available_windows: list[TimeWindow] | None = None,
        buffer_minutes: int = 15,
    ) -> "PipelineConfig":
        """Convenience factory creating default mock configuration."""
        now = datetime.now()
        start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        default_videos = videos or [
            VideoItem(
                video_id="vid-1",
                title="Advanced Distributed Systems Deep Dive",
                channel="Tech Academy",
                duration_seconds=1800,  # 30 mins
                published_at="2026-01-01T00:00:00Z",
                url="https://youtube.com/watch?v=vid-1",
            ),
            VideoItem(
                video_id="vid-2",
                title="Lofi Focus Beats to Code To",
                channel="Chill Beats",
                duration_seconds=1200,  # 20 mins
                published_at="2026-01-02T00:00:00Z",
                url="https://youtube.com/watch?v=vid-2",
            ),
        ]

        default_windows = available_windows or [
            TimeWindow(
                start_time=start,
                end_time=start + timedelta(minutes=60),
                energy_level=EnergyLevel.MORNING_FOCUS,
            ),
            TimeWindow(
                start_time=start + timedelta(hours=9),
                end_time=start + timedelta(hours=10),
                energy_level=EnergyLevel.EVENING_UNWIND,
            ),
        ]

        inbox_map = {"Queue-Inbox": {v.video_id for v in default_videos}}
        return cls(
            ingestion_source=InMemoryIngestionSource(default_videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=InMemoryCalendarTarget(default_windows),
            playlist_target=InMemoryPlaylistTarget(inbox_map),
            buffer_minutes=buffer_minutes,
            planning_start=start - timedelta(hours=1),
            planning_end=end,
        )


def _energy_compatibility_score(demand: CognitiveDemand, window_energy: EnergyLevel) -> int:
    """Higher score indicates better cognitive/energy alignment."""
    if demand in (CognitiveDemand.VERY_HIGH_ACTIVE, CognitiveDemand.HIGH_ACTIVE):
        if window_energy == EnergyLevel.MORNING_FOCUS:
            return 3
        if window_energy == EnergyLevel.AFTERNOON_MAINTENANCE:
            return 2
        return 1

    if demand == CognitiveDemand.MEDIUM_REFLECTIVE:
        if window_energy == EnergyLevel.AFTERNOON_MAINTENANCE:
            return 3
        if window_energy == EnergyLevel.EVENING_UNWIND:
            return 2
        return 1

    # Low or Very Low Passive
    if window_energy == EnergyLevel.EVENING_UNWIND:
        return 3
    if window_energy == EnergyLevel.AFTERNOON_MAINTENANCE:
        return 2
    return 1


def plan_schedule(
    triaged_videos: list[TriagedVideo],
    available_windows: list[TimeWindow],
    buffer_minutes: int = 15,
) -> SchedulePlan:
    """Match triaged videos against available calendar windows using energy tiers and buffers."""
    remaining_windows = list(available_windows)
    scheduled_slots: list[ScheduledSlot] = []
    unassigned_videos: list[TriagedVideo] = []

    # Sort videos by cognitive demand priority (demanding items first)
    demand_order = {
        CognitiveDemand.VERY_HIGH_ACTIVE: 0,
        CognitiveDemand.HIGH_ACTIVE: 1,
        CognitiveDemand.MEDIUM_REFLECTIVE: 2,
        CognitiveDemand.LOW_PASSIVE: 3,
        CognitiveDemand.VERY_LOW_PASSIVE: 4,
    }
    sorted_videos = sorted(
        triaged_videos,
        key=lambda tv: demand_order.get(tv.triage.cognitive_demand, 5),
    )

    for tv in sorted_videos:
        video_mins = math.ceil(tv.item.duration_seconds / 60)
        required_mins = video_mins + buffer_minutes

        best_window_idx: int | None = None
        best_score = -1

        for idx, w in enumerate(remaining_windows):
            if required_mins <= w.duration_minutes:
                score = _energy_compatibility_score(tv.triage.cognitive_demand, w.energy_level)
                if score > best_score:
                    best_score = score
                    best_window_idx = idx

        if best_window_idx is not None:
            chosen_window = remaining_windows.pop(best_window_idx)
            slot = ScheduledSlot(
                slot_id=f"slot-{tv.item.video_id}-{len(scheduled_slots) + 1}",
                window=chosen_window,
                video_id=tv.item.video_id,
                title=tv.item.title,
                video_duration_minutes=video_mins,
                buffer_minutes=buffer_minutes,
                target_playlist=tv.triage.target_playlist,
            )
            scheduled_slots.append(slot)
        else:
            unassigned_videos.append(tv)

    return SchedulePlan(
        slots=tuple(scheduled_slots),
        unassigned_videos=tuple(unassigned_videos),
    )


def run_pipeline(cfg: PipelineConfig) -> PipelineSummary:
    """Execute end-to-end pipeline pass through highest seam."""
    errors: list[dict[str, Any]] = []

    # 1. Ingestion
    try:
        pending_videos = cfg.ingestion_source.fetch_pending_videos()
    except Exception as exc:
        return PipelineSummary(
            total_ingested=0,
            total_classified=0,
            total_scheduled=0,
            total_relocated=0,
            errors=({"stage": "ingestion", "error": str(exc)},),
        )

    # 2. Extraction & Classification
    triaged_videos: list[TriagedVideo] = []
    transport = getattr(cfg.transcript_provider, "http_transport", None)
    for item in pending_videos:
        try:
            if cfg.hydrate_metadata:
                item = hydrate_video_item(item, http_transport=transport)

            transcript = cfg.transcript_provider.extract_transcript(
                item.video_id,
                title=item.title,
                description=item.description,
                duration_seconds=item.duration_seconds,
            )
            if not item.chapters and transcript and transcript.chapters:
                item = replace(item, chapters=transcript.chapters)
            triage = cfg.classifier.classify_video(item, transcript)
            triaged_videos.append(TriagedVideo(item=item, triage=triage, transcript=transcript))
        except Exception as exc:
            errors.append({"video_id": item.video_id, "stage": "triage", "error": str(exc)})

    # 3. Calendar availability query & scheduling
    planning_start = cfg.planning_start or datetime.now()
    planning_end = cfg.planning_end or (planning_start + timedelta(days=7))

    try:
        available_windows = cfg.calendar_target.query_availability(planning_start, planning_end)
        schedule_plan = plan_schedule(
            triaged_videos=triaged_videos,
            available_windows=available_windows,
            buffer_minutes=cfg.buffer_minutes,
        )
    except Exception as exc:
        errors.append({"stage": "scheduling", "error": str(exc)})
        schedule_plan = SchedulePlan(slots=(), unassigned_videos=tuple(triaged_videos))

    slots_by_video_id = {slot.video_id: slot for slot in schedule_plan.slots}

    # 4. Playlist relocation & calendar dispatch (two-phase mutation)
    relocated_count = 0
    scheduled_count = 0

    for tv in triaged_videos:
        vid_id = tv.item.video_id
        target_pl = tv.triage.target_playlist

        try:
            # Step A: Append to destination playlist
            added = cfg.playlist_target.add_to_playlist(target_pl, vid_id)
            if not added:
                errors.append(
                    {
                        "video_id": vid_id,
                        "stage": "playlist_add",
                        "error": "failed to add to destination playlist",
                    }
                )
                continue

            # Step B: Insert calendar event if scheduled
            if vid_id in slots_by_video_id:
                slot = slots_by_video_id[vid_id]
                event_added = cfg.calendar_target.insert_event(slot)
                if event_added:
                    scheduled_count += 1
                else:
                    errors.append(
                        {
                            "video_id": vid_id,
                            "stage": "calendar_dispatch",
                            "error": "failed to insert calendar event",
                        }
                    )

            # Step C: Purge from intake playlist only after destination insertion
            cfg.playlist_target.remove_from_playlist(cfg.inbox_playlist_name, vid_id)
            relocated_count += 1

        except Exception as exc:
            errors.append({"video_id": vid_id, "stage": "mutation", "error": str(exc)})

    return PipelineSummary(
        total_ingested=len(pending_videos),
        total_classified=len(triaged_videos),
        total_scheduled=scheduled_count,
        total_relocated=relocated_count,
        errors=tuple(errors),
    )
