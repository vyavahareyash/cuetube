# Developer Guide & Setup

Instructions for setting up the local development environment, running tests, linting, and executing pipeline workflows for **CueTube**.

---

## 1. Prerequisites

- **Python**: 3.11 or higher (Python 3.13 recommended)
- **uv**: Fast Python package manager ([installation instructions](https://docs.astral.sh/uv/))

Verify installations:
```bash
python3 --version
uv --version
```

---

## 2. Environment Setup

CueTube uses `uv` for virtual environment provisioning and dependency management.

```bash
# Clone repository
git clone https://github.com/vyavahareyash/cuetube.git
cd cuetube

# Create virtual environment
uv venv .venv

# Activate virtual environment
source .venv/bin/activate

# Install development dependencies (ruff, pytest)
uv pip install -e ".[dev]"
```

---

## 3. Running Tests

Automated testing targets the highest seam (`run_pipeline`) and validates domain invariants:

```bash
# Run complete test suite with pytest
pytest -v

# Run with standard library unittest (zero dependencies required)
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py" -v
```

---

## 4. Linting & Formatting

Code quality is enforced using `ruff` with configuration in `pyproject.toml`:

```bash
# Check for lint violations
ruff check .

# Automatically apply safe fixes
ruff check . --fix

# Check formatting without modifying files
ruff format --check .

# Auto-format all files
ruff format .
```

---

## 5. Running the Pipeline

### Minimal In-Memory Mock Run
Verify end-to-end orchestration through in-memory adapters:

```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from cuetube.pipeline.runner import run_pipeline, PipelineConfig

summary = run_pipeline(PipelineConfig.create_mock_default())
print('Pipeline Summary:', summary)
assert summary.total_ingested > 0
assert len(summary.errors) == 0
"
```

### Custom Pipeline Invocation Example
```python
from datetime import datetime, timedelta
from cuetube.domain.models import VideoItem, TimeWindow, EnergyLevel
from cuetube.adapters.memory import (
    InMemoryIngestionSource,
    InMemoryTranscriptProvider,
    InMemoryLlmClassifier,
    InMemoryCalendarTarget,
    InMemoryPlaylistTarget,
)
from cuetube.pipeline.runner import PipelineConfig, run_pipeline

# 1. Prepare items
videos = [
    VideoItem(
        video_id="v-1",
        title="Distributed Systems Lecture",
        channel="MIT OCW",
        duration_seconds=3600,
        published_at="2026-01-01T00:00:00Z",
        url="https://youtube.com/watch?v=v-1",
    )
]

# 2. Configure adapters
now = datetime.now()
calendar = InMemoryCalendarTarget(
    [
        TimeWindow(
            start_time=now.replace(hour=9, minute=0, second=0),
            end_time=now.replace(hour=10, minute=30, second=0),
            energy_level=EnergyLevel.MORNING_FOCUS,
        )
    ]
)

config = PipelineConfig(
    ingestion_source=InMemoryIngestionSource(videos),
    transcript_provider=InMemoryTranscriptProvider(),
    classifier=InMemoryLlmClassifier(),
    calendar_target=calendar,
    playlist_target=InMemoryPlaylistTarget({"Queue-Inbox": {"v-1"}}),
    buffer_minutes=15,
)

# 3. Execute
summary = run_pipeline(config)
print(summary)
```

### Parameter-Based Initialization (No .env)
For programmatic usage without relying on `.env` files:
```python
from cuetube import PipelineConfig, run_pipeline

cfg = PipelineConfig.create(
    openrouter_api_key="sk-or-...",
    google_client_id="...",
    google_client_secret="...",
    google_refresh_token="...",
    target_calendar="CueTube",
    buffer_minutes=15,
    load_env=False,
)
summary = run_pipeline(cfg, dry_run=True)
```

> See [CONFIGURATION.md](file:///Users/yash/Documents/Repos/cuetube/docs/CONFIGURATION.md) for the exhaustive list of configuration parameters, types, defaults, and circadian options.

---

## 6. Architecture & Code Guidelines

- **Deep Modules**: All external interactions (`YouTube API`, `InnerTube`, `LLM`, `Google Calendar`) must hide complexity behind compact protocol interfaces in `src/cuetube/domain/protocols.py`.
- **Domain Immutability**: All domain models in `src/cuetube/domain/models.py` must remain frozen (`@dataclass(frozen=True)` or Pydantic immutable) with strict validation.
- **Seam Policy**: Automated tests must primarily exercise the highest seam (`run_pipeline`) to prevent test brittleness (see [ADR 0002](file:///Users/yash/Documents/Repos/cuetube/docs/adr/0002-single-highest-seam-pipeline-testing.md)).
- **Domain Vocabulary**: Always use terms defined in [GLOSSARY.md](file:///Users/yash/Documents/Repos/cuetube/docs/GLOSSARY.md).
