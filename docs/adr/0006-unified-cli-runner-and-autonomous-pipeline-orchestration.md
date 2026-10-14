# Unified CLI Runner and Autonomous Pipeline Orchestration

Autonomous backlog processing requires reliable end-to-end execution across ingestion, transcript extraction, LLM cognitive triage, chronobiological slotting, and multi-service mutations (Google Calendar event insertion and YouTube playlist moves). We provide a unified command-line entrypoint (`cuetube run`, `cuetube daemon`) and headless orchestrator factory (`PipelineConfig.from_env`) with zero core dependencies. To ensure operator safety and visibility, the runner supports dry-run planning, pure stdlib RFC 5545 iCalendar and JSON exports (`cuetube.adapters.export`), circadian energy-level Google Calendar event color coding (`colorId`), and an interactive schedule confirmation gate (`--confirm`). Transient video-level failures are isolated to preserve batch progress without crashing the orchestrator.

## Orchestration Sequence Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as User / CLI Runner
    participant Pipeline as PipelineRunner
    participant Dedupe as DeduplicationEngine
    participant Intake as IntakeSource (YouTube/CSV)
    participant Transcripts as TranscriptService
    participant LLM as OpenRouterClassifier
    participant YouTube as YouTubeTarget (Relocator)
    participant Calendar as GoogleCalendarAdapter

    User->>Pipeline: cuetube run (--target-calendar, --dry-run/--no-relocate)
    Calendar->>Calendar: resolve_target_calendar_id (find or create secondary calendar)
    Calendar->>Dedupe: get_scheduled_video_ids()
    Pipeline->>Intake: ingest_pending_items()
    Pipeline->>Dedupe: filter already scheduled videos
    loop Each Candidate Video
        Pipeline->>Transcripts: extract_transcript(video_id)
        Pipeline->>LLM: classify(video, transcript) -> TriageMetadata
    end
    Pipeline->>Calendar: query_freebusy() + schedule_slots()
    alt Dry Run
        Pipeline-->>User: export schedule.json & render preview
    else Live Dispatch
        loop Each Scheduled Slot
            opt Relocate Playlists Enabled
                Pipeline->>YouTube: insert_to_destination_playlist(title/ID)
                Pipeline->>YouTube: remove_from_intake(video_id)
            end
            Pipeline->>Calendar: create_event(summary, URL, chapters, extendedProperties)
        end
    end
```
