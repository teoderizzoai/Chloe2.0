# F-M06 · Mobile "Now" tab (Chloe's state)

## Overview

`GET /v1/state/now`. Renders: active goals with progress bars, top 3 interests with intensity bars, "she is currently…" one-liner from `kv["current_activity"]`.

## Server-side endpoint

```python
# chloe/channels/state_routes.py

from fastapi import APIRouter
from chloe.state.db import get_connection
from chloe.state.kv import get as kv_get
from chloe.affect.dims import load as load_affect
from chloe.affect.label import get_label as get_affect_label
import json

router = APIRouter(prefix="/v1/state", tags=["state"])


@router.get("/now")
async def get_now():
    conn = get_connection()
    affect = load_affect()
    affect_label = await get_affect_label(affect)

    # Active goals
    goals = [dict(r) for r in conn.execute(
        "SELECT id, title, description, tags, progress FROM inner_goals "
        "WHERE completed IS NULL OR completed = 0 ORDER BY priority DESC LIMIT 5"
    ).fetchall()]
    for g in goals:
        if isinstance(g.get("tags"), str):
            g["tags"] = json.loads(g["tags"])

    # Top 3 interests
    interests = [dict(r) for r in conn.execute(
        "SELECT id, label, category, intensity FROM interest_garden "
        "ORDER BY intensity DESC LIMIT 3"
    ).fetchall()]

    return {
        "affect": {
            "valence": round(affect.valence, 2),
            "arousal": round(affect.arousal, 2),
            "social_pull": round(affect.social_pull, 2),
            "openness": round(affect.openness, 2),
            "label": affect_label,
        },
        "current_activity": kv_get("current_activity", default="thinking"),
        "goals": goals,
        "interests": interests,
    }
```

## React Native implementation

```typescript
// app/(tabs)/now.tsx
import { useState, useEffect } from 'react';
import {
  View, Text, ScrollView, StyleSheet, RefreshControl, ActivityIndicator
} from 'react-native';
import { Config } from '../../constants/Config';

interface NowState {
  affect: {
    valence: number;
    arousal: number;
    social_pull: number;
    openness: number;
    label: string;
  };
  current_activity: string;
  goals: Array<{
    id: string;
    title: string;
    description: string;
    tags: string[];
    progress: number | null;
  }>;
  interests: Array<{
    id: string;
    label: string;
    category: string;
    intensity: number;
  }>;
}

export default function NowScreen() {
  const [state, setState] = useState<NowState | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    const resp = await fetch(`${Config.API_BASE_URL}/v1/state/now`);
    setState(await resp.json());
    setRefreshing(false);
  };

  useEffect(() => { load(); }, []);

  if (!state) return <ActivityIndicator style={{ flex: 1 }} />;

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => {
        setRefreshing(true); load();
      }} />}
    >
      {/* Affect state */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Chloe is feeling…</Text>
        <Text style={styles.affectLabel}>{state.affect.label}</Text>
        <Text style={styles.activity}>She is currently {state.current_activity}</Text>
        <View style={styles.dimensionsGrid}>
          {[
            { name: 'Valence', value: (state.affect.valence + 1) / 2 },
            { name: 'Arousal', value: state.affect.arousal },
            { name: 'Social', value: state.affect.social_pull },
            { name: 'Open', value: state.affect.openness },
          ].map(dim => (
            <View key={dim.name} style={styles.dimRow}>
              <Text style={styles.dimLabel}>{dim.name}</Text>
              <View style={styles.barBg}>
                <View style={[styles.barFill, {
                  width: `${Math.round(dim.value * 100)}%`,
                  backgroundColor: dim.value > 0.6 ? '#10B981' : dim.value > 0.3 ? '#F59E0B' : '#EF4444',
                }]} />
              </View>
              <Text style={styles.dimValue}>{Math.round(dim.value * 100)}%</Text>
            </View>
          ))}
        </View>
      </View>

      {/* Active goals */}
      {state.goals.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Active goals</Text>
          {state.goals.map(goal => (
            <View key={goal.id} style={styles.goalCard}>
              <Text style={styles.goalTitle}>{goal.title}</Text>
              {goal.description && (
                <Text style={styles.goalDesc}>{goal.description}</Text>
              )}
              {goal.progress !== null && (
                <View style={styles.barBg}>
                  <View style={[styles.barFill, {
                    width: `${Math.round((goal.progress || 0) * 100)}%`,
                    backgroundColor: '#8B5CF6',
                  }]} />
                </View>
              )}
            </View>
          ))}
        </View>
      )}

      {/* Interests */}
      {state.interests.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Current interests</Text>
          {state.interests.map(interest => (
            <View key={interest.id} style={styles.interestRow}>
              <Text style={styles.interestLabel}>{interest.label}</Text>
              <View style={[styles.barBg, { flex: 1, marginLeft: 8 }]}>
                <View style={[styles.barFill, {
                  width: `${Math.round(interest.intensity * 100)}%`,
                  backgroundColor: '#6366F1',
                }]} />
              </View>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8f8f8' },
  section: { backgroundColor: '#fff', margin: 12, borderRadius: 12, padding: 16 },
  sectionTitle: { fontSize: 14, fontWeight: '600', color: '#666', marginBottom: 12,
    textTransform: 'uppercase', letterSpacing: 0.5 },
  affectLabel: { fontSize: 22, fontWeight: '700', color: '#1a1a1a', marginBottom: 4 },
  activity: { fontSize: 14, color: '#666', marginBottom: 16 },
  dimensionsGrid: { gap: 8 },
  dimRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  dimLabel: { width: 52, fontSize: 13, color: '#666' },
  barBg: { flex: 1, height: 8, backgroundColor: '#E5E7EB', borderRadius: 4, overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: 4 },
  dimValue: { width: 36, fontSize: 12, color: '#999', textAlign: 'right' },
  goalCard: { marginBottom: 12 },
  goalTitle: { fontSize: 16, fontWeight: '600', color: '#1a1a1a', marginBottom: 4 },
  goalDesc: { fontSize: 14, color: '#666', marginBottom: 8 },
  interestRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  interestLabel: { width: 120, fontSize: 14, color: '#1a1a1a' },
});
```

## Dependencies

- E-03 (`affect/dims.py` — `load()`).
- E-04 (`affect/label.py` — `get_label()`).
- F-04 (`inner_goals`, `interest_garden` tables).

## Testing

### Manual UAT

1. Open the Now tab.
2. Verify affect label and all 4 dimension bars are displayed.
3. Verify active goals appear with progress bars.
4. Verify top 3 interests appear with intensity bars.
5. Pull to refresh — verify data updates.

## Acceptance criteria

- Affect label, current activity, all 4 dimensions visible.
- Active goals shown with optional progress bar.
- Top 3 interests shown with intensity bars.
- Pull-to-refresh updates data.
