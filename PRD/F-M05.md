# F-M05 · Mobile Activity tab (audit feed)

## Overview

`GET /v1/audit?limit=50&offset=0`. Scrollable list showing timestamp, tool icon, verb, intent, state chip. "Show held back" toggle. Revert button for `kinetic` rows with a `reverse_verb`.

## Server-side endpoint

```python
# Add to admin or v1 routes:

@router.get("/v1/audit")
async def get_audit(limit: int = 50, offset: int = 0, include_held_back: bool = False):
    from chloe.state.db import get_connection
    conn = get_connection()

    where = ""
    if not include_held_back:
        where = "WHERE state NOT IN ('held_back', 'denied')"

    rows = conn.execute(
        f"SELECT * FROM actions {where} ORDER BY proposed_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()

    return [dict(r) for r in rows]
```

## React Native implementation

```typescript
// app/(tabs)/activity.tsx
import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, FlatList, Switch, TouchableOpacity,
  StyleSheet, RefreshControl, Alert
} from 'react-native';
import { Config } from '../../constants/Config';

const TOOL_ICONS: Record<string, string> = {
  spotify:   '🎵',
  gmail:     '📧',
  calendar:  '📅',
  notes:     '📝',
  reminders: '⏰',
  messages:  '💬',
  web_search: '🔍',
  smart_home: '🏠',
};

const STATE_COLORS: Record<string, string> = {
  executed:              '#10B981',
  awaiting_confirmation: '#F59E0B',
  held_back:             '#6B7280',
  denied:                '#EF4444',
  reverted:              '#8B5CF6',
  failed:                '#EF4444',
};

interface AuditRow {
  id: string;
  tool: string;
  verb: string;
  intent: string;
  state: string;
  proposed_at: string;
  authorization: string;
}

export default function ActivityScreen() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [showHeldBack, setShowHeldBack] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const resp = await fetch(
      `${Config.API_BASE_URL}/v1/audit?limit=50&include_held_back=${showHeldBack}`
    );
    const data = await resp.json();
    setRows(data);
    setRefreshing(false);
  }, [showHeldBack]);

  useEffect(() => { load(); }, [showHeldBack]);

  const handleRevert = async (row: AuditRow) => {
    Alert.alert('Revert', `Undo: ${row.intent}?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Revert',
        style: 'destructive',
        onPress: async () => {
          const resp = await fetch(
            `${Config.API_BASE_URL}/v1/actions/${row.id}/revert`,
            { method: 'POST' }
          );
          if (resp.ok) {
            setRows(prev => prev.map(r =>
              r.id === row.id ? { ...r, state: 'reverted' } : r
            ));
          } else {
            Alert.alert('Error', 'Could not revert action');
          }
        },
      },
    ]);
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Activity</Text>
        <View style={styles.toggleRow}>
          <Text style={styles.toggleLabel}>Show held back</Text>
          <Switch value={showHeldBack} onValueChange={setShowHeldBack} />
        </View>
      </View>
      <FlatList
        data={rows}
        keyExtractor={r => r.id}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => {
          setRefreshing(true); load();
        }} />}
        renderItem={({ item }) => (
          <ActivityRow row={item} onRevert={handleRevert} />
        )}
      />
    </View>
  );
}


function ActivityRow({ row, onRevert }: {
  row: AuditRow;
  onRevert: (r: AuditRow) => void;
}) {
  const icon = TOOL_ICONS[row.tool] || '⚡';
  const stateColor = STATE_COLORS[row.state] || '#6B7280';
  const time = new Date(row.proposed_at).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit',
  });
  const canRevert = row.state === 'executed' && row.authorization === 'kinetic';

  return (
    <View style={styles.row}>
      <Text style={styles.icon}>{icon}</Text>
      <View style={styles.rowContent}>
        <View style={styles.rowTop}>
          <Text style={styles.toolVerb}>{row.tool}.{row.verb}</Text>
          <Text style={styles.time}>{time}</Text>
        </View>
        <Text style={styles.intent} numberOfLines={2}>{row.intent}</Text>
        <View style={styles.rowBottom}>
          <View style={[styles.chip, { backgroundColor: stateColor + '22' }]}>
            <Text style={[styles.chipText, { color: stateColor }]}>{row.state}</Text>
          </View>
          {canRevert && (
            <TouchableOpacity style={styles.revertBtn} onPress={() => onRevert(row)}>
              <Text style={styles.revertText}>Revert</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8f8f8' },
  header: { backgroundColor: '#fff', padding: 16, borderBottomWidth: 1, borderBottomColor: '#eee' },
  headerTitle: { fontSize: 20, fontWeight: '700', marginBottom: 8 },
  toggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  toggleLabel: { fontSize: 14, color: '#666' },
  row: { flexDirection: 'row', backgroundColor: '#fff', margin: 8, borderRadius: 10,
    padding: 12, shadowColor: '#000', shadowOpacity: 0.05, shadowRadius: 4, elevation: 1 },
  icon: { fontSize: 24, marginRight: 12, marginTop: 2 },
  rowContent: { flex: 1 },
  rowTop: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  toolVerb: { fontSize: 13, fontWeight: '600', color: '#8B5CF6' },
  time: { fontSize: 12, color: '#999' },
  intent: { fontSize: 14, color: '#1a1a1a', marginBottom: 8 },
  rowBottom: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  chip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  chipText: { fontSize: 12, fontWeight: '600' },
  revertBtn: { paddingHorizontal: 10, paddingVertical: 4, backgroundColor: '#F3F4F6',
    borderRadius: 6 },
  revertText: { fontSize: 12, color: '#8B5CF6', fontWeight: '600' },
});
```

## Dependencies

- A-13 (admin audit endpoint — extend for mobile use).
- C-11 (revert endpoint).

## Testing

### Manual UAT

1. Perform 5 actions (mix of Spotify, calendar, notes) via the chat.
2. Open Activity tab — verify all 5 appear with correct tool icons and state chips.
3. Toggle "Show held back" — verify held-back actions appear/disappear.
4. Tap "Revert" on a calendar event — verify it disappears or shows "reverted" state.

## Acceptance criteria

- All executed, awaiting, and held-back actions visible (latter behind toggle).
- Tool icon, verb, intent, timestamp, and state chip shown for each row.
- State chip colors match: green=executed, amber=awaiting, grey=held_back, red=denied.
- "Revert" button shown only for `state=executed` and `authorization=kinetic`.
- Tapping "Revert" calls `POST /v1/actions/{id}/revert` and updates UI.
- Pull-to-refresh reloads the list.
