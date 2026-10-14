# CueTube Configuration & Usage Guide

Complete reference for all configuration options, environment variables, initialization parameters, and step-by-step usage workflows for **CueTube**.

---

## 1. Quickstart: Step-by-Step Instructions

### Step 1: Install CueTube
Install the package from PyPI:
```bash
pip install cuetube
```
Or for local development:
```bash
git clone https://github.com/vyavahareyash/cuetube.git
cd cuetube
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Step 2: Obtain Credentials
1. **Google OAuth 2.0 Credentials**:
   - Create a Google Cloud project and enable **YouTube Data API v3** and **Google Calendar API**.
   - Create an **OAuth 2.0 Client ID** (Desktop Application).
   - Run the helper script to obtain your refresh token:
     ```bash
     python3 scripts/get_youtube_token.py -f client_secret.json
     ```
   - This provides `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` (shared across YouTube and Calendar).
2. **OpenRouter API Key**:
   - Obtain an API key from [OpenRouter.ai](https://openrouter.ai/keys).

### Step 3: Choose Configuration Method
CueTube supports two configuration approaches:
- **Environment / File-based**: Set environment variables or write a `.env` file (copied from `.env.example`).
- **In-Code / Parameter-based**: Pass credentials and options directly as keyword arguments to `PipelineConfig.create(...)` with `load_env=False`.

### Step 4: Run via CLI or Python Library
- **CLI**: `cuetube run --dry-run` to preview, or `cuetube run` to execute live.
- **Python**: Import `PipelineConfig` and `run_pipeline` in your Python application.

---

## 2. Configuration Reference

All settings can be configured via environment variables, CLI flags, or Python method parameters:

| Config Option | Env Variable | Python Kwarg (`PipelineConfig.create`) | CLI Flag | Type | Default | Description |
|---|---|---|---|---|---|---|
| **Google Client ID** | `GOOGLE_CLIENT_ID` / `YOUTUBE_CLIENT_ID` | `google_client_id` | N/A | `str` | `None` | Google OAuth Desktop Client ID |
| **Google Client Secret** | `GOOGLE_CLIENT_SECRET` / `YOUTUBE_CLIENT_SECRET` | `google_client_secret` | N/A | `str` | `None` | Google OAuth Desktop Client Secret |
| **Google Refresh Token** | `GOOGLE_REFRESH_TOKEN` / `YOUTUBE_REFRESH_TOKEN` | `google_refresh_token` | N/A | `str` | `None` | Google OAuth refresh token |
| **Google Credentials Object** | N/A | `google_credentials` | N/A | `OAuthCredentials` | `None` | Direct pre-built credentials instance |
| **OpenRouter API Key** | `OPENROUTER_API_KEY` | `openrouter_api_key` | N/A | `str` | `None` | OpenRouter API authentication key |
| **OpenRouter Model** | `OPENROUTER_MODEL` | `openrouter_model` | N/A | `str` | `"google/gemini-2.5-flash"` | LLM model for triage classification |
| **Intake Playlist** | `CUETUBE_INBOX` | `inbox_playlist_name` | `--inbox` | `str` | `"Queue-Inbox"` | Name or ID of YouTube intake playlist |
| **Operational Buffer** | `CUETUBE_BUFFER_MINUTES` | `buffer_minutes` | `--buffer` | `int` | `15` | Minutes reserved after each scheduled video |
| **Planning Horizon** | `CUETUBE_PLANNING_DAYS` | `planning_days` | `--days` | `int` | `7` | Days ahead to search for open calendar slots |
| **Busy Calendars** | `CUETUBE_CALENDAR_IDS` | `calendar_ids` | `--calendars` | `tuple[str, ...]` | `("primary",)` | Calendars checked for busy conflict slots |
| **Target Calendar** | `CUETUBE_TARGET_CALENDAR` | `target_calendar` | `--target-calendar` | `str` | `"CueTube"` | Dedicated calendar for events (auto-created if needed) |
| **Include All Calendars** | `CUETUBE_ALL_CALENDARS` | `include_all_accessible_calendars` | `--all-calendars` | `bool` | `False` | Inspect all accessible secondary calendars |
| **Takeout Intake Path** | `CUETUBE_TAKEOUT_PATH` | `takeout_path` | `--takeout` | `str \| Path` | `None` | Path to Google Takeout CSV/ZIP archive |
| **Worker Concurrency** | `CUETUBE_CONCURRENCY` | `concurrency` | `--concurrency` | `int` | `4` | Parallel threads for transcripts and LLM triage |
| **Load Env Fallback** | N/A | `load_env` | N/A | `bool` | `True` | Fall back to `.env`/environment if param omitted |
| **Circadian Config** | `CUETUBE_*` | `schedule_config` | N/A | `CircadianScheduleConfig` | Default instance | Circadian tiers, colors, and blackout windows |

---

## 3. Circadian & Energy Window Settings (`CircadianScheduleConfig`)

Configures energy alignment and calendar event presentation:

| Setting | Env Variable | `CircadianScheduleConfig` Kwarg | Type | Default | Description |
|---|---|---|---|---|---|
| **Morning Window Start** | `CUETUBE_MORNING_START` | `morning_start_hour` | `int` | `9` | Hour (0-23) starting high-energy focus block |
| **Morning Window End** | `CUETUBE_MORNING_END` | `morning_end_hour` | `int` | `12` | Hour (0-23) ending high-energy focus block |
| **Afternoon Window Start** | `CUETUBE_AFTERNOON_START` | `afternoon_start_hour` | `int` | `13` | Hour starting reflective/maintenance block |
| **Afternoon Window End** | `CUETUBE_AFTERNOON_END` | `afternoon_end_hour` | `int` | `17` | Hour ending reflective/maintenance block |
| **Evening Window Start** | `CUETUBE_EVENING_START` | `evening_start_hour` | `int` | `17` | Hour starting passive unwind block |
| **Evening Window End** | `CUETUBE_EVENING_END` | `evening_end_hour` | `int` | `21` | Hour ending passive unwind block |
| **Minimum Slot Duration** | `CUETUBE_MIN_SLOT_MINUTES` | `min_slot_duration_minutes` | `int` | `15` | Minimum free minutes needed to schedule a video |
| **Event Title Prefix** | `CUETUBE_EVENT_PREFIX` | `event_prefix` | `str` | `"CueTube: "` | Prefix prepended to calendar event titles |
| **Blackout Windows** | `CUETUBE_BLACKOUT_WINDOWS` | `blackout_windows` | `tuple[BlackoutWindow, ...]` | `()` | Excluded recurring time ranges (e.g. `12:00-13:00`) |
| **Morning Color ID** | `CUETUBE_COLOR_MORNING` | Part of `energy_colors` | `str` | `"11"` (Flamingo) | Google Calendar colorId for Morning Focus |
| **Afternoon Color ID** | `CUETUBE_COLOR_AFTERNOON` | Part of `energy_colors` | `str` | `"5"` (Banana) | Google Calendar colorId for Afternoon Maintenance |
| **Evening Color ID** | `CUETUBE_COLOR_EVENING` | Part of `energy_colors` | `str` | `"2"` (Sage) | Google Calendar colorId for Evening Unwind |

---

## 4. Pipeline Execution Options (`run_pipeline`)

Runtime control passed directly to `run_pipeline(cfg, ...)`:

| Kwarg | Env Variable | CLI Flag | Type | Default | Description |
|---|---|---|---|---|---|
| `dry_run` | `CUETUBE_DRY_RUN` | `--dry-run` | `bool` | `False` | Run full analysis without mutating calendar or playlists |
| `confirm_callback` | `CUETUBE_CONFIRM` | `--confirm` | `Callable[[SchedulePlan], bool]` | `None` | Interactive prompt/callback before dispatch |
| `export_path` | `CUETUBE_EXPORT_PATH` | `--export` | `str \| Path` | `None` | Export generated schedule to `.ics` or `.json` |
| `relocate_playlists` | `CUETUBE_RELOCATE_PLAYLISTS` | `--no-relocate` | `bool` | `True` | Move scheduled videos to categorized destination playlists |
| `progress_callback` | N/A | `--quiet` / `--json` | `Callable[[str, int, int, str], None]` | `None` | Progress update hook for UI/CLI integration |

---

## 5. Usage Examples

### Example 1: Pure Parameter Initialization (No .env)
```python
from cuetube import CircadianScheduleConfig, PipelineConfig, run_pipeline

schedule_cfg = CircadianScheduleConfig(
    morning_start_hour=8,
    morning_end_hour=11,
    event_prefix="[Study] ",
)

cfg = PipelineConfig.create(
    openrouter_api_key="sk-or-v1-...",
    google_client_id="my-client-id.apps.googleusercontent.com",
    google_client_secret="my-client-secret",
    google_refresh_token="my-refresh-token",
    inbox_playlist_name="Queue-Inbox",
    target_calendar="CueTube",
    buffer_minutes=15,
    schedule_config=schedule_cfg,
    load_env=False,  # Strict isolation from host environment
)

summary = run_pipeline(cfg, dry_run=True, export_path="schedule.ics")
print(f"Scheduled {summary.total_scheduled} of {summary.total_ingested} videos.")
```

### Example 2: Environment Variable Fallback
```python
from cuetube import PipelineConfig, run_pipeline

# Automatically reads .env with optional parameter overrides
cfg = PipelineConfig.from_env(
    buffer_minutes=20,
    target_calendar="My-Dedicated-Calendar",
)
summary = run_pipeline(cfg)
```

### Example 3: Offline Takeout Processing (No YouTube API Quota)
```bash
# Ingest directly from Google Takeout export
cuetube run --takeout watch-later.csv --dry-run --export draft.ics
```
