# Modules Specification

## 1. `domain`
- **Role**: Pure domain entities and value objects. Zero external deps.
- **Key Types**: `VideoItem`, `Transcript`, `TriageMetadata`, `TimeWindow`, `ScheduledSlot`.
- **Invariants**: Immutable dataclasses / Pydantic models with strict validation.

## 2. `ingestion`
- **Interface**: `fetch_pending_videos(source: IngestionSource) -> list[VideoItem]`
- **Depth**: Hides quota paging, batch deduplication, and auth token refreshing behind a single call.
- **Seams**: `IngestionSource` protocol.
- **Adapters**: `YouTubeApiSource`, `TakeoutCsvSource`.

## 3. `extraction`
- **Interface**: `extract_transcript(video_id: str) -> Transcript | None`
- **Depth**: Hides language detection, fallback chains, auto-generated vs manual caption resolution, chunking.
- **Seams**: `TranscriptProvider` protocol.
- **Adapters**: `YouTubeTranscriptProvider`, `FallbackScraperProvider`.

## 4. `classification`
- **Interface**: `classify_video(item: VideoItem, transcript: Transcript | None) -> TriageMetadata`
- **Depth**: Hides prompt templating, token window limits, structured schema enforcement, and retry strategies.
- **Seams**: `LlmClient` protocol.
- **Adapters**: `OpenRouterClassifierAdapter` (`GeminiClassifierAdapter` alias), `InMemoryLlmClassifier`.

## 5. `scheduling`
- **Interface**: `plan_schedule(items: list[TriagedVideo], calendar: CalendarTarget, rules: ScheduleRules) -> SchedulePlan`
- **Depth**: Hides slot fitting algorithms, energy-level matching, conflict resolution, buffer management.
- **Seams**: `CalendarTarget` protocol.
- **Adapters**: `GoogleCalendarTarget`, `InMemoryCalendarTarget`.

## 6. `pipeline`
- **Interface**: `run_pipeline(cfg: PipelineConfig) -> PipelineSummary`
- **Depth**: End-to-end orchestration, error recovery, step checkpoints, caller only provides config.
