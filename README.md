# CueTube

Autonomous YouTube backlog triage and calendar scheduling pipeline.

## Overview
Turns unstructured video backlogs (custom intake playlist / Google Takeout exports) into structured, energy-aware calendar viewing blocks via automated extraction, LLM classification, and calendar slotting.

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
python3 -m cuetube.cli run --mock

# Inspect draft schedule and request user approval before mutating calendar/playlists
python3 -m cuetube.cli run --confirm

# Dry-run plan and export to iCalendar (.ics) or JSON without mutations
python3 -m cuetube.cli run --dry-run --export schedule.ics

# Run autonomous live pipeline (requires .env configuration)
python3 -m cuetube.cli run

# Run scheduled background worker (polling every 60 minutes)
python3 -m cuetube.cli daemon --interval-minutes 60
```

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
- [Developer Guide](file:///Users/yash/Documents/Repos/cuetube/docs/DEVELOPMENT.md): Detailed environment setup, execution examples, and developer guidelines.
- [Architecture](file:///Users/yash/Documents/Repos/cuetube/docs/ARCHITECTURE.md): System decomposition, deep modules, seams, and adapters.
- [Modules Specification](file:///Users/yash/Documents/Repos/cuetube/docs/MODULES.md): Module interfaces, responsibilities, and seam contracts.
- [Glossary](file:///Users/yash/Documents/Repos/cuetube/docs/GLOSSARY.md): Shared domain vocabulary and architectural terminology.
- [Background Research](file:///Users/yash/Documents/Repos/cuetube/docs/RESEARCH.md): YouTube API constraints, ingestion mechanics, and runtime trade-offs.
- [Architecture Decision Records (ADRs)](file:///Users/yash/Documents/Repos/cuetube/docs/adr): Key architectural choices and trade-offs.



