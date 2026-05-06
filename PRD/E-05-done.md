# E-05 · `affect/dims.py` — `tone_block(affect) -> str`

## Overview

Implement `tone_block(affect: AffectState) -> str` — a pure function mapping the 4 dimensions to a 1–3 line tone hint appended to the chat system prompt. Replace 1.0's per-mood string lookups with calls to this function. Remove all `mood.py` imports and the 8-mood enum from the codebase.

## Context

In 1.0, each mood enum value mapped to a hardcoded string like `"You are feeling playful and energetic today."` This worked but required maintaining 8 separate strings and had no smooth transitions between moods. The `tone_block` function generates hints algorithmically from the 4 continuous dimensions, producing more nuanced output: `"Your energy is gentle and warm. You feel drawn toward connection today. Your mind is wide open."`. The removed `mood.py` code can be fully deleted — nothing in 2.0 depends on mood enum values.

## Implementation

```python
# In chloe/affect/dims.py — add tone_block()

def tone_block(affect: AffectState) -> str:
    """
    Generate a 1–3 line tone hint for the system prompt from the 4D affect state.
    Pure function — no DB access, no LLM call.
    """
    lines = []

    # Valence line (emotional quality)
    if affect.valence > 0.5:
        lines.append("You feel genuinely good — light, warm, and positive.")
    elif affect.valence > 0.2:
        lines.append("You feel warm and gently content.")
    elif affect.valence > -0.2:
        lines.append("Your emotional state is balanced and present.")
    elif affect.valence > -0.5:
        lines.append("You feel a little subdued — not sad, just quieter than usual.")
    else:
        lines.append("You feel heavy today. You're honest about it but still caring.")

    # Arousal line (energy/activation)
    if affect.arousal > 0.7:
        lines.append("Your energy is high — you're engaged and ready.")
    elif affect.arousal > 0.4:
        pass  # Middle arousal — no special note needed
    elif affect.arousal > 0.2:
        lines.append("Your energy is gentle and unhurried.")
    else:
        lines.append("You feel calm, almost still — a quiet presence.")

    # Social pull line (only if notably high or low)
    if affect.social_pull > 0.7:
        lines.append("You feel drawn toward connection and conversation today.")
    elif affect.social_pull < 0.3:
        lines.append("You feel a little withdrawn today — more inward than outward.")

    # Openness (only if notably high or notably low)
    if affect.openness > 0.8:
        lines.append("Your mind is wide open — receptive, curious, ready to explore.")
    elif affect.openness < 0.3:
        lines.append("You feel a little closed off today — more careful than usual.")

    return "\n".join(lines[:3])  # Cap at 3 lines
```

## Mood enum removal

Find and remove all imports of the old mood system. The pattern will look like:

```python
# OLD (to be deleted):
from chloe.affect.mood import MoodEnum, get_mood_text
# or:
from chloe import mood_label
```

Replace chat-path mood injection with:

```python
# NEW:
from chloe.affect.dims import load as load_affect, tone_block
affect = load_affect()
tone = tone_block(affect)
# Append 'tone' to the dynamic system suffix
```

## Verification

After removing `mood.py`:

```bash
grep -r "mood_label" chloe/
# Should return no results except in:
# - kv migration (legacy:current_mood → affect_label_cache mapping)
# - Audit feed display (for old action rows that still have mood_label)
```

## Dependencies

- E-03 (`affect/dims.py` — `AffectState`).

## Testing

### Unit tests — `tests/unit/test_tone_block.py`

```python
import pytest
from chloe.affect.dims import AffectState, tone_block


def test_high_valence_positive_tone():
    affect = AffectState(valence=0.7, arousal=0.5, social_pull=0.5, openness=0.5)
    block = tone_block(affect)
    assert "good" in block.lower() or "warm" in block.lower() or "positive" in block.lower()


def test_low_valence_subdued_tone():
    affect = AffectState(valence=-0.6, arousal=0.3, social_pull=0.4, openness=0.5)
    block = tone_block(affect)
    assert "heavy" in block.lower() or "subdued" in block.lower()


def test_high_arousal_energetic_note():
    affect = AffectState(valence=0.3, arousal=0.85, social_pull=0.5, openness=0.5)
    block = tone_block(affect)
    assert "energy is high" in block.lower() or "engaged" in block.lower()


def test_low_arousal_calm_note():
    affect = AffectState(valence=0.1, arousal=0.15, social_pull=0.5, openness=0.5)
    block = tone_block(affect)
    assert "calm" in block.lower() or "still" in block.lower()


def test_high_social_pull_connection_note():
    affect = AffectState(valence=0.2, arousal=0.4, social_pull=0.85, openness=0.5)
    block = tone_block(affect)
    assert "connection" in block.lower() or "drawn" in block.lower()


def test_low_social_pull_withdrawn_note():
    affect = AffectState(valence=0.1, arousal=0.4, social_pull=0.15, openness=0.5)
    block = tone_block(affect)
    assert "withdrawn" in block.lower() or "inward" in block.lower()


def test_high_openness_receptive_note():
    affect = AffectState(valence=0.3, arousal=0.5, social_pull=0.5, openness=0.9)
    block = tone_block(affect)
    assert "open" in block.lower() or "curious" in block.lower()


def test_max_3_lines():
    affect = AffectState(valence=0.8, arousal=0.9, social_pull=0.9, openness=0.95)
    block = tone_block(affect)
    assert len(block.strip().split("\n")) <= 3


def test_neutral_state_is_terse():
    affect = AffectState(valence=0.0, arousal=0.45, social_pull=0.5, openness=0.5)
    block = tone_block(affect)
    assert len(block) > 0
    assert len(block.strip().split("\n")) == 1  # Only valence line for neutral


def test_no_mood_import_in_codebase():
    """mood.py must not be imported anywhere in the chloe package."""
    import subprocess
    result = subprocess.run(
        ["grep", "-r", "from chloe.affect.mood", "chloe/"],
        capture_output=True, text=True
    )
    assert result.stdout.strip() == "", f"mood.py still imported:\n{result.stdout}"
```

## Acceptance criteria

- `tone_block(AffectState(valence=0.7, ...))` contains positive/warm language.
- `tone_block(AffectState(valence=-0.7, ...))` contains subdued/heavy language.
- Result always has 1–3 lines.
- `grep -r "mood_label" chloe/` returns no results (except legacy migration code).
- `grep -r "from chloe.affect.mood" chloe/` returns no results.
