# F-M03 · Mobile push notification handler

## Overview

Register device token with the server (`POST /v1/devices`). Handle `type="message"` push → surface as iOS/Android notification and update chat. Handle `type="confirmation"` push → navigate to Confirmations tab with the ticket pre-selected.

## Context

Push notifications are how Chloe reaches Teo when the app is backgrounded. The app needs to register its push token with the server (so the server can call APNs/FCM), handle incoming push notifications, and deep-link into the correct tab. Expo Notifications handles the cross-platform push registration and receipt.

## Server-side endpoint

```python
# chloe/channels/devices_routes.py (extend from C-10)

@devices_router.post("")
async def register_device(reg: DeviceRegistration):
    devices = kv_get("devices", default=[])
    devices = [d for d in devices if d.get("platform") != reg.platform]
    devices.append({
        "token": reg.token,
        "platform": reg.platform,
        "registered_at": datetime.utcnow().isoformat(),
    })
    kv_set("devices", devices)
    return {"status": "registered"}
```

## React Native implementation

### Push notification setup

```typescript
// hooks/usePushNotifications.ts
import { useEffect } from 'react';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';
import { Config } from '../constants/Config';
import { router } from 'expo-router';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});


export function usePushNotifications() {
  useEffect(() => {
    registerForPush();
    const sub = Notifications.addNotificationResponseReceivedListener(handleResponse);
    return () => sub.remove();
  }, []);
}


async function registerForPush() {
  if (!Device.isDevice) return;  // Only on real devices

  const { status: existing } = await Notifications.getPermissionsAsync();
  let finalStatus = existing;
  if (existing !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }
  if (finalStatus !== 'granted') return;

  const tokenData = await Notifications.getExpoPushTokenAsync({
    projectId: 'your-expo-project-id',  // from app.json
  });

  // Get the native device token for direct APNs/FCM
  let nativeToken = tokenData.data;
  if (Platform.OS === 'ios') {
    const native = await Notifications.getDevicePushTokenAsync();
    nativeToken = native.data;
  }

  await fetch(`${Config.API_BASE_URL}/v1/devices`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      token: nativeToken,
      platform: Platform.OS,
    }),
  });
}


function handleResponse(response: Notifications.NotificationResponse) {
  const data = response.notification.request.content.data;

  if (data?.type === 'confirmation') {
    // Navigate to Confirmations tab with ticket pre-selected
    router.push({
      pathname: '/(tabs)/confirmations',
      params: { ticketId: data.ticket_id },
    });
  } else if (data?.type === 'message') {
    router.push('/(tabs)/');
  }
}
```

### Root layout integration

```typescript
// app/_layout.tsx
import { usePushNotifications } from '../hooks/usePushNotifications';

export default function RootLayout() {
  usePushNotifications();
  // ... rest of layout
}
```

### Confirmation deep-link handler

```typescript
// app/(tabs)/confirmations.tsx
import { useLocalSearchParams } from 'expo-router';
import { useEffect } from 'react';

export default function ConfirmationsScreen() {
  const { ticketId } = useLocalSearchParams<{ ticketId?: string }>();

  useEffect(() => {
    if (ticketId) {
      // Pre-select the ticket from deep link
      setSelectedTicketId(ticketId);
    }
  }, [ticketId]);

  // ... rest of component (implemented in F-M04)
}
```

## Dependencies

- `expo-notifications` — push notification registration and handling.
- `expo-device` — device detection.
- C-08/C-09/C-10 (APNs/FCM push clients and server-side device registration).

## Testing

### Manual UAT

1. Install app on a real iOS/Android device (not simulator).
2. Launch app — verify push permission prompt appears.
3. Check server `kv["devices"]` contains the device token.
4. Trigger a `messages.send_text` action from the server (or admin UI).
5. Background the app — verify push notification appears on the lock screen.
6. Tap the notification — verify app opens to the Chat tab.
7. Trigger a `gmail.send_reply` kinetic-sensitive action from the server.
8. Background app — verify confirmation push notification arrives.
9. Tap the notification — verify app navigates to Confirmations tab with ticket pre-selected.

### Unit test (registration flow)

```typescript
// __tests__/usePushNotifications.test.ts
import { renderHook } from '@testing-library/react-hooks';
import { usePushNotifications } from '../hooks/usePushNotifications';

jest.mock('expo-notifications', () => ({
  setNotificationHandler: jest.fn(),
  getPermissionsAsync: jest.fn().mockResolvedValue({ status: 'granted' }),
  getExpoPushTokenAsync: jest.fn().mockResolvedValue({ data: 'ExponentPushToken[xxx]' }),
  getDevicePushTokenAsync: jest.fn().mockResolvedValue({ data: 'native_token_123' }),
  addNotificationResponseReceivedListener: jest.fn().mockReturnValue({ remove: jest.fn() }),
}));

jest.mock('expo-device', () => ({ isDevice: true }));

global.fetch = jest.fn().mockResolvedValue({ json: () => ({ status: 'registered' }) });

test('registers device token on mount', async () => {
  renderHook(() => usePushNotifications());
  await new Promise(r => setTimeout(r, 100));
  expect(fetch).toHaveBeenCalledWith(
    expect.stringContaining('/v1/devices'),
    expect.objectContaining({ method: 'POST' })
  );
});
```

## Acceptance criteria

- App prompts for push permission on first launch.
- Device token registered with server (`POST /v1/devices` called on mount).
- `type="message"` push → notification appears, tap navigates to Chat tab.
- `type="confirmation"` push → notification appears, tap navigates to Confirmations tab with `ticketId` in params.
