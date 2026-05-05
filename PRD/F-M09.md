# F-M09 · TestFlight submission + Discord demotion

## Overview

Build iOS IPA via EAS Build, submit to TestFlight. Flip `DISCORD_ENABLED=false` in `config.py`. Update `channels/discord_optional.py` to check the flag before sending. Discord can be re-enabled via the flag.

## Context

With the mobile app in TestFlight, the primary notification channel shifts from Discord to iOS push notifications. Discord remains supported (for server-side logging or dev testing) but is no longer the default. Demoting Discord is a feature flag change, not a code deletion — it can be re-enabled at any time.

## Server-side changes

### `config.py`

```python
# Add to Settings:
discord_enabled: bool = False  # Demoted after F-M09; re-enable for dev/debug
```

### `channels/discord_optional.py`

```python
# chloe/channels/discord_optional.py

from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("discord")


async def send_discord_message(content: str) -> bool:
    """
    Send a Discord DM if DISCORD_ENABLED is True.
    Returns True if sent, False if skipped or failed.
    """
    settings = get_settings()
    if not settings.discord_enabled:
        log.debug("discord_disabled_skip", content_preview=content[:40])
        return False

    try:
        from chloe.channels.discord_bot import send_dm  # existing 1.0 function
        await send_dm(content)
        return True
    except Exception as exc:
        log.warning("discord_send_error", error=str(exc))
        return False
```

### Update `tools/messages.py`

```python
# Replace direct Discord call with conditional:

async def execute(self, verb: str, args: dict) -> ToolResult:
    if verb == "send_text":
        body = args.get("body", "")

        # Try mobile push first
        from chloe.channels.push import preferred_push, get_teo_device_info
        device_info = get_teo_device_info()
        push_sent = False
        if device_info:
            push_sent = await preferred_push(device_info, {
                "type": "message",
                "title": "Chloe",
                "body": body,
            })

        # Fall back to Discord if push not available
        if not push_sent:
            from chloe.channels.discord_optional import send_discord_message
            await send_discord_message(body)

        return ToolResult(success=True, data={"body": body, "channel": "push" if push_sent else "discord"})
```

## EAS Build setup

```json
// eas.json
{
  "cli": { "version": ">= 5.0.0" },
  "build": {
    "preview": {
      "distribution": "internal",
      "ios": { "simulator": false }
    },
    "production": {
      "ios": { "buildConfiguration": "Release" }
    }
  },
  "submit": {
    "production": {
      "ios": {
        "appleId": "teo.derizzo@gmail.com",
        "ascAppId": "YOUR_APP_STORE_CONNECT_APP_ID",
        "appleTeamId": "YOUR_TEAM_ID"
      }
    }
  }
}
```

Build and submit commands:
```bash
# Build for TestFlight
npx eas build --platform ios --profile preview

# Submit to TestFlight
npx eas submit --platform ios --latest
```

## Dependencies

- F-M01 through F-M08 (mobile app must be feature-complete before TestFlight).
- C-08/C-09 (APNs/FCM push — new primary channel).
- A-07 (`tools/messages.py` — message routing).

## Testing

### Unit tests — `tests/unit/test_discord_demotion.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_discord_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr("chloe.channels.discord_optional.get_settings",
                        lambda: MagicMock(discord_enabled=False))

    from chloe.channels.discord_optional import send_discord_message
    discord_called = []

    with patch("chloe.channels.discord_bot.send_dm", new=AsyncMock(side_effect=lambda x: discord_called.append(x))):
        result = await send_discord_message("hello")

    assert result is False
    assert len(discord_called) == 0


@pytest.mark.asyncio
async def test_discord_used_when_enabled(monkeypatch):
    monkeypatch.setattr("chloe.channels.discord_optional.get_settings",
                        lambda: MagicMock(discord_enabled=True))

    sent = []
    monkeypatch.setattr("chloe.channels.discord_bot.send_dm", AsyncMock(side_effect=lambda x: sent.append(x)))

    from chloe.channels.discord_optional import send_discord_message
    result = await send_discord_message("hello from chloe")

    assert result is True
    assert "hello from chloe" in sent


@pytest.mark.asyncio
async def test_messages_tool_prefers_push(monkeypatch):
    from chloe.tools.messages import MessagesTool

    push_called = []
    discord_called = []

    monkeypatch.setattr("chloe.channels.push.get_teo_device_info",
                        lambda: {"token": "tok", "platform": "ios"})
    monkeypatch.setattr("chloe.channels.push.preferred_push",
                        AsyncMock(side_effect=lambda d, p: push_called.append(p) or True))
    monkeypatch.setattr("chloe.channels.discord_optional.send_discord_message",
                        AsyncMock(side_effect=lambda x: discord_called.append(x)))

    tool = MessagesTool()
    result = await tool.execute("send_text", {"body": "test push"})

    assert result.success
    assert len(push_called) == 1
    assert len(discord_called) == 0  # Discord not called when push succeeds
    assert result.data["channel"] == "push"
```

### Manual TestFlight checklist

- [ ] `npx eas build` completes without errors.
- [ ] Build appears in App Store Connect → TestFlight.
- [ ] Install on test device.
- [ ] All 5 tabs functional.
- [ ] Push notifications working.
- [ ] `DISCORD_ENABLED=false` set in production `.env`.
- [ ] CI passes with `DISCORD_ENABLED=false`.

## Acceptance criteria

- App installable via TestFlight.
- `DISCORD_ENABLED=false` → Discord `send_dm` never called.
- `tools/messages.py` routes to push first, Discord as fallback.
- CI passes with `DISCORD_ENABLED=false`.
