import pytest
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.affect.continuity import (
    save_checkpoint,
    restore_checkpoint,
    apply_goal_completion_pulse,
    _decay,
    _apply_absence_penalty,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_save_and_restore_no_decay(db):
    save_checkpoint(0.5, 0.6, "happy")
    result = restore_checkpoint()
    assert abs(result["valence"] - 0.5) < 0.05
    assert abs(result["arousal"] - 0.6) < 0.05


def test_restore_with_elapsed_time_decays(db):
    past = (datetime.utcnow() - timedelta(hours=6)).isoformat()
    kv_set("affect_checkpoint", json.dumps({
        "valence": 0.8, "arousal": 0.8, "label": "elated", "saved_at": past
    }))
    result = restore_checkpoint()
    assert result["valence"] < 0.6
    assert result["arousal"] < 0.6


def test_restore_missing_checkpoint_returns_neutral(db):
    result = restore_checkpoint()
    assert result["valence"] == 0.0
    assert result["label"] == "neutral"


def test_decay_half_life_math():
    result = _decay(current=1.0, target=0.0, elapsed_h=6.0, half_life_h=6.0)
    assert abs(result - 0.5) < 0.01


def test_decay_zero_elapsed_unchanged():
    result = _decay(0.7, 0.0, 0.0, 6.0)
    assert result == pytest.approx(0.7)


def test_absence_penalty_under_threshold_no_effect():
    result = _apply_absence_penalty(0.5, elapsed_h=24.0)
    assert result == pytest.approx(0.5)


def test_absence_penalty_over_threshold_applied():
    result = _apply_absence_penalty(0.5, elapsed_h=72.0)
    assert result < 0.5


def test_goal_completion_pulse_increases_valence():
    new_v, new_a = apply_goal_completion_pulse(0.3, 0.3)
    assert new_v > 0.3
    assert new_a > 0.3


def test_goal_completion_pulse_capped_at_one():
    new_v, new_a = apply_goal_completion_pulse(0.95, 0.95)
    assert new_v <= 1.0
    assert new_a <= 1.0


def test_checkpoint_key_written_to_kv(db):
    save_checkpoint(0.4, 0.5, "calm")
    raw = kv_get("affect_checkpoint")
    assert raw is not None
    data = json.loads(raw)
    assert data["valence"] == pytest.approx(0.4)
    assert data["label"] == "calm"
