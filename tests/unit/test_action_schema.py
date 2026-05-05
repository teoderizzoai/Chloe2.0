import pytest
from datetime import datetime, timezone
from chloe.actions.schema import Action, ActionResult, ArtifactRef, CostEstimate, ulid


def test_ulid_is_string():
    uid = ulid()
    assert isinstance(uid, str)
    assert len(uid) > 0


def test_ulid_unique():
    ids = {ulid() for _ in range(100)}
    assert len(ids) == 100


def test_action_roundtrip_json():
    a = Action(
        tool="spotify",
        verb="queue_track",
        args={"uri": "spotify:track:abc"},
        intent="I want to queue something calming",
        preview="Queue 'Bloom' by Beach House",
        authorization="kinetic",
    )
    json_str = a.model_dump_json()
    a2 = Action.model_validate_json(json_str)
    assert a2.tool == "spotify"
    assert a2.verb == "queue_track"
    assert a2.args["uri"] == "spotify:track:abc"
    assert a2.state == "proposed"


def test_action_default_id_set():
    a = Action(
        tool="notes", verb="create",
        intent="make a note", preview="Create note",
        authorization="kinetic",
    )
    assert a.id is not None
    assert len(a.id) > 0


def test_action_empty_intent_raises():
    with pytest.raises(Exception):
        Action(
            tool="notes", verb="create",
            intent="   ",
            preview="preview", authorization="kinetic",
        )


def test_action_invalid_authorization_raises():
    with pytest.raises(Exception):
        Action(
            tool="notes", verb="create",
            intent="test", preview="p",
            authorization="superkinetic",
        )


def test_action_invalid_state_raises():
    with pytest.raises(Exception):
        Action(
            tool="notes", verb="create",
            intent="test", preview="p",
            authorization="kinetic",
            state="flying",
        )


def test_action_result_defaults():
    ar = ActionResult()
    assert ar.executed is False
    assert ar.suppressed is False
    assert ar.awaiting is False


def test_cost_estimate_defaults():
    c = CostEstimate()
    assert c.tokens == 0
    assert c.reversibility == 1.0


def test_cost_estimate_reversibility_bounds():
    with pytest.raises(Exception):
        CostEstimate(reversibility=1.5)


def test_action_with_artifact_refs():
    a = Action(
        tool="spotify", verb="queue_track",
        intent="queue", preview="Queue track",
        authorization="kinetic",
        artifact_refs=[ArtifactRef(kind="spotify_track", ref="spotify:track:xyz")]
    )
    j = a.model_dump_json()
    a2 = Action.model_validate_json(j)
    assert len(a2.artifact_refs) == 1
    assert a2.artifact_refs[0].ref == "spotify:track:xyz"
