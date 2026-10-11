# Architecture

## Design Principles
- **Deep Modules**: High behavioural depth behind compact interfaces.
- **Explicit Seams**: Seams exist where external drivers vary (YouTube APIs, calendars, LLM providers).
- **Testability**: Interfaces accept dependencies, return pure data structures, avoid side-effect leakage.

## System Topology

```
[YouTube Source] ──(Seam: Ingestion)──> [Ingestion Module]
                                                 │
                                           Video Items
                                                 ▼
[Transcript APIs] ──(Seam: Extraction)──> [Extraction Module]
                                                 │
                                           Transcripts
                                                 ▼
[LLM Provider] ──(Seam: Classification)──> [Classification Module]
                                                 │
                                           Triage Metadata
                                                 ▼
[Calendar API] ──(Seam: Calendar)───────> [Scheduling Module]
                                                 │
                                            Schedule Plan
```

## Seams & Adapters Matrix

| Module | External Seam | Adapters |
|---|---|---|
| `ingestion` | Playlist / Queue Source | YouTube Data API v3 (`CustomPlaylistAdapter`), Takeout CSV (`TakeoutArchiveAdapter`) |
| `extraction` | Transcript Extraction | YouTube Transcript API (`OfficialTranscriptAdapter`), Browser Fallback |
| `classification` | Inference Engine | Gemini API (`GeminiClassifierAdapter`), Rule-based Mock |
| `scheduling` | Calendar Target | Google Calendar API (`GCalAdapter`), iCal Export (`ICalFileAdapter`) |
| `pipeline` | Orchestration | CLI Runner, Scheduled Cron / Worker |
