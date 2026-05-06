# F-M10 · Voice button in mobile chat

## Overview

A hold-to-talk button in the chat screen opens a WebSocket to `/v1/voice`. Streams audio from the microphone. Displays a waveform animation while Chloe responds. Plays received audio chunks via `expo-av`. Releasing the button sends an interrupt event.

## Context

The voice button completes the realtime voice pipeline: it's the user-facing trigger for F-V03. The hold-to-talk interaction model (press and hold, release to send interrupt) is familiar and avoids accidental activations. The waveform animation gives visual feedback that audio is being transmitted, and playing back Chloe's response closes the loop.

## Implementation

### Audio permission

```typescript
// hooks/useAudioPermission.ts
import { Audio } from 'expo-av';
import { useEffect, useState } from 'react';

export function useAudioPermission(): boolean {
  const [granted, setGranted] = useState(false);
  useEffect(() => {
    Audio.requestPermissionsAsync().then(({ granted }) => setGranted(granted));
  }, []);
  return granted;
}
```

### Voice button component

```typescript
// components/VoiceButton.tsx
import { useState, useRef, useCallback, useEffect } from 'react';
import {
  TouchableOpacity, View, Text, StyleSheet, Animated
} from 'react-native';
import { Audio } from 'expo-av';
import { Config } from '../constants/Config';
import { Ionicons } from '@expo/vector-icons';

interface Props {
  onTranscript?: (text: string) => void;
}

export function VoiceButton({ onTranscript }: Props) {
  const [isActive, setIsActive] = useState(false);
  const [isChloeResponding, setIsChloeResponding] = useState(false);
  const waveAnim = useRef(new Animated.Value(1)).current;
  const wsRef = useRef<WebSocket | null>(null);
  const recordingRef = useRef<Audio.Recording | null>(null);
  const soundRef = useRef<Audio.Sound | null>(null);
  const audioBufferRef = useRef<Uint8Array[]>([]);

  // Waveform animation
  useEffect(() => {
    if (isActive || isChloeResponding) {
      Animated.loop(
        Animated.sequence([
          Animated.timing(waveAnim, { toValue: 1.3, duration: 400, useNativeDriver: true }),
          Animated.timing(waveAnim, { toValue: 1.0, duration: 400, useNativeDriver: true }),
        ])
      ).start();
    } else {
      waveAnim.setValue(1);
    }
  }, [isActive, isChloeResponding]);

  const startVoice = useCallback(async () => {
    setIsActive(true);

    // Setup audio
    await Audio.setAudioModeAsync({
      allowsRecordingIOS: true,
      playsInSilentModeIOS: true,
    });

    // Connect WebSocket
    const ws = new WebSocket(`${Config.WS_BASE_URL}/v1/voice`);
    wsRef.current = ws;

    ws.onopen = () => {
      // Start recording
      Audio.Recording.createAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY)
        .then(({ recording }) => {
          recordingRef.current = recording;
          recording.setOnRecordingStatusUpdate((status) => {
            if (status.metering !== undefined && ws.readyState === WebSocket.OPEN) {
              // Send audio chunk (polled every ~100ms)
            }
          });
        });
    };

    ws.onmessage = async (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Received audio chunk from Chloe
        setIsChloeResponding(true);
        audioBufferRef.current.push(new Uint8Array(event.data));
      } else {
        const data = JSON.parse(event.data);
        if (data.type === 'transcript' && data.final) {
          onTranscript?.(data.text);
        } else if (data.type === 'done') {
          setIsChloeResponding(false);
          await playAudioBuffer();
          audioBufferRef.current = [];
        }
      }
    };

    ws.onclose = () => {
      setIsActive(false);
      setIsChloeResponding(false);
    };
  }, [onTranscript]);

  const stopVoice = useCallback(async () => {
    setIsActive(false);

    // Stop recording
    if (recordingRef.current) {
      await recordingRef.current.stopAndUnloadAsync();

      // Send final audio data
      const uri = recordingRef.current.getURI();
      if (uri && wsRef.current?.readyState === WebSocket.OPEN) {
        const response = await fetch(uri);
        const blob = await response.blob();
        // In React Native, we can't easily stream; send as one chunk
        wsRef.current.send(await blob.arrayBuffer());
      }
      recordingRef.current = null;
    }

    // Send interrupt when button released
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'interrupt' }));
    }
  }, []);

  const playAudioBuffer = async () => {
    if (audioBufferRef.current.length === 0) return;

    // Concatenate all audio chunks
    const totalLen = audioBufferRef.current.reduce((acc, arr) => acc + arr.length, 0);
    const combined = new Uint8Array(totalLen);
    let offset = 0;
    for (const chunk of audioBufferRef.current) {
      combined.set(chunk, offset);
      offset += chunk.length;
    }

    // Play via expo-av (PCM bytes need to be WAV-wrapped for expo-av)
    const wavBytes = pcmToWav(combined, 24000);
    const { sound } = await Audio.Sound.createAsync(
      { uri: `data:audio/wav;base64,${btoa(String.fromCharCode(...wavBytes))}` },
      { shouldPlay: true }
    );
    soundRef.current = sound;
  };

  return (
    <View style={styles.container}>
      <Animated.View style={[styles.ripple, { transform: [{ scale: waveAnim }] }]} />
      <TouchableOpacity
        style={[styles.button, (isActive || isChloeResponding) && styles.buttonActive]}
        onPressIn={startVoice}
        onPressOut={stopVoice}
        activeOpacity={0.8}
      >
        <Ionicons
          name={isActive ? "mic" : isChloeResponding ? "volume-high" : "mic-outline"}
          size={28}
          color="#fff"
        />
      </TouchableOpacity>
      {(isActive || isChloeResponding) && (
        <Text style={styles.label}>{isActive ? "Listening…" : "Chloe is responding…"}</Text>
      )}
    </View>
  );
}

// Simple PCM→WAV wrapper
function pcmToWav(pcmData: Uint8Array, sampleRate: number): Uint8Array {
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * bitsPerSample / 8;
  const dataSize = pcmData.length;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeStr = (o: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
  writeStr(0, 'RIFF'); view.setUint32(4, 36 + dataSize, true);
  writeStr(8, 'WAVE'); writeStr(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true); view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true); view.setUint16(32, numChannels * bitsPerSample / 8, true);
  view.setUint16(34, bitsPerSample, true); writeStr(36, 'data');
  view.setUint32(40, dataSize, true);
  new Uint8Array(buffer).set(pcmData, 44);
  return new Uint8Array(buffer);
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', marginRight: 4 },
  ripple: { position: 'absolute', width: 52, height: 52, borderRadius: 26,
    backgroundColor: '#8B5CF620' },
  button: { width: 44, height: 44, borderRadius: 22, backgroundColor: '#8B5CF6',
    alignItems: 'center', justifyContent: 'center' },
  buttonActive: { backgroundColor: '#7C3AED' },
  label: { position: 'absolute', bottom: -18, fontSize: 10, color: '#666', whiteSpace: 'nowrap' },
});
```

### Add to chat screen

```typescript
// In app/(tabs)/index.tsx — add VoiceButton to input row
import { VoiceButton } from '../../components/VoiceButton';

// In the inputRow View:
<VoiceButton onTranscript={(text) => setInput(prev => prev + text)} />
```

## Dependencies

- F-V03 (`voice/realtime.py` — server-side WebSocket).
- `expo-av` — audio recording and playback.
- F-M02 (chat screen — VoiceButton added here).

## Testing

### Manual UAT

1. Hold the microphone button — verify "Listening…" label appears.
2. Speak a message — verify waveform animation pulses.
3. Release the button — verify Chloe's voice response plays through speakers.
4. Verify the transcript appears in the chat bubbles.
5. Hold the button while Chloe is responding — verify interrupt stops her mid-sentence.

## Acceptance criteria

- Hold-to-talk button connects to `/v1/voice` WebSocket.
- Audio recorded and sent while button held.
- Release sends interrupt event.
- Waveform animation shown during active recording and Chloe's response.
- Chloe's audio response plays via `expo-av`.
- Transcript appears in chat history after voice turn.
