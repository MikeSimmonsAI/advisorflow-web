/**
 * OPPORTUNITY DETAIL — the deal, and the four things you can do to it from a
 * phone: contact the person, move the stage, write a note, start a proposal.
 *
 * STAGE MOVES GO THROUGH THE SAME PATCH THE DESKTOP USES, with the same
 * vocabulary (`OPPORTUNITY_STAGES`). There is no mobile "quick win" button and
 * no second state machine — `won` here means exactly what `won` means
 * everywhere else, including to the compensation ledger that reads it.
 *
 * A NOTE IS NEVER LOST. If the note write fails for any reason other than a
 * refusal, it goes into the outbox and the screen says so. A rep types a note
 * standing in a basement; losing it because the cell dropped is the failure
 * this whole app exists to avoid.
 *
 * The id in the URL is untrusted — it can arrive from a deep link, a push
 * payload or a typed address. `GET /sales/opportunities/{id}` re-checks
 * ownership through `assert_can_view_opportunity` regardless, so an id for
 * somebody else's deal ends in a refusal that this screen renders as one.
 */

import React, { useCallback, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, TextInput } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { proposals as proposalsApi, sales } from '../../src/api/endpoints';
import { asList, asMoney, useScopedQuery, useScopedRefresh } from '../../src/hooks/useApi';
import {
  Button, Card, Divider, ErrorState, KeyValue, Loading, Pill, Row, Screen,
  ScreenTitle, SectionHeader,
} from '../../src/components/ui';
import { ContactActions } from '../../src/components/ContactActions';
import { outbox } from '../../src/offline/queue';
import { nameOf, relativeOf, whenOf } from '../../src/format';
import { OPPORTUNITY_STAGES, stageLabel } from '../../src/vocab';
import { palette, radius, space, type as typography, HIT_SIZE } from '../../src/theme/tokens';
import type { Opportunity, Proposal } from '../../src/api/types';

export default function OpportunityDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const refresh = useScopedRefresh();
  const client = useQueryClient();

  const [note, setNote] = useState('');
  const [savingNote, setSavingNote] = useState(false);
  const [noteStatus, setNoteStatus] = useState<string | null>(null);
  const [stageBusy, setStageBusy] = useState(false);

  const opp = useScopedQuery(['opportunity', id], () => sales.opportunity(String(id)),
    { enabled: !!id });

  const props = useScopedQuery(['opportunity', id, 'proposals'],
    () => proposalsApi.forOpportunity(String(id)), { enabled: !!id });

  const saveNote = useCallback(async () => {
    const text = note.trim();
    if (!text || !id) return;
    setSavingNote(true);
    setNoteStatus(null);
    try {
      await sales.addNote(String(id), { body: text, note: text });
      setNote('');
      setNoteStatus('Saved.');
      void client.invalidateQueries({ queryKey: ['opportunity', id] });
      refresh();
    } catch (err) {
      const status = (err as { status?: number })?.status ?? 0;
      if (status >= 400 && status < 500 && status !== 429) {
        setNoteStatus((err as { detail?: string })?.detail ?? 'The server refused that note.');
      } else {
        // Queued, visible, and retried. Never silently dropped.
        await outbox.enqueue({
          kind: 'note',
          label: `Note on ${nameOf(opp.data as Record<string, unknown>, 'this deal')}`,
          targetId: String(id),
          payload: { body: text, note: text },
        });
        setNote('');
        setNoteStatus('No signal — saved on this phone and will send when you are back online.');
      }
    } finally {
      setSavingNote(false);
    }
  }, [note, id, client, refresh, opp.data]);

  const changeStage = useCallback((stage: string) => {
    if (!id) return;
    const apply = async () => {
      setStageBusy(true);
      try {
        await sales.setStage(String(id), stage);
        void client.invalidateQueries({ queryKey: ['opportunity', id] });
        refresh();
      } catch (err) {
        Alert.alert('Could not change the stage',
          (err as { detail?: string })?.detail ?? 'Try again when you have signal.');
      } finally {
        setStageBusy(false);
      }
    };
    // Won and lost are the two that echo outward — into compensation, into
    // provisioning, into the manager's pipeline. They get a confirmation; the
    // ordinary forward moves do not, because a rep updating a stage between
    // meetings should not have to tap twice.
    if (stage === 'won' || stage === 'lost') {
      Alert.alert(
        stage === 'won' ? 'Mark this deal won?' : 'Mark this deal lost?',
        stage === 'won'
          ? 'This starts implementation and makes the deal eligible for commission.'
          : 'This closes the deal. You can reopen it from a computer.',
        [{ text: 'Cancel', style: 'cancel' }, { text: 'Yes', onPress: () => void apply() }],
      );
      return;
    }
    void apply();
  }, [id, client, refresh]);

  if (opp.isLoading) return <Screen><Loading label="Loading deal" /></Screen>;
  if (opp.isError) return <Screen><ErrorState error={opp.error} onRetry={refresh} /></Screen>;

  const o = (opp.data ?? {}) as Opportunity;
  const proposalRows = asList<Proposal>(props.data, 'proposals', 'items');

  return (
    <Screen refreshing={opp.isFetching} onRefresh={refresh}>
      <ScreenTitle title={nameOf(o, 'Opportunity')} subtitle={stageLabel(o.stage)} />

      <Card>
        <KeyValue label="Value" value={o.amount != null ? asMoney(o.amount) : '—'} />
        <KeyValue label="Owner" value={String(o.owner_name ?? '—')} />
        <KeyValue
          label="Expected close"
          value={o.expected_close_date ? whenOf(o.expected_close_date) : '—'}
        />
        {o.next_step ? <KeyValue label="Next step" value={String(o.next_step)} /> : null}
        <Divider />
        <ContactActions
          phone={o.contact_phone as string | undefined}
          email={o.contact_email as string | undefined}
          opportunityId={String(id)}
          personLabel={o.contact_name as string | undefined}
          onLogged={refresh}
        />
      </Card>

      <SectionHeader title="Stage" />
      <ScrollView horizontal showsHorizontalScrollIndicator={false}
                  contentContainerStyle={styles.stages}>
        {OPPORTUNITY_STAGES.map((s) => {
          const on = o.stage === s;
          return (
            <Button
              key={s}
              label={stageLabel(s)}
              variant={on ? 'primary' : 'secondary'}
              disabled={stageBusy || on}
              onPress={() => changeStage(s)}
              style={styles.stageButton}
            />
          );
        })}
      </ScrollView>

      <SectionHeader title="Add a note" />
      <Card>
        <TextInput
          value={note}
          onChangeText={setNote}
          placeholder="What happened, and what is next?"
          placeholderTextColor={palette.textFaint}
          multiline
          style={styles.input}
          accessibilityLabel="Note"
        />
        <Button
          label="Save note"
          onPress={() => void saveNote()}
          disabled={!note.trim()}
          loading={savingNote}
        />
        {noteStatus ? <Text style={styles.status}>{noteStatus}</Text> : null}
      </Card>

      <SectionHeader
        title={`Proposals · ${proposalRows.length}`}
        action={
          <Button
            label="New"
            variant="ghost"
            onPress={() => router.push(`/proposal/new?opportunityId=${id}` as never)}
          />
        }
      />
      {proposalRows.map((p) => (
        <Row
          key={String(p.id)}
          title={String(p.title ?? `Version ${p.version ?? 1}`)}
          subtitle={String(p.status ?? '')}
          meta={p.sent_at ? `Sent ${relativeOf(p.sent_at)}`
            : p.published_at ? `Published ${relativeOf(p.published_at)}` : 'Draft'}
          onPress={() => router.push(`/proposal/${p.id}` as never)}
          right={p.total != null ? <Pill label={asMoney(p.total)} tone="accent" /> : undefined}
        />
      ))}
      {!proposalRows.length ? (
        <Text style={styles.note}>No proposal yet on this deal.</Text>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  stages: { gap: space.sm, paddingRight: space.lg },
  stageButton: { paddingHorizontal: space.lg },
  input: {
    minHeight: HIT_SIZE * 2,
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    padding: space.lg,
    color: palette.text,
    ...typography.body,
    textAlignVertical: 'top',
  },
  status: { ...typography.caption, color: palette.accentSoft },
  note: { ...typography.caption, color: palette.textFaint },
});
