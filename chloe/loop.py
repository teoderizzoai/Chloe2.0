from chloe.tools.messages import MessagesTool
from chloe.tools.registry import get_registry


def register_all_tools(discord_send_callback=None):
    registry = get_registry()
    registry.register(MessagesTool(send_callback=discord_send_callback))
