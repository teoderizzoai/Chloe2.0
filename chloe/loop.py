import asyncio

from chloe.tools.messages import MessagesTool
from chloe.tools.smart_home import SmartHomeTool
from chloe.tools.registry import get_registry
from chloe.observability.logging import get_logger

log = get_logger("loop")


def register_all_tools(discord_send_callback=None):
    registry = get_registry()
    registry.register(MessagesTool(send_callback=discord_send_callback))
    registry.register(SmartHomeTool())


async def initiative_loop():
    """Main initiative loop — runs tick() every 60 seconds."""
    from chloe.initiative.engine import tick as initiative_tick
    while True:
        try:
            await initiative_tick()
        except Exception as e:
            log.warning("initiative_tick_error", error=str(e))
        await asyncio.sleep(60)
