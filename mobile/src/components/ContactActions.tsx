/**
 * Call / Text / Email, in one place.
 *
 * NO SCREEN IMPORTS `Linking` TO DIAL A NUMBER. Everything goes through
 * `src/comms/communications.ts`, which is the seam the missing brand-sales
 * communications backend lives behind (Phase 0, GAP-4). When that backend
 * exists, this component keeps its props and its buttons and starts producing a
 * logged conversation instead of a device handoff.
 *
 * The disclosure line under the buttons is not boilerplate. A rep who believes
 * a text was logged when it was not will stop writing notes, and the platform
 * loses the record entirely — which is a worse outcome than the gap itself.
 */

import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Button } from './ui';
import { contactDisclosure, contactProspect } from '../comms/communications';
import { palette, space, type as typography } from '../theme/tokens';

export function ContactActions({
  phone, email, opportunityId, personLabel, onLogged,
}: {
  phone?: string | null;
  email?: string | null;
  opportunityId?: string | null;
  personLabel?: string | null;
  onLogged?: () => void;
}) {
  const [status, setStatus] = useState<string | null>(null);

  async function go(channel: 'call' | 'text' | 'email', value?: string | null) {
    if (!value) return;
    const result = await contactProspect({
      channel, value, opportunityId, personLabel,
    });
    if (!result.opened) { setStatus(result.reason ?? 'Could not open that.'); return; }
    if (result.logged) { setStatus('Recorded against this deal.'); onLogged?.(); return; }
    setStatus(result.reason ?? null);
  }

  const nothing = !phone && !email;

  return (
    <View style={styles.wrap}>
      <View style={styles.row}>
        <Button
          label="Call"
          variant="secondary"
          disabled={!phone}
          onPress={() => void go('call', phone)}
          style={styles.button}
        />
        <Button
          label="Text"
          variant="secondary"
          disabled={!phone}
          onPress={() => void go('text', phone)}
          style={styles.button}
        />
        <Button
          label="Email"
          variant="secondary"
          disabled={!email}
          onPress={() => void go('email', email)}
          style={styles.button}
        />
      </View>

      {nothing ? (
        <Text style={styles.note}>No phone number or email address on this record.</Text>
      ) : (
        <Text style={styles.note}>{contactDisclosure(!!opportunityId)}</Text>
      )}

      {status ? <Text style={styles.status}>{status}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: space.sm },
  row: { flexDirection: 'row', gap: space.sm },
  button: { flex: 1, paddingHorizontal: space.sm },
  note: { ...typography.caption, color: palette.textFaint },
  status: { ...typography.caption, color: palette.accentSoft },
});
