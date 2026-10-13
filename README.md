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

### 4. Run Simulated Pipeline
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from cuetube.pipeline.runner import run_pipeline, PipelineConfig

summary = run_pipeline(PipelineConfig.create_mock_default())
print('Pipeline Summary:', summary)
"
```

## Documentation Index
- [Developer Guide](file:///Users/yash/Documents/Repos/cuetube/docs/DEVELOPMENT.md): Detailed environment setup, execution examples, and developer guidelines.
- [Architecture](file:///Users/yash/Documents/Repos/cuetube/docs/ARCHITECTURE.md): System decomposition, deep modules, seams, and adapters.
- [Modules Specification](file:///Users/yash/Documents/Repos/cuetube/docs/MODULES.md): Module interfaces, responsibilities, and seam contracts.
- [Glossary](file:///Users/yash/Documents/Repos/cuetube/docs/GLOSSARY.md): Shared domain vocabulary and architectural terminology.
- [Background Research](file:///Users/yash/Documents/Repos/cuetube/docs/RESEARCH.md): YouTube API constraints, ingestion mechanics, and runtime trade-offs.
- [Architecture Decision Records (ADRs)](file:///Users/yash/Documents/Repos/cuetube/docs/adr): Key architectural choices and trade-offs.


