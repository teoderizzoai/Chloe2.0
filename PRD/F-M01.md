# F-M01 · Mobile app scaffold — React Native (Expo)

## Overview

Bootstrap `mobile/ChloeApp/` with Expo + TypeScript. Configure ESLint and Prettier. Set up React Navigation with 5 tabs: Chat, Confirmations, Activity, Now, Settings. Placeholder screens only — no logic.

## Context

The mobile app is the primary interface for Teo on the go: push notifications, confirmation UI, voice button, and the "Now" tab showing Chloe's state. Expo is chosen for its managed workflow (no native build configuration needed until push notifications require it), strong TypeScript support, and straightforward TestFlight deployment via EAS Build.

## Commands

```bash
# Bootstrap
npx create-expo-app ChloeApp --template expo-template-blank-typescript
cd ChloeApp

# Navigation
npx expo install @react-navigation/native @react-navigation/bottom-tabs react-native-screens react-native-safe-area-context

# Dev dependencies
npm install --save-dev eslint @typescript-eslint/eslint-plugin @typescript-eslint/parser prettier eslint-config-prettier
```

## Directory structure

```
mobile/ChloeApp/
├── app/
│   ├── (tabs)/
│   │   ├── _layout.tsx       # Tab navigator
│   │   ├── index.tsx         # Chat tab
│   │   ├── confirmations.tsx # Confirmations tab
│   │   ├── activity.tsx      # Activity/audit tab
│   │   ├── now.tsx           # Now/state tab
│   │   └── settings.tsx      # Settings tab
│   └── _layout.tsx           # Root layout
├── components/
│   └── PlaceholderScreen.tsx
├── constants/
│   └── Config.ts             # API base URL etc.
├── .eslintrc.js
├── .prettierrc
├── tsconfig.json
└── app.json
```

## Tab navigator

```typescript
// app/(tabs)/_layout.tsx
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

export default function TabLayout() {
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: '#8B5CF6' }}>
      <Tabs.Screen
        name="index"
        options={{
          title: 'Chat',
          tabBarIcon: ({ color }) => (
            <Ionicons name="chatbubble-outline" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="confirmations"
        options={{
          title: 'Confirm',
          tabBarIcon: ({ color }) => (
            <Ionicons name="checkmark-circle-outline" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="activity"
        options={{
          title: 'Activity',
          tabBarIcon: ({ color }) => (
            <Ionicons name="list-outline" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="now"
        options={{
          title: 'Now',
          tabBarIcon: ({ color }) => (
            <Ionicons name="heart-outline" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: 'Settings',
          tabBarIcon: ({ color }) => (
            <Ionicons name="settings-outline" size={24} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
```

## Placeholder screen

```typescript
// components/PlaceholderScreen.tsx
import { View, Text, StyleSheet } from 'react-native';

interface Props {
  name: string;
}

export function PlaceholderScreen({ name }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>{name}</Text>
      <Text style={styles.subtitle}>Coming soon</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#fff' },
  title: { fontSize: 24, fontWeight: '600', color: '#1a1a1a' },
  subtitle: { fontSize: 16, color: '#666', marginTop: 8 },
});
```

## Config

```typescript
// constants/Config.ts
const isDev = __DEV__;

export const Config = {
  API_BASE_URL: isDev ? 'http://localhost:8000' : 'https://chloe.your-server.com',
  WS_BASE_URL: isDev ? 'ws://localhost:8000' : 'wss://chloe.your-server.com',
};
```

## ESLint + Prettier config

```javascript
// .eslintrc.js
module.exports = {
  extends: [
    'expo',
    '@typescript-eslint/recommended',
    'prettier',
  ],
  rules: {
    '@typescript-eslint/no-unused-vars': 'warn',
  },
};
```

## Dependencies

- None from the backend (this is a standalone React Native project).

## Testing

### Manual UAT

1. `cd mobile/ChloeApp && npx expo start`
2. Open in Expo Go on iOS or Android emulator
3. Verify all 5 tabs are visible and tappable
4. Verify each tab shows the placeholder screen with the correct name
5. Verify TypeScript compilation passes: `npx tsc --noEmit`
6. Verify ESLint passes: `npx eslint app/ --ext .ts,.tsx`

### CI checks

```yaml
# .github/workflows/mobile.yml
mobile-lint:
  runs-on: ubuntu-latest
  defaults:
    run:
      working-directory: mobile/ChloeApp
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-node@v4
      with:
        node-version: '20'
        cache: 'npm'
        cache-dependency-path: mobile/ChloeApp/package-lock.json
    - run: npm ci
    - run: npx tsc --noEmit
    - run: npx eslint app/ --ext .ts,.tsx --max-warnings 0
```

## Acceptance criteria

- `npx expo start` runs without errors.
- Emulator/device shows 5 tabs with correct names and icons.
- Each tab renders the placeholder screen.
- TypeScript compilation passes (`tsc --noEmit`).
- ESLint passes with zero warnings.
