# CueTube

Autonomous YouTube backlog triage and calendar scheduling pipeline.

## Overview
Turns unstructured video backlogs (custom intake playlist / Google Takeout exports) into structured, energy-aware calendar viewing blocks via automated extraction, LLM classification, and calendar slotting.

## Installation

```bash
pip install cuetube
```

## Quickstart & Development

### 1. Setup Environment
```bash
# Create and activate virtual environment via uv
uv venv .venv
source .venv/bin/activate

# Install dependencies in editable mode with dev tools
uv pip install -e ".[dev]"

# Copy environment template and add credentials
cp .env.example .env
```

### 2. Run Tests
```bash
# Run tests with pytest
pytest -v

# Or run using stdlib unittest without external dependencies
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py" -v
```

### 3. Lint & Format
```bash
# Lint checks
ruff check .

# Code formatting
ruff format .
```

### 4. Run Pipeline via Unified CLI

CueTube provides a unified CLI for local execution, draft inspection, schedule export, and background daemon polling:

```bash
# Run simulated pipeline pass
cuetube run --mock

# Inspect draft schedule and request user approval before mutating calendar/playlists
cuetube run --confirm

# Dry-run plan and export to iCalendar (.ics) or JSON without mutations
cuetube run --dry-run --export schedule.ics

# Run autonomous live pipeline (requires .env configuration)
cuetube run

# Run scheduled background worker (polling every 60 minutes)
cuetube daemon --interval-minutes 60
```

### 5. Python Library Usage

CueTube can be imported and configured directly via method parameters without `.env` or environment variables:

```python
from cuetube import CircadianScheduleConfig, OAuthCredentials, PipelineConfig, run_pipeline

# Option A: Direct in-code parameter initialization (zero .env required)
cfg = PipelineConfig.create(
    openrouter_api_key="sk-or-v1-...",
    google_client_id="your-client-id.apps.googleusercontent.com",
    google_client_secret="your-client-secret",
    google_refresh_token="your-refresh-token",
    target_calendar="CueTube",
    buffer_minutes=15,
    planning_days=7,
    schedule_config=CircadianScheduleConfig(morning_start_hour=8, event_prefix="[Study] "),
    load_env=False,
)

# Option B: Pass pre-authenticated credentials directly
creds = OAuthCredentials(client_id="...", client_secret="...", refresh_token="...")
cfg = PipelineConfig.create(
    openrouter_api_key="sk-or-v1-...",
    google_credentials=creds,
    load_env=False,
)

# Option C: Load automatically from environment variables or .env
cfg = PipelineConfig.from_env()

# Execute pipeline (supports dry_run=True, progress_callback, etc.)
summary = run_pipeline(cfg, dry_run=True)
print(f"Ingested: {summary.total_ingested}, Scheduled: {summary.total_scheduled}")
```

## Configuration Reference

CueTube parameters can be supplied via environment variables, CLI options, or Python constructor parameters:

| Parameter | Env Variable | Python Kwarg (`PipelineConfig.create`) | CLI Option | Default | Description |
|---|---|---|---|---|---|
| **Google Client ID** | `GOOGLE_CLIENT_ID` / `YOUTUBE_CLIENT_ID` | `google_client_id` | N/A | `None` | OAuth 2.0 Desktop Client ID |
| **Google Client Secret** | `GOOGLE_CLIENT_SECRET` / `YOUTUBE_CLIENT_SECRET` | `google_client_secret` | N/A | `None` | OAuth 2.0 Desktop Client Secret |
| **Google Refresh Token** | `GOOGLE_REFRESH_TOKEN` / `YOUTUBE_REFRESH_TOKEN` | `google_refresh_token` | N/A | `None` | Long-lived OAuth refresh token |
| **OpenRouter API Key** | `OPENROUTER_API_KEY` | `openrouter_api_key` | N/A | `None` | OpenRouter authentication key |
| **OpenRouter Model** | `OPENROUTER_MODEL` | `openrouter_model` | N/A | `"google/gemini-2.5-flash"` | LLM model for triage classification |
| **Intake Playlist** | `CUETUBE_INBOX` | `inbox_playlist_name` | `--inbox` | `"Queue-Inbox"` | Source YouTube intake playlist name or ID |
| **Buffer Minutes** | `CUETUBE_BUFFER_MINUTES` | `buffer_minutes` | `--buffer` | `15` | Operational gap (minutes) per scheduled video |
| **Planning Days** | `CUETUBE_PLANNING_DAYS` | `planning_days` | `--days` | `7` | Days ahead to search for calendar openings |
| **Calendar IDs** | `CUETUBE_CALENDAR_IDS` | `calendar_ids` | `--calendars` | `("primary",)` | Google Calendar IDs to check for conflicts |
| **Target Calendar** | `CUETUBE_TARGET_CALENDAR` | `target_calendar` | `--target-calendar` | `"CueTube"` | Destination calendar (auto-created if missing) |
| **All Calendars** | `CUETUBE_ALL_CALENDARS` | `include_all_accessible_calendars` | `--all-calendars` | `False` | Query all visible secondary/shared calendars |
| **Takeout Path** | `CUETUBE_TAKEOUT_PATH` | `takeout_path` | `--takeout` | `None` | Path to Google Takeout CSV/ZIP archive |
| **Concurrency** | `CUETUBE_CONCURRENCY` | `concurrency` | `--concurrency` | `4` | Worker threads for transcripts and LLM triage |
| **Schedule Config** | `CUETUBE_*` (circadian) | `schedule_config` | N/A | `CircadianScheduleConfig()` | Circadian windows, energy colors & blackouts |
| **Load Env Fallback** | N/A | `load_env` | N/A | `True` | Fall back to `.env`/host environment if param unset |

> For the exhaustive configuration guide including circadian window hours, energy colors, event title prefixes, and blackout formatting, see [Configuration Guide](file:///Users/yash/Documents/Repos/cuetube/docs/CONFIGURATION.md).

## Product Roadmap & Vision

CueTube follows a two-phased delivery strategy:

1. **Phase 1: PyPI Reusable Library & CLI (Current Build Phase)**:
   - Designed as a modular, decoupled Python library with zero external runtime dependencies.
   - Strict domain invariants and deep protocol seams (`IngestionSource`, `PlaylistTarget`, `LlmClassifier`, `CalendarTarget`).
   - Packaged and published on **PyPI** (`pip install cuetube`) with a companion CLI for headless automation, scripts, and scheduled cron workers.

2. **Phase 2: Full-Fledged Web & Mobile Application (Post-Build Phase)**:
   - Development of consumer-facing web and mobile applications built on top of the CueTube core engine.
   - **Frictionless Onboarding**: Centralized "Sign in with Google" integration eliminating individual Google Cloud Console, OAuth credential, or API key configuration for end users.
   - Interactive queue selection, energy-schedule dashboards, and real-time viewing notifications.

## Documentation Index
- [Configuration Guide](file:///Users/yash/Documents/Repos/cuetube/docs/CONFIGURATION.md): Complete configuration reference, parameter mapping, and circadian rules.
- [Developer Guide](file:///Users/yash/Documents/Repos/cuetube/docs/DEVELOPMENT.md): Detailed environment setup, execution examples, and developer guidelines.
- [Architecture](file:///Users/yash/Documents/Repos/cuetube/docs/ARCHITECTURE.md): System decomposition, deep modules, seams, and adapters.
- [Modules Specification](file:///Users/yash/Documents/Repos/cuetube/docs/MODULES.md): Module interfaces, responsibilities, and seam contracts.
- [Glossary](file:///Users/yash/Documents/Repos/cuetube/docs/GLOSSARY.md): Shared domain vocabulary and architectural terminology.
- [Background Research](file:///Users/yash/Documents/Repos/cuetube/docs/RESEARCH.md): YouTube API constraints, ingestion mechanics, and runtime trade-offs.
- [Architecture Decision Records (ADRs)](file:///Users/yash/Documents/Repos/cuetube/docs/adr): Key architectural choices and trade-offs.



