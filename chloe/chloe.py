from chloe.observability.logging import get_logger

log = get_logger("chloe")


class ChloeCore:
    """
    2.0 Chloe core stub. Autonomous outreach routes exclusively through the action gate.
    Reactive chat replies (user-initiated) are not gate territory.
    """

    async def _send_autonomous_outreach(self, person_id: str, message: str) -> None:
        from chloe.actions.schema import Action
        from chloe.actions import gate

        action = Action(
            tool="messages",
            verb="send_text",
            args={"body": message},
            intent="autonomous outreach to " + person_id,
            preview=f"Send: {message[:80]}",
            authorization="kinetic",
        )
        result = await gate.submit(action)
        if not result.executed:
            log.info("outreach_gate_suppressed", person_id=person_id, reason=result.reason)
