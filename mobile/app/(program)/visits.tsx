import React from 'react';
import { router } from 'expo-router';
import { useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import { program, shortTime } from '../../src/program/data';
import { Page, Heading, Section, Panel, Line, Notice, State } from '../../src/program/surfaces';

export default function ProgramVisits() {
  const refresh = useScopedRefresh();
  const query = useScopedQuery(['program', 'upcoming-visits'], program.visits, { refetchInterval: 60000 });
  const items = query.data?.items ?? [];
  return <Page refresh={refresh} refreshing={query.isRefetching}>
    <Heading title="Upcoming visits" subtitle="Calendar bookings from the authorized program. No estimates." />
    {query.isPending && !query.data ? <State busy title="Checking the appointment calendar" /> : null}
    {query.isError ? <State title="Visits not available for this account"
      detail="This list requires the program's manager-level access. Your other program information remains available." retry={refresh} /> : null}
    {query.data ? <>
      <Section title="Appointment list" hint={items.length + ' shown'} />
      <Panel>{items.length ? items.map(v => <Line key={v.id} title={v.name}
        detail={v.location || 'Location not supplied'}
        meta={shortTime(v.when) + ' · ' + v.status}
        onPress={() => router.push(('/program/family/' + encodeURIComponent(v.lead_id)) as never)} />) :
        <State title="No upcoming appointments" detail="Appointments will appear here when booked." />}</Panel>
    </> : null}
    <Notice text="Read-only pilot. To schedule, reschedule or confirm a visit, use the existing authorized SCI Live desktop workflow." />
  </Page>;
}
