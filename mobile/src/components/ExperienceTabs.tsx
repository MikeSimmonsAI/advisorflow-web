/**
 * The tab bar for whichever experience is active.
 *
 * ONE COMPONENT, FIVE EXPERIENCES. The alternative — a hand-written Tabs block
 * per group — is five places for the tab set to drift from
 * `resolve.ts::TABS`, and drift here shows up as a destination that exists in
 * one experience and silently does not in another.
 *
 * THE GUARD IS THE INTERESTING PART. A group renders its tabs only while the
 * ACTIVE experience is the one that owns them. Switching from Sales to
 * Executive unmounts the sales group; without the guard, a screen from the
 * previous experience can paint one more frame against the new request context
 * — the previous scope's query, sent with the new scope's headers. That is a
 * cross-tenant read with a plausible explanation, which is the worst kind.
 */

import React from 'react';
import { Redirect, Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

import { useExperience } from '../experience/ExperienceContext';
import { ExperienceKind, TABS } from '../experience/resolve';
import { palette, type as typography } from '../theme/tokens';

/** One glyph per destination. Keyed by tab name, shared across experiences so
 *  "calendar" is the same shape everywhere it appears. */
const ICONS: Record<string, keyof typeof Ionicons.glyphMap> = {
  'my-day': 'sunny-outline',
  home: 'home-outline',
  inbox: 'chatbubbles-outline',
  visits: 'calendar-outline',
  leads: 'people-outline',
  calendar: 'calendar-outline',
  compensation: 'cash-outline',
  team: 'people-circle-outline',
  pipeline: 'trending-up-outline',
  approvals: 'checkmark-done-outline',
  command: 'grid-outline',
  organizations: 'business-outline',
  revenue: 'bar-chart-outline',
  customers: 'briefcase-outline',
  issues: 'warning-outline',
  more: 'ellipsis-horizontal',
};

export function ExperienceTabs({ kind }: { kind: ExperienceKind }) {
  const { active } = useExperience();
  const isProgram = kind === 'program';

  // No experience settled yet, or the person switched away from this group.
  // Sending them to `/` lets the index screen redirect to whatever is active
  // now, rather than leaving a dead group mounted.
  if (!active) return null;
  if (active.kind !== kind) return <Redirect href={'/' as never} />;

  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: isProgram ? '#F5F8FC' : palette.ground },
        headerTintColor: isProgram ? '#183453' : palette.text,
        headerShadowVisible: false,
        headerTitleStyle: { color: isProgram ? '#183453' : palette.text, ...typography.heading },
        tabBarStyle: {
          backgroundColor: isProgram ? '#FFFFFF' : palette.surfaceRaised,
          borderTopColor: isProgram ? '#E2EAF4' : palette.border,
          height: 84,
          paddingTop: 6,
          paddingBottom: 24,
        },
        tabBarActiveTintColor: isProgram ? '#1668AD' : palette.accentSoft,
        tabBarInactiveTintColor: isProgram ? '#75889D' : palette.textFaint,
        tabBarLabelStyle: { ...typography.micro, textTransform: 'none' },
        sceneStyle: { backgroundColor: isProgram ? '#F5F8FC' : palette.ground },
      }}
    >
      {TABS[kind].map((tab) => (
        <Tabs.Screen
          key={tab.name}
          name={tab.name}
          options={{
            title: tab.title,
            tabBarIcon: ({ color, size }) => (
              <Ionicons
                name={ICONS[tab.name] ?? 'ellipse-outline'}
                color={color}
                size={size ?? 22}
              />
            ),
          }}
        />
      ))}
    </Tabs>
  );
}
