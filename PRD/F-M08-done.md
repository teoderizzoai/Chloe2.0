# F-M08 · Mobile Account settings

## Overview

Show per-integration OAuth status (connected / disconnected). Revoke button calls `DELETE /v1/oauth/{service}` which clears the stored token. Re-auth button opens the OAuth flow in the in-app browser.

## Server-side endpoints

```python
# chloe/channels/oauth_routes.py — extend existing

@router.get("/v1/oauth/status")
async def oauth_status():
    from chloe.state.oauth_tokens import load as load_token
    services = ["spotify", "google"]
    result = {}
    for svc in services:
        token = load_token(svc)
        result[svc] = {
            "connected": bool(token and token.get("access_token")),
            "scopes": token.get("scope", "") if token else "",
        }
    return result


@router.delete("/v1/oauth/{service}")
async def revoke_oauth(service: str):
    from chloe.state.kv import set as kv_set
    kv_set(f"oauth_{service}", None)  # Clear encrypted token
    return {"status": "revoked", "service": service}
```

## React Native implementation

```typescript
// app/(tabs)/settings.tsx — Account section (add below Leash settings)
import * as WebBrowser from 'expo-web-browser';

interface OAuthStatus {
  spotify: { connected: boolean; scopes: string };
  google: { connected: boolean; scopes: string };
}

function AccountSection() {
  const [oauth, setOauth] = useState<OAuthStatus | null>(null);

  useEffect(() => {
    fetch(`${Config.API_BASE_URL}/v1/oauth/status`)
      .then(r => r.json())
      .then(setOauth);
  }, []);

  const revoke = async (service: string) => {
    Alert.alert(
      'Revoke access',
      `Disconnect ${service}? Chloe will lose access to your ${service} account.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Revoke',
          style: 'destructive',
          onPress: async () => {
            await fetch(`${Config.API_BASE_URL}/v1/oauth/${service}`, { method: 'DELETE' });
            setOauth(prev => prev ? {
              ...prev,
              [service]: { ...prev[service as keyof OAuthStatus], connected: false },
            } : null);
          },
        },
      ]
    );
  };

  const reconnect = async (service: string) => {
    const url = `${Config.API_BASE_URL}/admin/oauth/${service}/start`;
    await WebBrowser.openBrowserAsync(url);
    // Reload status after OAuth flow completes
    setTimeout(() => {
      fetch(`${Config.API_BASE_URL}/v1/oauth/status`)
        .then(r => r.json())
        .then(setOauth);
    }, 2000);
  };

  if (!oauth) return null;

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>Connected accounts</Text>
      {(['spotify', 'google'] as const).map(svc => {
        const status = oauth[svc];
        return (
          <View key={svc} style={styles.oauthRow}>
            <View>
              <Text style={styles.oauthName}>
                {svc === 'google' ? 'Google (Gmail + Calendar)' : 'Spotify'}
              </Text>
              <View style={[styles.statusDot,
                { backgroundColor: status.connected ? '#10B981' : '#9CA3AF' }]}>
                <Text style={styles.statusText}>
                  {status.connected ? 'Connected' : 'Disconnected'}
                </Text>
              </View>
            </View>
            {status.connected ? (
              <TouchableOpacity
                style={[styles.oauthBtn, styles.revokeBtn]}
                onPress={() => revoke(svc)}
              >
                <Text style={styles.revokeBtnText}>Revoke</Text>
              </TouchableOpacity>
            ) : (
              <TouchableOpacity
                style={[styles.oauthBtn, styles.connectBtn]}
                onPress={() => reconnect(svc)}
              >
                <Text style={styles.connectBtnText}>Connect</Text>
              </TouchableOpacity>
            )}
          </View>
        );
      })}
    </View>
  );
}

// Add to StyleSheet:
const accountStyles = StyleSheet.create({
  oauthRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#f0f0f0' },
  oauthName: { fontSize: 15, color: '#1a1a1a', marginBottom: 4 },
  statusDot: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, alignSelf: 'flex-start' },
  statusText: { fontSize: 12, color: '#fff', fontWeight: '600' },
  oauthBtn: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 8 },
  revokeBtn: { backgroundColor: '#FEE2E2' },
  revokeBtnText: { color: '#EF4444', fontWeight: '600' },
  connectBtn: { backgroundColor: '#EDE9FE' },
  connectBtnText: { color: '#8B5CF6', fontWeight: '600' },
});
```

## Dependencies

- B-02 (`/admin/oauth/spotify/start` — Spotify OAuth start).
- B-03 (`/admin/oauth/google/start` — Google OAuth start).
- B-01 (`oauth_tokens.py` — token storage; revoke by clearing).
- `expo-web-browser` — in-app OAuth browser.

## Testing

### Manual UAT

1. Open Settings → Account section.
2. Verify Spotify shows "Connected" (if token present) or "Disconnected".
3. Tap "Revoke" for Spotify — verify status changes to "Disconnected".
4. Verify `tools/spotify.py` returns error for subsequent calls.
5. Tap "Connect" — verify in-app browser opens Spotify OAuth.
6. Complete auth — verify status shows "Connected" again.

## Acceptance criteria

- Connected services show green "Connected" badge.
- Disconnected services show grey "Disconnected" badge.
- "Revoke" calls `DELETE /v1/oauth/{service}` and updates UI.
- After revoke, Spotify tool returns `PermissionError`.
- "Connect" opens in-app browser to admin OAuth start URL.
