"""In-memory test doubles and mock adapters satisfying domain protocols."""

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from cuetube.domain.models import (
    CognitiveDemand,
    EngagementMode,
    ScheduledSlot,
    TimeWindow,
    Transcript,
    TriageMetadata,
    VideoItem,
)


class InMemoryIngestionSource:
    """In-memory backlog queue source."""

    def __init__(self, videos: list[VideoItem] | None = None) -> None:
        self.videos: list[VideoItem] = list(videos or [])

    def fetch_pending_videos(self) -> list[VideoItem]:
        return list(self.videos)


class InMemoryTranscriptProvider:
    """In-memory transcript provider with canned mappings."""

    def __init__(
        self,
        transcripts: dict[str, Transcript] | None = None,
        default_fallback: bool = True,
    ) -> None:
        self.transcripts: dict[str, Transcript] = dict(transcripts or {})
        self.default_fallback = default_fallback

    def extract_transcript(
        self,
        video_id: str,
        title: str = "",
        description: str = "",
        duration_seconds: int | None = None,
    ) -> Transcript | None:
        if video_id in self.transcripts:
            return self.transcripts[video_id]
        if self.default_fallback:
            lines: list[str] = []
            if title and title.strip():
                lines.append(f"Title: {title.strip()}")
            if description and description.strip():
                lines.append(f"Description:\n{description.strip()}")
            fallback_text = "\n\n".join(lines) if lines else ""
            return Transcript(
                video_id=video_id,
                text=fallback_text,
                segments=(),
                is_sampled=False,
                is_fallback=True,
            )
        return None


class InMemoryLlmClassifier:
    """Rule-based and canned mock classifier."""

    def __init__(
        self,
        canned_triage: dict[str, TriageMetadata] | None = None,
        rule_fn: Callable[[VideoItem, Transcript | None], TriageMetadata] | None = None,
    ) -> None:
        self.canned_triage = dict(canned_triage or {})
        self.rule_fn = rule_fn

    def classify_video(self, item: VideoItem, transcript: Transcript | None) -> TriageMetadata:
        if item.video_id in self.canned_triage:
            return self.canned_triage[item.video_id]

        if self.rule_fn is not None:
            return self.rule_fn(item, transcript)

        # Default classification heuristics for testing
        title_lower = item.title.lower()
        if "tutorial" in title_lower or "lecture" in title_lower or "deep dive" in title_lower:
            demand = CognitiveDemand.HIGH_ACTIVE
            mode = EngagementMode.ACTIVE
            category = "Technical Deep-Dive"
            target_playlist = "Tech-Tutorials"
        elif "music" in title_lower or "ambient" in title_lower or "lofi" in title_lower:
            demand = CognitiveDemand.VERY_LOW_PASSIVE
            mode = EngagementMode.PASSIVE
            category = "Music / Focus"
            target_playlist = "Background-Audio"
        else:
            demand = CognitiveDemand.MEDIUM_REFLECTIVE
            mode = EngagementMode.REFLECTIVE
            category = "Conceptual Explainer"
            target_playlist = "Explanations-Queue"

        duration_mins = max(1, item.duration_seconds // 60)
        return TriageMetadata(
            video_id=item.video_id,
            category=category,
            cognitive_demand=demand,
            engagement_mode=mode,
            recommended_session_duration_minutes=duration_mins,
            summary=f"Automated mock triage for {item.title}",
            target_playlist=target_playlist,
        )


class InMemoryCalendarTarget:
    """In-memory calendar target recording free availability and event insertions."""

    def __init__(self, available_windows: list[TimeWindow] | None = None) -> None:
        self.available_windows: list[TimeWindow] = list(available_windows or [])
        self.inserted_events: list[ScheduledSlot] = []

    def query_availability(self, start_time: datetime, end_time: datetime) -> list[TimeWindow]:
        results: list[TimeWindow] = []
        for w in self.available_windows:
            if w.start_time >= start_time and w.end_time <= end_time:
                results.append(w)
        return results

    def insert_event(self, slot: ScheduledSlot) -> bool:
        self.inserted_events.append(slot)
        return True


class InMemoryPlaylistTarget:
    """In-memory playlist tracking mutations and inbox clearing."""

    def __init__(self, initial_playlists: dict[str, set[str]] | None = None) -> None:
        self.playlists: dict[str, set[str]] = defaultdict(set)
        if initial_playlists:
            for pl, ids in initial_playlists.items():
                self.playlists[pl] = set(ids)

    def add_to_playlist(self, playlist_name: str, video_id: str) -> bool:
        self.playlists[playlist_name].add(video_id)
        return True

    def remove_from_playlist(self, playlist_name: str, video_id: str) -> bool:
        self.playlists[playlist_name].discard(video_id)
        return True
