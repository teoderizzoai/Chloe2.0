# F-V04 · Remove Fish Speech and the Python 3.11 venv

## Overview

Delete `voice/legacy.py`, `voice/pipeline.py`, the Fish Speech model files, and the 3.11 venv. Update `pyproject.toml` and `ops/bootstrap.sh`. Ensure `python -m chloe` still starts cleanly.

## Context

Chloe 1.0 had a Fish Speech TTS implementation running in a separate Python 3.11 virtual environment (because Fish Speech required Python 3.11 and specific CUDA versions incompatible with the main 3.12 environment). Now that Cartesia and ElevenLabs handle TTS via API, the entire Fish Speech stack is dead weight: ~2GB of model files, a legacy venv, and subprocess IPC code that adds complexity and failure modes.

## Files to delete

```bash
# Voice legacy code
rm -f chloe/voice/legacy.py
rm -f chloe/voice/pipeline.py
rm -f chloe/voice/fish_speech*.py  # Any fish_speech-specific files

# Model files (if stored locally)
rm -rf models/fish_speech/
rm -rf .venv_311/  # Python 3.11 venv (or wherever it lives)

# Legacy voice scripts
rm -f ops/fish_speech_server.sh
rm -f ops/start_voice.sh
```

## `pyproject.toml` changes

Remove from `[project.optional-dependencies]` or `[project.dependencies]`:
- `fish-speech` (if listed)
- `torch` (if it was only needed for Fish Speech — keep if used elsewhere)
- Any Fish Speech-specific packages

## `ops/bootstrap.sh` changes

Remove:
```bash
# REMOVE these lines:
python3.11 -m venv .venv_311
.venv_311/bin/pip install fish-speech torch==2.x
# etc.
```

## Verification commands

```bash
# These must all return no results:
grep -r "fish_speech" .
grep -r "legacy.py" chloe/
grep -r "pipeline.py" chloe/voice/
grep -r "venv_311" .
```

## Dependencies

- F-V01, F-V02, F-V03 (new voice pipeline must be in place before removing the old one).

## Testing

### Smoke tests — `tests/unit/test_voice_cutover.py`

```python
import subprocess
import pytest


def test_no_fish_speech_references():
    """fish_speech must not appear anywhere in the codebase."""
    result = subprocess.run(
        ["grep", "-r", "fish_speech", ".", "--include=*.py"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() == "", f"fish_speech still referenced:\n{result.stdout}"


def test_no_legacy_voice_imports():
    """voice/legacy.py must not exist."""
    from pathlib import Path
    assert not Path("chloe/voice/legacy.py").exists(), "legacy.py still exists"


def test_no_pipeline_voice_imports():
    """voice/pipeline.py must not exist (replaced by realtime.py)."""
    from pathlib import Path
    assert not Path("chloe/voice/pipeline.py").exists(), "pipeline.py still exists"


def test_chloe_starts_without_fish_speech():
    """python -m chloe should start without any fish_speech import errors."""
    result = subprocess.run(
        ["python", "-c", "import chloe; print('ok')"],
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"Import failed:\n{result.stderr}"
    assert "ok" in result.stdout


def test_voice_module_imports_cleanly():
    """New voice modules should import without errors."""
    result = subprocess.run(
        ["python", "-c",
         "from chloe.voice.stt_whisper import transcribe_stream; "
         "from chloe.voice.tts import synthesize_stream; "
         "from chloe.voice.realtime import handle_voice_session; "
         "print('ok')"],
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"Voice import failed:\n{result.stderr}"


def test_new_voice_imports_present():
    """New voice files must exist."""
    from pathlib import Path
    assert Path("chloe/voice/stt_whisper.py").exists()
    assert Path("chloe/voice/tts_cartesia.py").exists()
    assert Path("chloe/voice/realtime.py").exists()
```

### CI job

Add to `.github/workflows/ci.yml`:

```yaml
voice-cutover-check:
  name: Voice cutover verification
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with:
        python-version: "3.12"
    - run: pip install -e ".[dev]"
    - run: pytest tests/unit/test_voice_cutover.py -v
    - name: Check no fish_speech references
      run: |
        if grep -r "fish_speech" . --include="*.py" --include="*.sh" --include="*.toml"; then
          echo "FAIL: fish_speech still referenced"
          exit 1
        fi
        echo "OK: no fish_speech references found"
```

## Acceptance criteria

- `grep -r "fish_speech" .` returns nothing.
- `chloe/voice/legacy.py` and `chloe/voice/pipeline.py` do not exist.
- `python -m chloe` starts cleanly.
- All new voice modules (`stt_whisper`, `tts_cartesia`, `tts_elevenlabs`, `realtime`) import without errors.
- CI job passes.
