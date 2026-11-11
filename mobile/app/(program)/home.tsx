import React from 'react';
import { View } from 'react-native';
import { router } from 'expo-router';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import { useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { program, count, shortTime, safeText } from '../../src/program/data';
import { Page, Heading, Section, Panel, Metric, Line, Notice, State, Action } from '../../src/program/surfaces';

export default function ProgramHome() {
  const exp = useActiveExperience();
  const refresh = useScopedRefresh();
  const home = useScopedQuery(['program', 'overview'], () => program.overview(), { refetchInterval: 60000 });
  const pipeline = useScopedQuery(['program', 'pipeline'], program.pipeline, { refetchInterval: 60000 });
  const overview = home.data;
  const stats = pipeline.data;
  const attention = (overview?.inbox ?? []).filter(x => x.sla_breached || x.handling_status === 'new');
  return <Page refresh={refresh} refreshing={home.isRefetching || pipeline.isRefetching}>
    <Heading title="Your day, at a glance" subtitle={exp.label + ' · Live outreach overview'} />
    {home.isPending && !overview ? <State busy title="Preparing your briefing" /> : null}
    {home.isError ? <State title="We couldn't load the program" detail="Your data hasn't been replaced with estimates." retry={refresh} /> : null}
    {overview ? <>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        <Metric tone="strong" label="Needs attention" value={count(overview.counts?.needs_attention)} />
        <Metric label="Replies today" value={count(overview.counts?.replies_today)} />
        <Metric label="Visits booked" value={count(overview.counts?.visits_booked)} />
        <Metric label="Contacted today" value={count(overview.counts?.contacted_today)} />
      </View>
      {overview.owner_alert?.text ? <Notice danger text={overview.owner_alert.text} /> : null}
      <Section title="Priority conversations" hint={attention.length ? 'Newest and urgent' : 'Up to date'} />
      <Panel>
        {attention.length ? attention.slice(0, 5).map(i =>
          <Line key={i.lead_id} title={i.name} detail={safeText(i.summary)}
            meta={[i.location, i.sla_breached ? 'Time-sensitive' : i.badge].filter(Boolean).join(' · ')}
            urgent={i.sla_breached} onPress={() => router.push(('/program/family/' + encodeURIComponent(i.lead_id)) as never)} />)
          : <State title="No new replies need review" detail="This list will update when a family responds." />}
      </Panel>
      <Action label="Open family inbox" onPress={() => router.push('/(program)/inbox' as never)} />
      <Section title="Outreach snapshot" />
      <Panel>
        {pipeline.isPending && !stats ? <State title="Loading outreach" busy /> : null}
        {pipeline.isError ? <State title="Outreach details unavailable" detail="The family inbox above is still available." retry={refresh} /> : null}
        {stats ? <>
          <Line title="Families in outreach" meta={String(stats.in_outreach ?? '—')} />
          <Line title="AI conversations" meta={String(stats.ai_conversations ?? '—')} />
          <Line title="Appointments offered" meta={String(stats.appointments_offered ?? '—')} />
          <Line title="Calendar-confirmed appointments" meta={String(stats.appointments_confirmed ?? '—')} />
          <Line title="Failed texts today" meta={String(stats.failed_today?.count ?? '—')} urgent={!!stats.failed_today?.count} />
        </> : null}
      </Panel>
      <Section title="Next actions" />
      <Panel>{overview.tasks?.length ? overview.tasks.slice(0, 4).map((t, i) =>
        <Line key={String(t.id ?? i)} title={String(t.title || 'Team follow-up')}
          detail={String(t.message || t.description || 'Review in SCI Live')} />) :
        <State title="No team tasks listed" />}</Panel>
      <Notice text={'Updated ' + shortTime(overview.updated_at) + '. Pull down to refresh. This is a read-only pilot: no customer messages or bookings are changed.'} />
    </> : null}
  </Page>;
}
