"""Pipeline runner and chronobiological schedule planner."""

import logging
import math
import threading
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    CircadianScheduleConfig,
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

logger = logging.getLogger(__name__)


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
    concurrency: int = 4

    @classmethod
    def create_mock_default(
        cls,
        videos: list[VideoItem] | None = None,
        available_windows: list[TimeWindow] | None = None,
        buffer_minutes: int = 15,
        concurrency: int = 4,
        inbox_playlist_name: str = "Queue-Inbox",
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

        inbox_map = {inbox_playlist_name: {v.video_id for v in default_videos}}
        return cls(
            ingestion_source=InMemoryIngestionSource(default_videos),
            transcript_provider=InMemoryTranscriptProvider(),
            classifier=InMemoryLlmClassifier(),
            calendar_target=InMemoryCalendarTarget(default_windows),
            playlist_target=InMemoryPlaylistTarget(inbox_map),
            buffer_minutes=buffer_minutes,
            inbox_playlist_name=inbox_playlist_name,
            concurrency=concurrency,
            planning_start=start - timedelta(hours=1),
            planning_end=end,
        )

    @classmethod
    def create(
        cls,
        openrouter_api_key: str | None = None,
        openrouter_model: str | None = None,
        google_credentials: Any | None = None,
        google_client_id: str | None = None,
        google_client_secret: str | None = None,
        google_refresh_token: str | None = None,
        inbox_playlist_name: str | None = None,
        buffer_minutes: int | None = None,
        planning_days: int | None = None,
        calendar_ids: tuple[str, ...] | None = None,
        include_all_accessible_calendars: bool | None = None,
        target_calendar: str | None = None,
        schedule_config: CircadianScheduleConfig | None = None,
        hydrate_metadata: bool = True,
        takeout_path: str | Path | None = None,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
        concurrency: int | None = None,
        load_env: bool = True,
    ) -> "PipelineConfig":
        """Factory assembling live production adapters from direct params or environment."""
        from cuetube.adapters.calendar import GoogleCalendarAdapter
        from cuetube.adapters.openrouter import OpenRouterClassifierAdapter, OpenRouterConfig
        from cuetube.adapters.takeout import TakeoutArchiveAdapter
        from cuetube.adapters.transcript import YouTubeTranscriptProvider
        from cuetube.adapters.youtube import (
            OAuthCredentials,
            YouTubeApiSource,
            YouTubePlaylistTarget,
        )
        from cuetube.config import (
            get_env_bool,
            get_env_int,
            get_env_list,
            get_env_str,
            load_dotenv,
        )

        if load_env:
            load_dotenv()

        effective_inbox = (
            inbox_playlist_name
            if inbox_playlist_name is not None
            else (get_env_str("CUETUBE_INBOX", "Queue-Inbox") if load_env else "Queue-Inbox")
            or "Queue-Inbox"
        )
        effective_buffer = (
            buffer_minutes
            if buffer_minutes is not None
            else (get_env_int("CUETUBE_BUFFER_MINUTES", 15) if load_env else 15)
        )
        effective_days = (
            planning_days
            if planning_days is not None
            else (get_env_int("CUETUBE_PLANNING_DAYS", 7) if load_env else 7)
        )
        effective_calendar_ids = (
            calendar_ids
            if calendar_ids is not None
            else (get_env_list("CUETUBE_CALENDAR_IDS", ("primary",)) if load_env else ("primary",))
        )
        effective_all_cals = (
            include_all_accessible_calendars
            if include_all_accessible_calendars is not None
            else (get_env_bool("CUETUBE_ALL_CALENDARS", False) if load_env else False)
        )
        effective_target_cal = (
            target_calendar
            if target_calendar is not None
            else (
                (
                    get_env_str("CUETUBE_TARGET_CALENDAR", None)
                    or get_env_str("CUETUBE_TARGET_CALENDAR_ID", "primary")
                )
                if load_env
                else "primary"
            )
            or "primary"
        )
        effective_takeout = (
            takeout_path
            if takeout_path is not None
            else (get_env_str("CUETUBE_TAKEOUT_PATH", None) if load_env else None)
        )
        effective_concurrency = (
            concurrency
            if concurrency is not None
            else (get_env_int("CUETUBE_CONCURRENCY", 4) if load_env else 4)
        )

        # Resolve Google/YouTube OAuth Credentials
        yt_creds = None
        if google_credentials is not None:
            yt_creds = google_credentials
        elif google_client_id and google_client_secret and google_refresh_token:
            yt_creds = OAuthCredentials(
                client_id=google_client_id,
                client_secret=google_client_secret,
                refresh_token=google_refresh_token,
            )
        elif load_env:
            for pfx in ("YOUTUBE_", "GOOGLE_"):
                try:
                    yt_creds = OAuthCredentials.from_env(env_prefix=pfx)
                    break
                except Exception:
                    continue

        # 1. Ingestion Source
        if effective_takeout:
            ingestion_source: IngestionSource = TakeoutArchiveAdapter(effective_takeout)
        else:
            if yt_creds is None:
                raise ValueError(
                    "Missing Google/YouTube credentials for ingestion. Provide "
                    "google_credentials, (google_client_id, google_client_secret, "
                    "google_refresh_token), or configure environment."
                )
            ingestion_source = YouTubeApiSource(
                credentials=yt_creds,
                playlist_name=effective_inbox,
                http_transport=http_transport,
            )

        # 2. Transcript Provider
        transcript_provider: TranscriptProvider = YouTubeTranscriptProvider(
            http_transport=http_transport
        )

        # 3. LLM Classifier
        if openrouter_api_key:
            or_cfg = OpenRouterConfig(
                api_key=openrouter_api_key,
                model=openrouter_model or "google/gemini-2.5-flash",
            )
            classifier: LlmClassifier = OpenRouterClassifierAdapter(
                config=or_cfg,
                http_transport=http_transport,
            )
        elif load_env:
            classifier = OpenRouterClassifierAdapter.from_env(http_transport=http_transport)
        else:
            raise ValueError(
                "Missing OpenRouter credentials. Provide openrouter_api_key "
                "or configure environment."
            )

        # 4. Calendar Target
        if yt_creds is None:
            raise ValueError(
                "Missing Google credentials for calendar target. Provide "
                "google_credentials, (google_client_id, google_client_secret, "
                "google_refresh_token), or configure environment."
            )

        if schedule_config is not None:
            effective_schedule_config = schedule_config
        elif load_env:
            effective_schedule_config = CircadianScheduleConfig.from_env()
        else:
            effective_schedule_config = CircadianScheduleConfig()

        calendar_target: CalendarTarget = GoogleCalendarAdapter(
            credentials=yt_creds,
            calendar_ids=effective_calendar_ids,
            schedule_config=effective_schedule_config,
            include_all_accessible_calendars=effective_all_cals,
            target_calendar=effective_target_cal,
            http_transport=http_transport,
        )

        # 5. Playlist Target
        playlist_target: PlaylistTarget = YouTubePlaylistTarget(
            credentials=yt_creds,
            http_transport=http_transport,
        )

        now = datetime.now(UTC)
        planning_start = now
        planning_end = now + timedelta(days=effective_days)

        return cls(
            ingestion_source=ingestion_source,
            transcript_provider=transcript_provider,
            classifier=classifier,
            calendar_target=calendar_target,
            playlist_target=playlist_target,
            buffer_minutes=effective_buffer,
            inbox_playlist_name=effective_inbox,
            planning_start=planning_start,
            planning_end=planning_end,
            hydrate_metadata=hydrate_metadata,
            concurrency=effective_concurrency,
        )

    @classmethod
    def from_env(
        cls,
        inbox_playlist_name: str | None = None,
        buffer_minutes: int | None = None,
        planning_days: int | None = None,
        calendar_ids: tuple[str, ...] | None = None,
        include_all_accessible_calendars: bool | None = None,
        target_calendar: str | None = None,
        schedule_config: CircadianScheduleConfig | None = None,
        hydrate_metadata: bool = True,
        takeout_path: str | Path | None = None,
        http_transport: Callable[[urllib.request.Request], Any] | None = None,
        concurrency: int | None = None,
    ) -> "PipelineConfig":
        """Factory assembling live production adapters from environment and configuration."""
        return cls.create(
            inbox_playlist_name=inbox_playlist_name,
            buffer_minutes=buffer_minutes,
            planning_days=planning_days,
            calendar_ids=calendar_ids,
            include_all_accessible_calendars=include_all_accessible_calendars,
            target_calendar=target_calendar,
            schedule_config=schedule_config,
            hydrate_metadata=hydrate_metadata,
            takeout_path=takeout_path,
            http_transport=http_transport,
            concurrency=concurrency,
            load_env=True,
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
    min_slot_duration_minutes: int = 15,
) -> SchedulePlan:
    """Match triaged videos against calendar windows using energy tiers and slot carving."""
    remaining_windows = sorted(list(available_windows), key=lambda w: w.start_time)
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
            slot_end = chosen_window.start_time + timedelta(minutes=required_mins)
            allocated_window = TimeWindow(
                start_time=chosen_window.start_time,
                end_time=slot_end,
                energy_level=chosen_window.energy_level,
            )
            slot = ScheduledSlot(
                slot_id=f"slot-{tv.item.video_id}-{len(scheduled_slots) + 1}",
                window=allocated_window,
                video_id=tv.item.video_id,
                title=tv.item.title,
                video_duration_minutes=video_mins,
                buffer_minutes=buffer_minutes,
                target_playlist=tv.triage.target_playlist,
                summary=tv.triage.summary,
                chapters=tv.item.chapters,
                video_url=tv.item.url,
            )
            scheduled_slots.append(slot)

            # Carve out remaining window if remainder >= min_slot_duration_minutes
            if slot_end < chosen_window.end_time:
                remainder_mins = int((chosen_window.end_time - slot_end).total_seconds() // 60)
                if remainder_mins >= min_slot_duration_minutes:
                    remainder_window = TimeWindow(
                        start_time=slot_end,
                        end_time=chosen_window.end_time,
                        energy_level=chosen_window.energy_level,
                    )
                    remaining_windows.append(remainder_window)
                    remaining_windows.sort(key=lambda w: w.start_time)
        else:
            unassigned_videos.append(tv)

    return SchedulePlan(
        slots=tuple(scheduled_slots),
        unassigned_videos=tuple(unassigned_videos),
    )


def format_draft_schedule_table(plan: SchedulePlan) -> str:
    """Format SchedulePlan into a human-readable draft table for preview and approval."""
    lines = [
        "=" * 88,
        "                         CUETUBE DRAFT SCHEDULE PLAN                            ",
        "=" * 88,
        (
            f"{'#':<4} {'Window (UTC)':<32} {'Energy Tier':<22} "
            f"{'Duration':<10} {'Target Playlist':<16}"
        ),
        "-" * 88,
    ]
    for idx, s in enumerate(plan.slots, 1):
        win_str = (
            f"{s.window.start_time.strftime('%Y-%m-%d %H:%M')} - "
            f"{s.window.end_time.strftime('%H:%M')}"
        )
        lines.append(
            f"{idx:<4} {win_str:<32} {s.window.energy_level.value:<22} "
            f"{s.video_duration_minutes}m+{s.buffer_minutes}m{'':<4} {s.target_playlist:<16}"
        )
        lines.append(f"     ↳ Video: {s.title} ({s.video_url or s.video_id})")
        if s.summary:
            lines.append(f"     ↳ Summary: {s.summary[:100]}...")

    if plan.unassigned_videos:
        lines.append("-" * 88)
        lines.append(
            f"Unassigned Videos ({len(plan.unassigned_videos)} item(s) "
            f"could not fit in open windows):"
        )
        for tv in plan.unassigned_videos:
            dur_mins = round(tv.item.duration_seconds / 60)
            lines.append(
                f" - {tv.item.title} ({dur_mins}m, {tv.triage.cognitive_demand.value}) "
                f"-> {tv.triage.target_playlist}"
            )
    lines.append("=" * 88)
    return "\n".join(lines)


def run_pipeline(
    cfg: PipelineConfig,
    dry_run: bool = False,
    confirm_callback: Callable[[SchedulePlan], bool] | None = None,
    export_path: str | Path | None = None,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
    relocate_playlists: bool = True,
) -> PipelineSummary:
    """Execute end-to-end pipeline pass through highest seam."""
    errors: list[dict[str, Any]] = []

    # 1. Ingestion
    if progress_callback:
        progress_callback(
            "ingestion", 0, 1, f"Polling intake playlist '{cfg.inbox_playlist_name}'..."
        )
    try:
        pending_videos = cfg.ingestion_source.fetch_pending_videos()
        total_ingested_count = len(pending_videos)
        if progress_callback:
            progress_callback(
                "ingestion",
                1,
                1,
                f"Retrieved {len(pending_videos)} pending item(s) from '{cfg.inbox_playlist_name}'",
            )
    except Exception as exc:
        return PipelineSummary(
            total_ingested=0,
            total_classified=0,
            total_scheduled=0,
            total_relocated=0,
            errors=({"stage": "ingestion", "error": str(exc)},),
        )

    planning_start = cfg.planning_start or datetime.now()
    planning_end = cfg.planning_end or (planning_start + timedelta(days=7))

    # Deduplicate against already scheduled calendar events
    if hasattr(cfg.calendar_target, "get_scheduled_video_ids"):
        try:
            already_scheduled = cfg.calendar_target.get_scheduled_video_ids(
                planning_start, planning_end
            )
            if already_scheduled:
                pending_videos = [v for v in pending_videos if v.video_id not in already_scheduled]
        except Exception as exc:
            logger.warning("Could not query already scheduled calendar events: %s", exc)

    # 2. Extraction & Classification
    triaged_videos: list[TriagedVideo] = []
    transport = getattr(cfg.transcript_provider, "http_transport", None)
    total_videos = len(pending_videos)

    def _triage_single(item: VideoItem) -> tuple[TriagedVideo | None, dict[str, Any] | None]:
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
            return TriagedVideo(item=item, triage=triage, transcript=transcript), None
        except Exception as exc:
            return None, {"video_id": item.video_id, "stage": "triage", "error": str(exc)}

    if total_videos == 0:
        triaged_videos = []
    elif cfg.concurrency <= 1:
        for idx, item in enumerate(pending_videos, 1):
            if progress_callback:
                progress_callback(
                    "triage", idx - 1, total_videos, f"Extracting transcript: {item.title}"
                )
            if progress_callback:
                progress_callback(
                    "triage", idx - 1, total_videos, f"LLM classification: {item.title}"
                )
            res, err = _triage_single(item)
            if res is not None:
                triaged_videos.append(res)
            if err is not None:
                errors.append(err)
            if progress_callback:
                progress_callback("triage", idx, total_videos, f"Triaged: {item.title}")
    else:
        triaged_slots: list[TriagedVideo | None] = [None] * total_videos
        completed_count = 0
        progress_lock = threading.Lock()

        if progress_callback:
            progress_callback(
                "triage",
                0,
                total_videos,
                f"Triage worker pool started ({min(cfg.concurrency, total_videos)} threads)...",
            )

        with ThreadPoolExecutor(max_workers=min(cfg.concurrency, total_videos)) as executor:
            future_to_idx = {
                executor.submit(_triage_single, item): (idx, item)
                for idx, item in enumerate(pending_videos)
            }
            for future in as_completed(future_to_idx):
                idx, item = future_to_idx[future]
                res, err = future.result()
                if res is not None:
                    triaged_slots[idx] = res
                if err is not None:
                    errors.append(err)
                with progress_lock:
                    completed_count += 1
                    if progress_callback:
                        progress_callback(
                            "triage",
                            completed_count,
                            total_videos,
                            f"Triaged ({completed_count}/{total_videos}): {item.title}",
                        )

        triaged_videos = [tv for tv in triaged_slots if tv is not None]

    # 3. Calendar availability query & scheduling
    if progress_callback:
        progress_callback(
            "scheduling", 0, 1, "Querying calendar availability & calculating energy slots..."
        )
    try:
        available_windows = cfg.calendar_target.query_availability(planning_start, planning_end)
        schedule_plan = plan_schedule(
            triaged_videos=triaged_videos,
            available_windows=available_windows,
            buffer_minutes=cfg.buffer_minutes,
        )
        if progress_callback:
            progress_callback(
                "scheduling",
                1,
                1,
                f"Planned {len(schedule_plan.slots)} slot(s) "
                f"({len(schedule_plan.unassigned_videos)} unassigned)",
            )
    except Exception as exc:
        errors.append({"stage": "scheduling", "error": str(exc)})
        schedule_plan = SchedulePlan(slots=(), unassigned_videos=tuple(triaged_videos))

    # Optional schedule export
    if export_path is not None:
        from cuetube.adapters.export import export_schedule_plan

        try:
            export_schedule_plan(schedule_plan, export_path)
        except Exception as exc:
            errors.append({"stage": "export", "error": str(exc)})

    # Dry-run check: bypass mutations if requested
    if dry_run:
        return PipelineSummary(
            total_ingested=total_ingested_count,
            total_classified=len(triaged_videos),
            total_scheduled=len(schedule_plan.slots),
            total_relocated=0,
            errors=tuple(errors),
        )

    # User confirmation callback gate
    if confirm_callback is not None:
        try:
            proceed = confirm_callback(schedule_plan)
        except Exception as exc:
            proceed = False
            errors.append({"stage": "confirmation", "error": str(exc)})

        if not proceed:
            return PipelineSummary(
                total_ingested=total_ingested_count,
                total_classified=len(triaged_videos),
                total_scheduled=0,
                total_relocated=0,
                errors=tuple(errors),
            )

    slots_by_video_id = {slot.video_id: slot for slot in schedule_plan.slots}

    # 4. Playlist relocation & calendar dispatch (two-phase mutation)
    relocated_count = 0
    scheduled_count = 0
    total_mutations = len(triaged_videos)

    for idx, tv in enumerate(triaged_videos, 1):
        vid_id = tv.item.video_id
        target_pl = tv.triage.target_playlist

        if relocate_playlists:
            if progress_callback:
                progress_callback(
                    "mutation",
                    idx - 1,
                    total_mutations,
                    f"Relocating to '{target_pl}': {tv.item.title}",
                )

            try:
                # Step A: Append to destination playlist
                added = cfg.playlist_target.add_to_playlist(target_pl, vid_id)
                if not added:
                    errors.append(
                        {
                            "video_id": vid_id,
                            "stage": "playlist_add",
                            "error": f"failed to add to destination playlist '{target_pl}'",
                        }
                    )
                    continue
            except Exception as exc:
                errors.append({"video_id": vid_id, "stage": "mutation", "error": str(exc)})
                continue
        elif progress_callback:
            progress_callback(
                "mutation",
                idx - 1,
                total_mutations,
                f"Scheduling event: {tv.item.title}",
            )

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

        if relocate_playlists:
            try:
                # Step C: Purge from intake playlist only after destination insertion
                removed = cfg.playlist_target.remove_from_playlist(cfg.inbox_playlist_name, vid_id)
                if not removed:
                    errors.append(
                        {
                            "video_id": vid_id,
                            "stage": "playlist_remove",
                            "error": (
                                f"failed to remove from intake playlist '{cfg.inbox_playlist_name}'"
                            ),
                        }
                    )
                relocated_count += 1
                if progress_callback:
                    progress_callback(
                        "mutation",
                        idx,
                        total_mutations,
                        f"Relocated to '{target_pl}': {tv.item.title}",
                    )
            except Exception as exc:
                errors.append({"video_id": vid_id, "stage": "playlist_remove", "error": str(exc)})

    if progress_callback:
        progress_callback("complete", 1, 1, "Pipeline run finished")

    return PipelineSummary(
        total_ingested=total_ingested_count,
        total_classified=len(triaged_videos),
        total_scheduled=scheduled_count,
        total_relocated=relocated_count,
        errors=tuple(errors),
    )
