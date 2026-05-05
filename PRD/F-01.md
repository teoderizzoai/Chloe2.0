# F-01 · Bootstrap the new repo layout

## Overview

Create the complete `chloe/` package directory tree as specified in PRD §6, populated with empty `__init__.py` stubs, a `pyproject.toml`, and a `.env.example`. No business logic yet — just the skeleton every subsequent step builds on.

## Context

The current codebase is a flat-ish structure rooted at `/workspaces/Chloe/chloe/` with files like `chloe.py`, `identity.py`, `heart.py`, etc. The 2.0 refactor reorganises this into a deeply nested package hierarchy that separates concerns (llm, state, memory, affect, identity, inner, actions, tools, initiative, reflect, voice, channels, persons, observability, admin). This step creates that shape without touching any existing files.

## Deliverables

- `chloe/` package tree matching PRD §6 exactly, with every sub-directory containing an `__init__.py`.
- `pyproject.toml` at repo root with `[project]` metadata, `[project.dependencies]` placeholder, `[tool.pytest.ini_options]`, `[tool.ruff]` config.
- `.env.example` listing every env var from PRD Appendix C with placeholder values and inline comments.
- `python -c "import chloe"` succeeds.
- `pytest tests/` collects 0 tests and exits 0.

## Directory tree to create

```
chloe/
  __init__.py
  config.py                  (empty stub)
  app.py                     (empty stub)
  loop.py                    (empty stub)
  llm/
    __init__.py
    gemini.py
    schemas.py
    prompts/                 (directory only, no __init__)
  state/
    __init__.py
    db.py
    kv.py
    chroma.py
    migrations/              (directory only, no __init__)
  memory/
    __init__.py
    store.py
    retrieval.py
    consolidation.py
    procedural.py
  affect/
    __init__.py
    dims.py
    label.py
    arc.py
  identity/
    __init__.py
    traits.py
    goals.py
    interest_garden.py
    self_model.py
  inner/
    __init__.py
    pressure.py
    residue.py
  actions/
    __init__.py
    schema.py
    gate.py
    audit.py
    confirm.py
    budget.py
    leash.py
    deliberate.py
  tools/
    __init__.py
    registry.py
    base.py
    spotify.py
    gmail.py
    calendar.py
    notes.py
    reminders.py
    web_search.py
    weather.py
    smart_home.py
    maps.py
    messages.py
    fs_workspace.py
    code_runner.py
    self_tools.py
  initiative/
    __init__.py
    engine.py
    candidates.py
    opportunity.py
  reflect/
    __init__.py
    every_2h.py
    nightly.py
    weekly.py
  voice/
    __init__.py
    realtime.py
    stt_whisper.py
    tts_cartesia.py
  channels/
    __init__.py
    chat_api.py
    mobile_ws.py
    push_apns.py
    push_fcm.py
    dashboard_ws.py
    discord_optional.py
  persons/
    __init__.py
    store.py
  observability/
    __init__.py
    logging.py
    metrics.py
    tracing.py
  admin/
    __init__.py
    api.py
    static/
tests/
  __init__.py
  unit/
    __init__.py
  integration/
    __init__.py
  shadow/
    __init__.py
  fixtures/
```

## pyproject.toml requirements

```toml
[project]
name = "chloe"
version = "2.0.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "pydantic>=2",
    "pydantic-settings",
    "google-generativeai",
    "chromadb",
    "structlog",
    "prometheus-client",
    "httpx",
    "python-ulid",
    "hypothesis",
    "pytest",
    "pytest-asyncio",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: requires external services",
    "live: hits real vendor APIs",
]

[tool.ruff]
line-length = 100
```

## .env.example

Must list all variables from PRD Appendix C with descriptive comments and `=REPLACE_ME` or typed-example placeholders.

## Implementation notes

- All stub `.py` files contain only `# stub` or a bare docstring — no imports. This keeps `import chloe` side-effect-free.
- The `prompts/` directory is **not** a Python package (no `__init__.py`) — it holds Markdown files only.
- The `migrations/` directory is likewise not a Python package.
- `admin/static/` can be empty.

## Dependencies

None. This is the root step.

## Testing

### Smoke tests (manual)
```bash
python -c "import chloe"
python -c "import chloe.llm.gemini"
python -c "import chloe.actions.gate"
pytest tests/ --collect-only   # should show: 0 tests collected
pytest tests/                  # should exit 0
```

### CI gate
Add a `test_imports.py` in `tests/unit/` that imports every top-level sub-package in a single test function. This test must pass after F-01 and must never regress.

```python
def test_all_packages_importable():
    import chloe
    import chloe.llm.gemini
    import chloe.llm.schemas
    import chloe.state.db
    import chloe.state.kv
    import chloe.memory.store
    import chloe.affect.dims
    import chloe.identity.traits
    import chloe.actions.gate
    import chloe.tools.registry
    import chloe.initiative.engine
    import chloe.channels.chat_api
    import chloe.observability.logging
```

## Acceptance criteria

- `python -c "import chloe"` exits 0 with no output.
- `pytest tests/` collects 0 tests and exits 0.
- `ls chloe/llm/prompts/` exists as a directory.
- `cat .env.example` contains `GEMINI_API_KEY`.
