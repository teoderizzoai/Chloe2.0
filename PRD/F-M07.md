# F-M07 · Mobile Leash settings screen

## Overview

Edit quiet hours, don't-touch lists, auth ceiling, spending cap, focus mode, away mode. Each change calls `PATCH /v1/preferences` which writes to the `preferences` table.

## Server-side endpoint

```python
# chloe/channels/preferences_routes.py

from fastapi import APIRouter
from pydantic import BaseModel
from chloe.state.db import get_connection
import json

router = APIRouter(prefix="/v1/preferences", tags=["preferences"])

class PreferenceUpdate(BaseModel):
    key: str
    value: object  # JSON-serializable

@router.get("")
async def get_preferences():
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM preferences").fetchall()
    return {r["key"]: json.loads(r["value"]) for r in rows}

@router.patch("")
async def update_preference(update: PreferenceUpdate):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        (update.key, json.dumps(update.value)),
    )
    conn.commit()
    return {"status": "updated", "key": update.key}
```

## React Native implementation

```typescript
// app/(tabs)/settings.tsx — Leash settings section
import { useState, useEffect } from 'react';
import {
  View, Text, ScrollView, Switch, TextInput, TouchableOpacity,
  StyleSheet, Alert, SectionList
} from 'react-native';
import { Config } from '../../constants/Config';
import Slider from '@react-native-community/slider';

interface Preferences {
  quiet_hours_start: string;   // "23:00"
  quiet_hours_end: string;     // "08:00"
  auth_ceiling: string;        // "kinetic" | "intimate" | "free"
  spending_cap_daily_usd: number;
  focus_mode: boolean;
  away_mode: boolean;
  dont_touch: string[];        // List of tool names
}

const DEFAULT_PREFS: Preferences = {
  quiet_hours_start: "23:00",
  quiet_hours_end: "08:00",
  auth_ceiling: "kinetic",
  spending_cap_daily_usd: 2.0,
  focus_mode: false,
  away_mode: false,
  dont_touch: [],
};

export default function SettingsScreen() {
  const [prefs, setPrefs] = useState<Preferences>(DEFAULT_PREFS);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${Config.API_BASE_URL}/v1/preferences`)
      .then(r => r.json())
      .then(data => setPrefs({ ...DEFAULT_PREFS, ...data }));
  }, []);

  const updatePref = async (key: string, value: unknown) => {
    setSaving(key);
    setPrefs(p => ({ ...p, [key]: value }));
    try {
      await fetch(`${Config.API_BASE_URL}/v1/preferences`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      });
    } catch {
      Alert.alert('Error', 'Failed to save setting');
    } finally {
      setSaving(null);
    }
  };

  return (
    <ScrollView style={styles.container}>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Availability</Text>
        <SettingRow label="Away mode" hint="Pauses all proactive actions">
          <Switch
            value={prefs.away_mode}
            onValueChange={v => updatePref('away_mode', v)}
            trackColor={{ true: '#8B5CF6' }}
          />
        </SettingRow>
        <SettingRow label="Focus mode" hint="Only urgent messages allowed">
          <Switch
            value={prefs.focus_mode}
            onValueChange={v => updatePref('focus_mode', v)}
            trackColor={{ true: '#8B5CF6' }}
          />
        </SettingRow>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Quiet hours</Text>
        <SettingRow label="Start" hint="No proactive messages after this time">
          <TextInput
            style={styles.timeInput}
            value={prefs.quiet_hours_start}
            onEndEditing={e => updatePref('quiet_hours_start', e.nativeEvent.text)}
            placeholder="23:00"
          />
        </SettingRow>
        <SettingRow label="End" hint="Quiet hours end at this time">
          <TextInput
            style={styles.timeInput}
            value={prefs.quiet_hours_end}
            onEndEditing={e => updatePref('quiet_hours_end', e.nativeEvent.text)}
            placeholder="08:00"
          />
        </SettingRow>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Authorization ceiling</Text>
        {(['free', 'intimate', 'kinetic'] as const).map(level => (
          <TouchableOpacity
            key={level}
            style={[styles.authOption, prefs.auth_ceiling === level && styles.authSelected]}
            onPress={() => updatePref('auth_ceiling', level)}
          >
            <Text style={[styles.authText, prefs.auth_ceiling === level && styles.authSelectedText]}>
              {level.charAt(0).toUpperCase() + level.slice(1)}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Daily spending cap</Text>
        <Text style={styles.capValue}>${prefs.spending_cap_daily_usd.toFixed(2)}</Text>
        <Slider
          value={prefs.spending_cap_daily_usd}
          minimumValue={0.5}
          maximumValue={10.0}
          step={0.5}
          onSlidingComplete={v => updatePref('spending_cap_daily_usd', v)}
          minimumTrackTintColor="#8B5CF6"
        />
      </View>

    </ScrollView>
  );
}

function SettingRow({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <View style={styles.row}>
      <View style={{ flex: 1 }}>
        <Text style={styles.rowLabel}>{label}</Text>
        {hint && <Text style={styles.rowHint}>{hint}</Text>}
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8f8f8' },
  section: { backgroundColor: '#fff', margin: 12, borderRadius: 12, padding: 16 },
  sectionTitle: { fontSize: 13, fontWeight: '600', color: '#666', marginBottom: 12,
    textTransform: 'uppercase', letterSpacing: 0.5 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#f0f0f0' },
  rowLabel: { fontSize: 16, color: '#1a1a1a' },
  rowHint: { fontSize: 12, color: '#999', marginTop: 2 },
  timeInput: { borderWidth: 1, borderColor: '#ddd', borderRadius: 8, padding: 8,
    fontSize: 15, width: 80, textAlign: 'center' },
  authOption: { padding: 12, borderRadius: 8, borderWidth: 1, borderColor: '#ddd',
    marginBottom: 8 },
  authSelected: { backgroundColor: '#8B5CF6', borderColor: '#8B5CF6' },
  authText: { fontSize: 15, color: '#1a1a1a', textAlign: 'center' },
  authSelectedText: { color: '#fff', fontWeight: '600' },
  capValue: { fontSize: 28, fontWeight: '700', color: '#8B5CF6', textAlign: 'center',
    marginBottom: 8 },
});
```

## Dependencies

- A-04 (`leash.py` — reads `preferences` table).
- F-04 (`preferences` table).

## Testing

### Manual UAT

1. Open Settings tab.
2. Toggle "Away mode" — verify server's `leash.violates()` blocks actions.
3. Set quiet hours to current time window — verify no initiative actions fire.
4. Change auth ceiling to "intimate" — verify kinetic actions blocked.
5. Adjust spending cap slider — verify `budgets` cap updated on server.

## Acceptance criteria

- All preference controls visible and editable.
- Changes immediately sent to `PATCH /v1/preferences`.
- Server `leash.violates()` correctly blocks outreach based on updated quiet hours.
- Auth ceiling change takes effect on next gate submission.
