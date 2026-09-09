/**
 * THE EXPERIENCE SWITCHER.
 *
 * It renders `contexts` from the server and invents nothing. Mike may have
 * Owner, Sales, Executive and a customer workspace; another person has Sales
 * alone; a third has only Executive. Each of them sees exactly what
 * `/auth/my-contexts` returned for them, and an experience that is not in that
 * list has no row here and no route to it.
 *
 * SWITCHING SELECTS A SCOPE. IT NEVER GRANTS ONE. The work of the switch —
 * dropping the query cache, setting or clearing `X-Workspace-Id`, and asking
 * the server to confirm a workspace membership before the first screen paints —
 * happens in ExperienceContext, which explains why each of those is a tenant
 * isolation control rather than a nicety.
 */

import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { useExperience } from '../src/experience/ExperienceContext';
import type { ExperienceKind } from '../src/experience/resolve';
import {
  Card, EmptyState, Loading, Pill, Row, Screen, ScreenTitle,
} from '../src/components/ui';
import { palette, space, type as typography } from '../src/theme/tokens';

const BLURB: Record<ExperienceKind, string> = {
  sales: 'Your day, your deals, your calendar and your pay.',
  manager: 'Everything a rep has, plus your team, pipeline and approvals.',
  advisor: 'One customer workspace: their leads, replies and calendar.',
  executive: 'The organizations assigned to you, and what moved.',
  owner: 'Platform command: critical issues, customers and revenue.',
};

export default function SwitchExperience() {
  const { experiences, active, switchTo, switching, switchError } = useExperience();
  const [pending, setPending] = useState<string | null>(null);

  async function choose(key: string) {
    setPending(key);
    const ok = await switchTo(key);
    setPending(null);
    if (ok) router.back();
  }

  if (!experiences.length) {
    return (
      <Screen>
        <EmptyState
          title="No experiences yet"
          body="Your account has not been given a sales, workspace or executive context."
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <ScreenTitle
        title="Switch experience"
        subtitle="Only what your account is authorized for appears here."
      />

      {switchError ? (
        <Card accent={palette.danger}>
          <Text style={styles.error}>{switchError}</Text>
        </Card>
      ) : null}

      {experiences.map((exp) => {
        const isActive = exp.key === active?.key;
        return (
          <Row
            key={exp.key}
            title={exp.label}
            subtitle={exp.detail ?? BLURB[exp.kind]}
            meta={isActive ? undefined : BLURB[exp.kind]}
            accent={isActive ? palette.accent : undefined}
            onPress={() => { if (!switching) void choose(exp.key); }}
            right={
              pending === exp.key
                ? <Loading label="" />
                : isActive
                  ? <Pill label="Current" tone="accent" />
                  : undefined
            }
          />
        );
      })}

      <View style={styles.footer}>
        <Text style={styles.note}>
          Switching changes what you are looking at, never what you are allowed
          to see. Data from the experience you leave is cleared before the next
          one loads.
        </Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  error: { ...typography.caption, color: palette.danger },
  footer: { marginTop: space.lg },
  note: { ...typography.caption, color: palette.textFaint },
});
