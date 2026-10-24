import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { program, shortTime, safeText } from '../../src/program/data';
import { Page, Heading, Section, Panel, Line, State, blue, muted } from '../../src/program/surfaces';

type Filter = 'all' | 'needs_reply';
export default function ProgramInbox() {
  const [filter, setFilter] = useState<Filter>('needs_reply');
  const refresh = useScopedRefresh();
  const query = useScopedQuery(['program', 'inbox', filter], () => program.overview(filter), { refetchInterval: 60000 });
  const items = query.data?.inbox ?? [];
  return <Page refresh={refresh} refreshing={query.isRefetching}>
    <Heading title="Family inbox" subtitle="Read the latest messages and see who needs a personal response." />
    <View style={{ flexDirection: 'row', gap: 10 }}>
      {(['needs_reply', 'all'] as Filter[]).map(f => <Pressable key={f}
        accessibilityRole="button" accessibilityState={{ selected: f === filter }}
        onPress={() => setFilter(f)}
        style={{ paddingVertical: 11, paddingHorizontal: 16, borderRadius: 12,
          backgroundColor: filter === f ? blue : '#E5EDF6' }}>
        <Text style={{ fontWeight: '700', color: filter === f ? '#fff' : muted }}>
          {f === 'all' ? 'All conversations' : 'Needs reply'}
        </Text>
      </Pressable>)}
    </View>
    {query.isPending && !query.data ? <State title="Loading family messages" busy /> : null}
    {query.isError ? <State title="Inbox unavailable" detail="Your account must have access to this program." retry={refresh} /> : null}
    {query.data ? <>
      <Section title={filter === 'all' ? 'Recent conversations' : 'Awaiting attention'}
        hint={items.length + ' shown'} />
      <Panel>{items.length ? items.map((i) => <Line
        key={i.lead_id} title={i.name} detail={safeText(i.summary)}
        meta={[i.badge || i.handling_status, i.channel_label || i.channel, i.location, shortTime(i.received_at)].filter(Boolean).join(' · ')}
        urgent={i.sla_breached}
        onPress={() => router.push(('/program/family/' + encodeURIComponent(i.lead_id)) as never)}
      />) : <State title={filter === 'all' ? 'No conversations to display' : 'Nothing awaiting a reply'}
        detail="Pull down to refresh when new family replies arrive." />}</Panel>
    </> : null}
  </Page>;
}
