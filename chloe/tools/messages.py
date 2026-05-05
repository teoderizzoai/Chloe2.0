from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("tool.messages")


class MessagesTool(Tool):
    name = "messages"

    def __init__(self, send_callback=None):
        """
        send_callback: async callable(body: str) -> bool
        In production, wired to discord_bot.send_dm(). In tests, pass a mock.
        """
        self._send_callback = send_callback
        self.verbs = {
            "send_text": ToolVerb(
                name="send_text",
                schema={
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "Message text to send"},
                    },
                    "required": ["body"],
                },
                auth_class="kinetic",
                reversibility=0.0,
                cost_per_call_usd=0.0,
                description_for_model=(
                    "Send a text message to Teo via the primary channel (Discord/push). "
                    "Use sparingly — this is a real message that arrives on his phone."
                ),
                description_for_human="Send a text message",
            ),
            "send_voice": ToolVerb(
                name="send_voice",
                schema={
                    "type": "object",
                    "properties": {
                        "audio_file": {"type": "string", "description": "Path to audio file"},
                    },
                    "required": ["audio_file"],
                },
                auth_class="kinetic",
                reversibility=0.0,
                cost_per_call_usd=0.0,
                description_for_model="Send a voice message. Use rarely.",
                description_for_human="Send a voice message",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        settings = get_settings()

        if verb == "send_text":
            body = args.get("body", "").strip()
            if not body:
                return ToolResult(success=False, error="body is required")

            if not self._send_callback:
                log.warning("messages_no_callback", verb=verb)
                return ToolResult(success=False, error="No send callback registered")

            if not settings.discord_enabled:
                log.info("messages_discord_disabled", verb=verb)
                return ToolResult(success=False, error="Discord is disabled")

            try:
                success = await self._send_callback(body)
                log.info("messages_sent", verb=verb, length=len(body))
                return ToolResult(success=bool(success), data={"sent": True})
            except Exception as e:
                log.error("messages_send_failed", verb=verb, error=str(e))
                return ToolResult(success=False, error=str(e))

        elif verb == "send_voice":
            return ToolResult(
                success=False,
                error="send_voice not yet implemented (Phase F)",
                is_dry_run=False,
            )

        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "send_text":
            body = args.get("body", "")
            return f"Would send: {body[:100]}"
        return super().dry_run(verb, args)
