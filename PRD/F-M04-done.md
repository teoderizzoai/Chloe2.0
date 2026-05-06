# F-M04 · Mobile Confirmations tab

## Overview

List `GET /v1/confirmations/pending`. Each ticket shows: preview text, diff if available, Yes/No/More buttons. Tap Yes → `POST /v1/confirmations/{id}/confirm`. Tap No → `POST /v1/confirmations/{id}/deny`. Tap More → show full action detail.

## Context

The Confirmations tab is the primary safety control for Teo — it's where kinetic-sensitive actions land before executing. The UI must be clear and quick: Teo should be able to confirm or deny within 2 taps. The "More" button surfaces the full action detail (tool, verb, args preview) for when he needs to understand what he's approving.

## Server-side endpoint (already in C-10)

```
GET  /v1/confirmations/pending
POST /v1/confirmations/{id}/confirm
POST /v1/confirmations/{id}/deny
```

## React Native implementation

```typescript
// app/(tabs)/confirmations.tsx
import { useState, useEffect, useCallback } from 'react';
import {
  View, Text, FlatList, TouchableOpacity, StyleSheet,
  RefreshControl, ActivityIndicator, Alert
} from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { Config } from '../../constants/Config';

interface ConfirmationTicket {
  id: string;
  action_tool: string;
  action_verb: string;
  preview: string;
  created_at: string;
  expires_at: string;
}

export default function ConfirmationsScreen() {
  const { ticketId: deepLinkTicketId } = useLocalSearchParams<{ ticketId?: string }>();
  const [tickets, setTickets] = useState<ConfirmationTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(deepLinkTicketId ?? null);

  const loadTickets = useCallback(async () => {
    try {
      const resp = await fetch(`${Config.API_BASE_URL}/v1/confirmations/pending`);
      const data: ConfirmationTicket[] = await resp.json();
      setTickets(data);
    } catch (e) {
      console.error('Failed to load tickets', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadTickets(); }, []);
  useEffect(() => {
    if (deepLinkTicketId) setExpandedId(deepLinkTicketId);
  }, [deepLinkTicketId]);

  const handleConfirm = async (ticket: ConfirmationTicket) => {
    try {
      await fetch(`${Config.API_BASE_URL}/v1/confirmations/${ticket.id}/confirm`, {
        method: 'POST',
      });
      setTickets(prev => prev.filter(t => t.id !== ticket.id));
    } catch (e) {
      Alert.alert('Error', 'Failed to confirm action');
    }
  };

  const handleDeny = async (ticket: ConfirmationTicket) => {
    try {
      await fetch(`${Config.API_BASE_URL}/v1/confirmations/${ticket.id}/deny`, {
        method: 'POST',
      });
      setTickets(prev => prev.filter(t => t.id !== ticket.id));
    } catch (e) {
      Alert.alert('Error', 'Failed to deny action');
    }
  };

  if (loading) {
    return <ActivityIndicator style={{ flex: 1 }} />;
  }

  return (
    <View style={styles.container}>
      {tickets.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyText}>No pending confirmations</Text>
        </View>
      ) : (
        <FlatList
          data={tickets}
          keyExtractor={t => t.id}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => {
            setRefreshing(true);
            loadTickets();
          }} />}
          renderItem={({ item }) => (
            <TicketCard
              ticket={item}
              expanded={expandedId === item.id}
              onToggleExpand={() => setExpandedId(
                expandedId === item.id ? null : item.id
              )}
              onConfirm={handleConfirm}
              onDeny={handleDeny}
            />
          )}
        />
      )}
    </View>
  );
}


function TicketCard({ ticket, expanded, onToggleExpand, onConfirm, onDeny }: {
  ticket: ConfirmationTicket;
  expanded: boolean;
  onToggleExpand: () => void;
  onConfirm: (t: ConfirmationTicket) => void;
  onDeny: (t: ConfirmationTicket) => void;
}) {
  return (
    <View style={styles.card}>
      <Text style={styles.toolBadge}>{ticket.action_tool}.{ticket.action_verb}</Text>
      <Text style={styles.preview}>{ticket.preview}</Text>

      {expanded && (
        <View style={styles.detail}>
          <Text style={styles.detailLabel}>Created</Text>
          <Text style={styles.detailValue}>{new Date(ticket.created_at).toLocaleString()}</Text>
          <Text style={styles.detailLabel}>Expires</Text>
          <Text style={styles.detailValue}>{new Date(ticket.expires_at).toLocaleString()}</Text>
        </View>
      )}

      <View style={styles.buttonRow}>
        <TouchableOpacity
          style={[styles.btn, styles.yesBtn]}
          onPress={() => onConfirm(ticket)}
        >
          <Text style={styles.btnText}>Yes, do it</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.btn, styles.noBtn]}
          onPress={() => onDeny(ticket)}
        >
          <Text style={styles.btnText}>No</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.btn, styles.moreBtn]} onPress={onToggleExpand}>
          <Text style={styles.moreText}>{expanded ? 'Less' : 'More'}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8f8f8' },
  empty: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  emptyText: { color: '#999', fontSize: 16 },
  card: { backgroundColor: '#fff', margin: 12, borderRadius: 12, padding: 16,
    shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1, shadowRadius: 4, elevation: 2 },
  toolBadge: { fontSize: 12, color: '#8B5CF6', fontWeight: '600', marginBottom: 6 },
  preview: { fontSize: 16, color: '#1a1a1a', marginBottom: 12 },
  detail: { backgroundColor: '#f0f0f0', borderRadius: 8, padding: 12, marginBottom: 12 },
  detailLabel: { fontSize: 12, color: '#666', marginTop: 4 },
  detailValue: { fontSize: 14, color: '#1a1a1a' },
  buttonRow: { flexDirection: 'row', gap: 8 },
  btn: { flex: 1, borderRadius: 8, paddingVertical: 10, alignItems: 'center' },
  yesBtn: { backgroundColor: '#10B981' },
  noBtn: { backgroundColor: '#EF4444' },
  moreBtn: { backgroundColor: '#E5E7EB', flex: 0.5 },
  btnText: { color: '#fff', fontWeight: '600' },
  moreText: { color: '#666', fontWeight: '600' },
});
```

## Dependencies

- C-07 (`confirm.py` — ticket lifecycle).
- C-10 (HTTP endpoints for confirm/deny/pending).
- F-M03 (deep-link navigation from push notification).

## Testing

### Manual UAT

1. Create a kinetic-sensitive action (e.g., trigger `gmail.send_reply` from admin).
2. Observe the ticket appears in the Confirmations tab.
3. Tap "More" — verify action details expand.
4. Tap "Yes, do it" — verify ticket disappears and action executes on server.
5. Create another ticket — tap "No" — verify denial and `held_back` memory on server.
6. Receive a confirmation push (F-M03) — verify deep link opens the Confirmations tab with correct ticket pre-expanded.

## Acceptance criteria

- `GET /v1/confirmations/pending` results displayed in a list.
- "Yes" → `POST /v1/confirmations/{id}/confirm` → ticket removed from list.
- "No" → `POST /v1/confirmations/{id}/deny` → ticket removed from list.
- "More" → expands action detail inline.
- Deep-link `ticketId` parameter pre-expands the matching ticket.
- Empty state shown when no pending tickets.
- Pull-to-refresh reloads the list.
