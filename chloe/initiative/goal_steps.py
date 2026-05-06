from __future__ import annotations


def _playlist_next_step(goal: dict):
    from chloe.initiative.candidates import CandidateAction
    return CandidateAction(
        tool="spotify", verb="build_playlist",
        args={"name": goal.get("title", "New playlist"), "description": goal.get("description", ""), "track_uris": []},
        intent=f"Make progress on goal: {goal.get('title', 'playlist goal')}",
        pressure=min(goal.get("progress", 0.5) + 0.2, 1.0),
        source="goal",
        source_id=str(goal.get("id", "")),
    )


def _research_next_step(goal: dict):
    from chloe.initiative.candidates import CandidateAction
    topic = goal.get("title", "unknown topic")
    return CandidateAction(
        tool="web_search", verb="search",
        args={"query": f"{topic} interesting facts"},
        intent=f"Research for goal: {topic}",
        pressure=0.5,
        source="goal",
        source_id=str(goal.get("id", "")),
    )


def _writing_next_step(goal: dict):
    from chloe.initiative.candidates import CandidateAction
    return CandidateAction(
        tool="notes", verb="append",
        args={"path": f"goals/{goal.get('id', 'note')}.md", "text": ""},
        intent=f"Make progress on writing goal: {goal.get('title', '')}",
        pressure=0.5,
        source="goal",
        source_id=str(goal.get("id", "")),
    )


GOAL_STEP_REGISTRY = {
    "playlist":  _playlist_next_step,
    "research":  _research_next_step,
    "writing":   _writing_next_step,
    "note":      _writing_next_step,
}
