import React from 'react';
import { View, Text, Pressable } from 'react-native';
import { Redirect, router, useLocalSearchParams } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useExperience } from '../../../src/experience/ExperienceContext';
import { useScopedQuery, useScopedRefresh } from '../../../src/hooks/useApi';
import { program, shortTime, safeText } from '../../../src/program/data';
import { Page, Heading, Section, Panel, Notice, State, ink, muted, blue } from '../../../src/program/surfaces';

export default function ProgramFamilyDetail() {
  const { id } = useLocalSearchParams<{ id?: string }>();
  const { active } = useExperience();
  const refresh = useScopedRefresh();
  const allowed = active?.kind === 'program' && !!id && !id.includes('/') && id.length < 160;
  const query = useScopedQuery(['program', 'family', id], () => program.family(id!), { enabled: allowed, refetchInterval: 60000 });
  if (!allowed) return <Redirect href="/" />;
  const family = query.data;
  return <><StatusBar style="dark" />
    <Page refresh={refresh} refreshing={query.isRefetching}>
      <Pressable accessibilityRole="button" onPress={() => router.back()} style={{ paddingVertical: 5 }}>
        <Text style={{ color: blue, fontWeight: '700' }}>‹ Back to program</Text>
      </Pressable>
      <Heading title={family?.name || 'Family conversation'} subtitle={family?.location || 'Live conversation history'} />
      {query.isPending && !family ? <State title="Loading conversation" busy /> : null}
      {query.isError ? <State title="Conversation not available" detail="Access to this family must be granted by the program." retry={refresh} /> : null}
      {family ? <>
        {family.booking?.when ? <Notice text={'Visit: ' + shortTime(family.booking.when) + ' · ' + (family.booking.status || 'Scheduled')} /> : null}
        {family.setter?.handoff_reason ? <Notice danger text={'Advisor attention: ' + family.setter.handoff_reason} /> : null}
        <Section title="Conversation history" hint={family.thread.length + ' entries'} />
        {family.thread.length ? family.thread.map((m, i) =>
          <View key={String(i) + '-' + m.at} style={{
            alignSelf: m.who === 'family' ? 'flex-start' : 'flex-end',
            maxWidth: '95%', minWidth: '55%', borderRadius: 14, padding: 14,
            backgroundColor: m.who === 'family' ? '#FFFFFF' : '#E6F1FA',
            borderWidth: 1, borderColor: '#E0EAF5', gap: 5,
          }}>
            <Text style={{ fontSize: 11, fontWeight: '700', color: m.who === 'family' ? ink : blue }}>
              {m.who === 'family' ? 'Family' : m.who === 'ai' ? 'AI draft (not sent)' : 'Team'} · {m.channel || 'Message'}
            </Text>
            <Text style={{ fontSize: 14, lineHeight: 21, color: ink }}>{safeText(m.text)}</Text>
            <Text style={{ fontSize: 11, color: muted }}>
              {shortTime(m.at)}{m.delivery && m.delivery !== 'delivered' ? ' · ' + m.delivery : ''}
            </Text>
          </View>
        ) : <Panel><State title="No messages recorded yet" /></Panel>}
        <Notice text="Preview is read-only. Use the authorized program workspace to reply, take ownership or book a visit." />
      </> : null}
    </Page>
  </>;
}
