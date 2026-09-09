/**
 * PROPOSAL BUILDER — three steps, one decision each.
 *
 *   which deal → which package → price and review
 *
 * It is a guided wizard on purpose. The desktop pricing grid is a real tool for
 * a real job and it does not survive being shrunk: on a phone it becomes a
 * spreadsheet with tap targets the width of a pencil. What a rep needs in a car
 * park is to put a known package in front of a customer at a known price.
 *
 * THE FLOOR IS THE SERVER'S. The package carries `floor_price` and this screen
 * compares against it only to WARN — the authority is
 * `pricing_policy_models.resolve_policy`, which runs again on create and again
 * on send. A rep who types a number below the floor gets a proposal marked
 * below-floor and a Send button that routes to an approval request instead.
 * Nothing here decides that; it only says it early.
 */

import React, { useMemo, useState } from 'react';
import { Alert, StyleSheet, Text, TextInput } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';

import { proposals, sales } from '../../src/api/endpoints';
import { asList, asMoney, useScopedQuery } from '../../src/hooks/useApi';
import { useActiveExperience } from '../../src/experience/ExperienceContext';
import {
  Button, Card, ErrorState, KeyValue, Loading, Pill, Row, Screen, ScreenTitle,
  SectionHeader,
} from '../../src/components/ui';
import { nameOf } from '../../src/format';
import { stageLabel } from '../../src/vocab';
import { palette, radius, space, type as typography, HIT_SIZE } from '../../src/theme/tokens';
import type { Opportunity, SalesPackage } from '../../src/api/types';

export default function NewProposal() {
  const params = useLocalSearchParams<{ opportunityId?: string }>();
  const exp = useActiveExperience();
  const brand = exp.brandSalesOrgId;

  const [opportunityId, setOpportunityId] = useState<string | null>(
    params.opportunityId ? String(params.opportunityId) : null);
  const [packageId, setPackageId] = useState<string | null>(null);
  const [price, setPrice] = useState('');
  const [busy, setBusy] = useState(false);

  const opps = useScopedQuery(['opportunities', brand, 'for-proposal'],
    () => sales.opportunities({ brand_sales_org_id: brand }),
    { enabled: !opportunityId });

  const pkgs = useScopedQuery(['packages', brand], () => sales.packages(brand));

  const oppRows = asList<Opportunity>(opps.data, 'opportunities', 'items');
  const pkgRows = asList<SalesPackage>(pkgs.data, 'packages', 'items');
  const chosenPackage = pkgRows.find((p) => String(p.id) === packageId) ?? null;

  const numericPrice = useMemo(() => {
    const n = Number(price.replace(/[^0-9.]/g, ''));
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [price]);

  const floor = chosenPackage?.floor_price ?? null;
  const belowFloor = floor != null && numericPrice != null && numericPrice < Number(floor);

  async function create() {
    if (!opportunityId || !packageId) return;
    setBusy(true);
    try {
      const created = await proposals.create({
        opportunity_id: opportunityId,
        package_id: packageId,
        // Sent only when the rep actually typed one. An empty box must not be
        // read as "zero", which is a discount of one hundred percent.
        ...(numericPrice != null ? { total: numericPrice, price: numericPrice } : {}),
      });
      const newId = (created as { id?: string })?.id;
      if (newId) router.replace(`/proposal/${newId}` as never);
      else router.back();
    } catch (err) {
      Alert.alert('Could not create the proposal',
        (err as { detail?: string })?.detail ?? 'Try again when you have signal.');
    } finally {
      setBusy(false);
    }
  }

  // ── step 1: which deal ────────────────────────────────────────────────────
  if (!opportunityId) {
    if (opps.isLoading) return <Screen><Loading label="Loading your deals" /></Screen>;
    if (opps.isError) {
      return <Screen><ErrorState error={opps.error} onRetry={() => void opps.refetch()} /></Screen>;
    }
    return (
      <Screen>
        <ScreenTitle title="New proposal" subtitle="Step 1 of 3 · Which deal?" />
        {oppRows.map((o) => (
          <Row
            key={String(o.id)}
            title={nameOf(o, 'Opportunity')}
            subtitle={stageLabel(o.stage)}
            onPress={() => setOpportunityId(String(o.id))}
          />
        ))}
      </Screen>
    );
  }

  // ── step 2: which package ─────────────────────────────────────────────────
  if (!packageId) {
    if (pkgs.isLoading) return <Screen><Loading label="Loading packages" /></Screen>;
    if (pkgs.isError) {
      return <Screen><ErrorState error={pkgs.error} onRetry={() => void pkgs.refetch()} /></Screen>;
    }
    return (
      <Screen>
        <ScreenTitle title="New proposal" subtitle="Step 2 of 3 · Which package?" />
        {pkgRows.map((p) => (
          <Row
            key={String(p.id)}
            title={String(p.name ?? 'Package')}
            subtitle={typeof p.description === 'string' ? p.description : null}
            onPress={() => {
              setPackageId(String(p.id));
              if (p.price != null) setPrice(String(p.price));
            }}
            right={p.price != null ? <Pill label={asMoney(p.price)} tone="accent" /> : undefined}
          />
        ))}
        {!pkgRows.length ? (
          <Text style={styles.note}>
            No packages are configured for this brand yet.
          </Text>
        ) : null}
      </Screen>
    );
  }

  // ── step 3: price and review ──────────────────────────────────────────────
  return (
    <Screen>
      <ScreenTitle title="New proposal" subtitle="Step 3 of 3 · Price and review" />

      <Card>
        <KeyValue label="Package" value={String(chosenPackage?.name ?? '—')} />
        <KeyValue label="List price" value={chosenPackage?.price != null
          ? asMoney(chosenPackage.price) : '—'} />
        {floor != null ? <KeyValue label="Your floor" value={asMoney(floor)} /> : null}
      </Card>

      <SectionHeader title="Price" />
      <Card accent={belowFloor ? palette.warning : undefined}>
        <TextInput
          value={price}
          onChangeText={setPrice}
          placeholder="Amount"
          placeholderTextColor={palette.textFaint}
          keyboardType="decimal-pad"
          inputMode="decimal"
          style={styles.input}
          accessibilityLabel="Proposal price"
        />
        {belowFloor ? (
          <Text style={styles.warn}>
            That is below your floor of {asMoney(floor)}. You can still create
            this proposal — but it will need a manager's approval before it can
            be sent, and the app will ask for that instead of sending.
          </Text>
        ) : (
          <Text style={styles.note}>
            Leave this as it is to use the package price.
          </Text>
        )}
      </Card>

      <Button
        label="Create proposal"
        loading={busy}
        onPress={() => void create()}
      />
      <Button
        label="Change package"
        variant="ghost"
        onPress={() => setPackageId(null)}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: {
    minHeight: HIT_SIZE,
    backgroundColor: palette.surfaceSunken,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    paddingHorizontal: space.lg,
    color: palette.text,
    ...typography.body,
  },
  note: { ...typography.caption, color: palette.textFaint },
  warn: { ...typography.caption, color: palette.warning },
});
