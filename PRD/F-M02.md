# F-M02 · Mobile chat screen — WebSocket + history

## Overview

Connect to `wss://{server}/v1/mobile/ws`. Render incoming messages as chat bubbles. Render Chloe's messages with an `artifact_preview` card (track title for Spotify, event title for Calendar). Send user messages over the socket. Cache last 100 messages in local SQLite (Expo SQLite).

## Context

The chat screen is the primary daily interaction surface. WebSocket is chosen over HTTP polling for real-time feel and to support streaming tokens. The artifact preview card surfaces the real-world actions Chloe has taken (queued a track, added an event) so Teo can see and revert them without leaving the chat.

## WebSocket protocol (server-side implementation)

```python
# chloe/channels/mobile_ws.py

from fastapi import WebSocket
import json
from chloe.channels.chat_api import chat_2_0
from chloe.state.kv import set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("mobile_ws")


async def handle_mobile_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    history = []
    log.info("mobile_ws_connected")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "message":
                user_text = data.get("text", "").strip()
                if not user_text:
                    continue

                kv_set("last_chat_seen", __import__("datetime").datetime.utcnow().isoformat())

                response = await chat_2_0(user_text, history)
                history.append({"role": "user", "text": user_text})
                history.append({"role": "assistant", "text": response})
                history = history[-40:]

                # Check if any actions were taken during this turn
                from chloe.actions.audit import recent as audit_recent
                recent = audit_recent(n=1)
                artifact_preview = None
                if recent:
                    last = recent[0]
                    if last.tool == "spotify" and last.state == "executed":
                        artifact_preview = {"type": "spotify_track", "text": last.intent}
                    elif last.tool == "calendar" and last.state == "executed":
                        artifact_preview = {"type": "calendar_event", "text": last.intent}

                await websocket.send_json({
                    "type": "message",
                    "role": "assistant",
                    "text": response,
                    "artifact_preview": artifact_preview,
                    "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                })

    except Exception as exc:
        if "disconnect" in str(exc).lower():
            log.info("mobile_ws_disconnected")
        else:
            log.warning("mobile_ws_error", error=str(exc))
```

Register in `app.py`:
```python
@app.websocket("/v1/mobile/ws")
async def mobile_ws(websocket: WebSocket):
    await handle_mobile_ws(websocket)
```

## React Native implementation

### Types

```typescript
// types/chat.ts
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  timestamp: string;
  artifactPreview?: {
    type: 'spotify_track' | 'calendar_event' | 'note';
    text: string;
  } | null;
}
```

### Chat screen

```typescript
// app/(tabs)/index.tsx
import { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, FlatList, TextInput, TouchableOpacity, StyleSheet, KeyboardAvoidingView, Platform
} from 'react-native';
import { Config } from '../../constants/Config';
import { ChatMessage } from '../../types/chat';
import { MessageBubble } from '../../components/MessageBubble';
import { ArtifactCard } from '../../components/ArtifactCard';
import * as SQLite from 'expo-sqlite';
import { nanoid } from 'nanoid/non-secure';

const db = SQLite.openDatabaseSync('chloe_chat.db');

// Initialize DB
db.execSync(`CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  artifact_json TEXT
)`);

export default function ChatScreen() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [connected, setConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const listRef = useRef<FlatList>(null);

  // Load cached messages on mount
  useEffect(() => {
    const rows = db.getAllSync<any>(
      'SELECT * FROM messages ORDER BY timestamp ASC LIMIT 100'
    );
    setMessages(rows.map(r => ({
      ...r,
      artifactPreview: r.artifact_json ? JSON.parse(r.artifact_json) : null,
    })));
  }, []);

  // WebSocket connection
  useEffect(() => {
    const connect = () => {
      const socket = new WebSocket(`${Config.WS_BASE_URL}/v1/mobile/ws`);

      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        setTimeout(connect, 3000); // Reconnect after 3s
      };
      socket.onerror = () => setConnected(false);

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'message') {
          const msg: ChatMessage = {
            id: nanoid(),
            role: data.role,
            text: data.text,
            timestamp: data.timestamp,
            artifactPreview: data.artifact_preview,
          };
          setMessages(prev => [...prev.slice(-99), msg]);
          // Persist to SQLite
          db.runSync(
            'INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?)',
            [msg.id, msg.role, msg.text, msg.timestamp,
             msg.artifactPreview ? JSON.stringify(msg.artifactPreview) : null]
          );
        }
      };

      ws.current = socket;
    };

    connect();
    return () => ws.current?.close();
  }, []);

  const sendMessage = useCallback(() => {
    if (!input.trim() || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;

    const msg: ChatMessage = {
      id: nanoid(),
      role: 'user',
      text: input.trim(),
      timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev.slice(-99), msg]);
    db.runSync('INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?)',
      [msg.id, 'user', msg.text, msg.timestamp, null]);

    ws.current.send(JSON.stringify({ type: 'message', text: input.trim() }));
    setInput('');
  }, [input]);

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={m => m.id}
        renderItem={({ item }) => (
          <View>
            <MessageBubble message={item} />
            {item.artifactPreview && <ArtifactCard preview={item.artifactPreview} />}
          </View>
        )}
        onContentSizeChange={() => listRef.current?.scrollToEnd()}
        style={styles.list}
      />
      <View style={styles.inputRow}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder={connected ? 'Message Chloe...' : 'Connecting...'}
          multiline
          returnKeyType="send"
          onSubmitEditing={sendMessage}
        />
        <TouchableOpacity style={styles.sendBtn} onPress={sendMessage}>
          <Text style={styles.sendText}>Send</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  list: { flex: 1, padding: 12 },
  inputRow: { flexDirection: 'row', padding: 8, borderTopWidth: 1, borderTopColor: '#eee' },
  input: { flex: 1, borderWidth: 1, borderColor: '#ddd', borderRadius: 20, paddingHorizontal: 14,
    paddingVertical: 8, marginRight: 8, maxHeight: 120 },
  sendBtn: { backgroundColor: '#8B5CF6', borderRadius: 20, paddingHorizontal: 16,
    paddingVertical: 8, justifyContent: 'center' },
  sendText: { color: '#fff', fontWeight: '600' },
});
```

## Dependencies

- `expo-sqlite` — local message cache.
- `nanoid/non-secure` — client-side message ID generation.
- F-V03 (or equivalent WebSocket endpoint `/v1/mobile/ws`).

## Testing

### Manual UAT

1. Start server: `python -m chloe`
2. Start app: `npx expo start`
3. Send a message from the chat screen
4. Verify Chloe's response appears as a bubble
5. Kill and reopen app — verify last 100 messages are restored from SQLite cache
6. If a Spotify action was taken, verify artifact card appears below response

## Acceptance criteria

- WebSocket connects automatically on screen load.
- User messages appear as right-aligned bubbles; Chloe's as left-aligned.
- `artifact_preview` data renders as an `ArtifactCard` below Chloe's message.
- Last 100 messages cached in Expo SQLite and restored on app relaunch.
- Auto-reconnects after 3 seconds on disconnect.
