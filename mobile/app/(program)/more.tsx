import React from 'react';
import { router } from 'expo-router';
import { useExperience } from '../../src/experience/ExperienceContext';
import { useAuth } from '../../src/auth/AuthContext';
import { Page, Heading, Section, Panel, Line, Action, Notice } from '../../src/program/surfaces';

export default function ProgramMore() {
  const { active, experiences } = useExperience();
  const { signOut } = useAuth();
  return <Page>
    <Heading title="Program information" subtitle="Your authorized location outreach experience." />
    <Section title="Current workspace" />
    <Panel>
      <Line title={active?.label || 'Outreach program'} detail="Program information, appointments, and conversations" />
      <Line title="Pilot access" detail="Read-only; no automatic texts or bookings are triggered by opening this app" />
      <Line title="Refresh" detail="The overview and inbox refresh about once a minute while open" />
    </Panel>
    {experiences.length > 1 ? <Action label="Switch authorized experience"
      onPress={() => router.push('/switch' as never)} /> : null}
    <Section title="Your account" />
    <Action label="Signed-in devices" onPress={() => router.push('/sessions' as never)} />
    <Action label="Sign out" onPress={() => { void signOut(); }} />
    <Notice text="This is an independently scoped program experience inside the reusable EvoSys Pro app. You will not see Wholesale, Atlantis or another business unless your account is separately authorized." />
  </Page>;
}
