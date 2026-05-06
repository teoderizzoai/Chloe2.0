"""E-03: AffectState tick() stays within bounds (property test)."""
from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings as hyp_settings
from hypothesis import strategies as st

from chloe.state.db import migrate, close
from chloe.affect.dims import AffectState, tick, load, save, tone_block

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@hyp_settings(max_examples=1000)
@given(
    valence=st.floats(-1.0, 1.0),
    arousal=st.floats(0.0, 1.0),
    social_pull=st.floats(0.0, 1.0),
    openness=st.floats(0.0, 1.0),
    hour=st.integers(0, 23),
)
def test_tick_stays_in_bounds(valence, arousal, social_pull, openness, hour):
    state = AffectState(valence=valence, arousal=arousal,
                        social_pull=social_pull, openness=openness)
    result = tick(state, hour=hour)
    assert -1.0 <= result.valence <= 1.0
    assert 0.0 <= result.arousal <= 1.0
    assert 0.0 <= result.social_pull <= 1.0
    assert 0.0 <= result.openness <= 1.0


def test_tick_1000_stable_stays_in_bounds():
    state = AffectState()
    for _ in range(1000):
        state = tick(state, hour=12)
    assert -1.0 <= state.valence <= 1.0
    assert 0.0 <= state.arousal <= 1.0
    assert 0.0 <= state.social_pull <= 1.0
    assert 0.0 <= state.openness <= 1.0


def test_load_save_round_trip():
    state = AffectState(valence=0.3, arousal=0.7, social_pull=0.4, openness=0.5)
    save(state)
    loaded = load()
    assert abs(loaded.valence - 0.3) < 1e-5
    assert abs(loaded.arousal - 0.7) < 1e-5
    assert abs(loaded.social_pull - 0.4) < 1e-5
    assert abs(loaded.openness - 0.5) < 1e-5


def test_load_returns_defaults_when_no_row(tmp_path):
    from chloe.state.db import close as db_close, get_connection
    db_close()
    migrate(db_path=tmp_path / "empty.db", migrations_dir=MIGRATIONS_DIR)
    result = load()
    assert result.valence == 0.0
    assert result.arousal == 0.4
    assert result.social_pull == 0.5
    assert result.openness == 0.6


def test_clamp_enforces_ranges():
    out_of_range = AffectState(valence=2.0, arousal=-0.5, social_pull=1.5, openness=-1.0)
    clamped = out_of_range.clamp()
    assert clamped.valence == 1.0
    assert clamped.arousal == 0.0
    assert clamped.social_pull == 1.0
    assert clamped.openness == 0.0
