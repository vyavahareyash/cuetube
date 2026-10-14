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
- **Interface**: `run_pipeline(cfg: PipelineConfig, dry_run: bool = False, confirm_callback: Callable[[SchedulePlan], bool] | None = None, export_path: str | Path | None = None) -> PipelineSummary`
- **Depth**: End-to-end orchestration, error recovery, step checkpoints, caller only provides config.
- **Factory**: `PipelineConfig.from_env(...)` resolves and wires live YouTube, OpenRouter, and Google Calendar adapters.

## 7. `adapters.export`
- **Interface**: `export_schedule_to_json(plan: SchedulePlan, output_path: Path | str | None)` / `export_schedule_to_ics(plan: SchedulePlan, output_path: Path | str | None)`
- **Depth**: Pure stdlib JSON and RFC 5545 iCalendar generation for offline calendar import without API mutations.

## 8. `cli`
- **Interface**: `cuetube [run|daemon] [--mock] [--dry-run] [--confirm] [--export <path>] [--concurrency <N>] [--progress|--no-progress] [--json]`
- **Depth**: Unified CLI runner, daemon scheduler, controlled worker concurrency, interactive single-line visual progress bar (`tqdm`-style), draft preview table, and exit codes.

## 9. `config`
- **Interface**: `load_dotenv(path)` / `get_env_str(k, def)` / `get_env_int(k, def)` / `get_env_bool(k, def)` / `get_env_list(k, def)`
- **Depth**: Pure stdlib `.env` file loader and typed environment variable parser with fallback defaults (precedence: CLI flag > env var > `.env` file > hardcoded fallback).
