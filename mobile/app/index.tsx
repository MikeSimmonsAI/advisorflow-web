/**
 * Where sign-in lands.
 *
 * The destination comes from the SERVER's `default_context`, resolved in
 * `src/experience/resolve.ts`. This screen renders nothing of its own — it
 * waits for the experience to settle and redirects.
 *
 * The empty case is not an error page. A person with no memberships is a real,
 * valid account that an operator has not finished setting up, and telling them
 * "access denied" would be both wrong and unhelpful.
 */

import React from 'react';
import { Redirect } from 'expo-router';

import { useExperience } from '../src/experience/ExperienceContext';
import { EmptyState, Loading, Screen, Button } from '../src/components/ui';
import { useAuth } from '../src/auth/AuthContext';

export default function Index() {
  const { active, experiences } = useExperience();
  const { signOut } = useAuth();

  if (active) return <Redirect href={active.route as never} />;

  if (!experiences.length) {
    return (
      <Screen>
        <EmptyState
          title="Nothing assigned yet"
          body={'Your account is active but it has not been given a sales, workspace or executive context yet. Whoever set up your access can add one.'}
        />
        <Button label="Sign out" variant="secondary" onPress={() => void signOut()} />
      </Screen>
    );
  }

  return <Screen><Loading label="Opening your workspace" /></Screen>;
}
