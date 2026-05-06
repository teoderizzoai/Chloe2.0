from __future__ import annotations

from chloe.persons.store import get_attachment_depth, set_attachment_depth
from chloe.observability.logging import get_logger

log = get_logger("persons.attachment")

_SILENCE_THRESHOLD_DAYS = 3
_SILENCE_DECAY_PER_DAY = 0.02


def apply_delta(person_id: int, delta: float) -> float:
    """
    Apply an attachment delta (in [-0.05, 0.05]) to a person's depth.
    Returns the new depth, clamped to [-1, 1].
    """
    delta = max(-0.05, min(0.05, delta))
    current = get_attachment_depth(person_id)
    new_depth = max(-1.0, min(1.0, current + delta))
    set_attachment_depth(person_id, new_depth)
    log.debug("attachment_delta", person_id=person_id, delta=delta,
              old=round(current, 3), new=round(new_depth, 3))
    return new_depth


def apply_silence_decay(person_id: int, days_since_contact: float) -> float:
    """
    Apply silence decay after _SILENCE_THRESHOLD_DAYS days of no contact.
    Returns the new depth.
    """
    if days_since_contact <= _SILENCE_THRESHOLD_DAYS:
        return get_attachment_depth(person_id)

    extra_days = days_since_contact - _SILENCE_THRESHOLD_DAYS
    total_decay = extra_days * _SILENCE_DECAY_PER_DAY
    current = get_attachment_depth(person_id)
    new_depth = max(-1.0, current - total_decay)
    set_attachment_depth(person_id, new_depth)
    log.debug("attachment_silence_decay", person_id=person_id,
              days=days_since_contact, decay=round(total_decay, 3),
              old=round(current, 3), new=round(new_depth, 3))
    return new_depth


def openness_bias(depth: float) -> float:
    """Returns the openness bias from attachment depth: +0.15 * depth."""
    return 0.15 * depth


def relationship_label(depth: float) -> str:
    """Return a prose relationship label based on attachment depth."""
    if depth >= 0.7:
        return "deeply close"
    if depth >= 0.4:
        return "warmly connected"
    if depth >= 0.1:
        return "friendly"
    if depth >= -0.1:
        return "neutral"
    if depth >= -0.4:
        return "distant"
    return "estranged"
