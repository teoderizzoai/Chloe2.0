from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get

log = get_logger("push")


async def preferred_push(device_info: dict, payload: dict) -> bool:
    platform = device_info.get("platform", "ios")
    token = device_info.get("token", "")

    if not token:
        log.warning("push_no_token", platform=platform)
        return False

    if platform == "ios":
        from chloe.channels.push_apns import send_push
        return await send_push(token, payload)
    elif platform == "android":
        from chloe.channels.push_fcm import send_push
        return await send_push(token, payload)
    else:
        log.warning("push_unknown_platform", platform=platform)
        return False


def get_teo_device_info() -> dict:
    devices = kv_get("devices", default=[])
    if not devices:
        return {}
    return devices[-1]
