/**
 * The primitives every screen is built from.
 *
 * Small on purpose. A field app needs about eight components and a consistent
 * hand for them; a component library of forty is how two screens end up looking
 * like two products. Everything here obeys three rules:
 *
 *   - MINIMUM 48pt TAP TARGETS. A missed tap standing in a car park costs more
 *     than a few points of density.
 *   - NO BLACK-ON-BLACK. Every surface is a real step off the ground colour and
 *     every text colour is one of exactly three from the ramp.
 *   - EMPTY SAYS WHY. An empty list is a sentence, never a blank rectangle —
 *     "no appointments today" and "we could not reach the server" look nothing
 *     alike to a person and must not look alike on screen.
 */

import React from 'react';
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View,
  ViewStyle, TextStyle, RefreshControl,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { palette, radius, space, type as typography, HIT_SIZE, shadow } from '../theme/tokens';
import { normaliseSeverity, SEVERITY_LABELS, type Severity } from '../vocab';

// ── screen scaffold ─────────────────────────────────────────────────────────

export function Screen({
  children, scroll = true, refreshing, onRefresh, contentStyle,
}: {
  children: React.ReactNode;
  scroll?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
  contentStyle?: ViewStyle;
}) {
  const body = scroll ? (
    <ScrollView
      contentContainerStyle={[styles.scrollBody, contentStyle]}
      keyboardShouldPersistTaps="handled"
      refreshControl={
        onRefresh ? (
          <RefreshControl
            refreshing={!!refreshing}
            onRefresh={onRefresh}
            tintColor={palette.accentSoft}
          />
        ) : undefined
      }
    >
      {children}
    </ScrollView>
  ) : (
    <View style={[styles.scrollBody, contentStyle]}>{children}</View>
  );

  return <SafeAreaView style={styles.screen} edges={['top']}>{body}</SafeAreaView>;
}

export function ScreenTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <View style={styles.screenTitle}>
      <Text style={styles.titleText}>{title}</Text>
      {subtitle ? <Text style={styles.subtitleText}>{subtitle}</Text> : null}
    </View>
  );
}

export function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle}>{title.toUpperCase()}</Text>
      {action}
    </View>
  );
}

// ── surfaces ────────────────────────────────────────────────────────────────

export function Card({
  children, onPress, style, accent,
}: {
  children: React.ReactNode;
  onPress?: () => void;
  style?: ViewStyle;
  /** A left rule in a status colour. Used sparingly — every card being urgent
   *  is the same as no card being urgent. */
  accent?: string;
}) {
  const content = (
    <View style={[styles.card, accent ? { borderLeftWidth: 3, borderLeftColor: accent } : null, style]}>
      {children}
    </View>
  );
  if (!onPress) return content;
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [pressed && styles.pressed]}
      accessibilityRole="button"
    >
      {content}
    </Pressable>
  );
}

export function Row({
  title, subtitle, meta, onPress, right, accent,
}: {
  title: string;
  subtitle?: string | null;
  meta?: string | null;
  onPress?: () => void;
  right?: React.ReactNode;
  accent?: string;
}) {
  const body = (
    <View style={[styles.row, accent ? { borderLeftWidth: 3, borderLeftColor: accent } : null]}>
      <View style={styles.rowMain}>
        <Text style={styles.rowTitle} numberOfLines={1}>{title}</Text>
        {subtitle ? (
          <Text style={styles.rowSubtitle} numberOfLines={2}>{subtitle}</Text>
        ) : null}
        {meta ? <Text style={styles.rowMeta} numberOfLines={1}>{meta}</Text> : null}
      </View>
      {right ? <View style={styles.rowRight}>{right}</View> : null}
    </View>
  );
  if (!onPress) return body;
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [pressed && styles.pressed]}
      accessibilityRole="button"
    >
      {body}
    </Pressable>
  );
}

// ── chips and status ────────────────────────────────────────────────────────

export function Pill({ label, tone = 'neutral' }: { label: string; tone?: PillTone }) {
  return (
    <View style={[styles.pill, { backgroundColor: pillBg(tone), borderColor: pillFg(tone) }]}>
      <Text style={[styles.pillText, { color: pillFg(tone) }]} numberOfLines={1}>{label}</Text>
    </View>
  );
}

export type PillTone = 'neutral' | 'accent' | 'positive' | 'warning' | 'danger';

function pillFg(tone: PillTone): string {
  switch (tone) {
    case 'accent': return palette.accentSoft;
    case 'positive': return palette.positive;
    case 'warning': return palette.warning;
    case 'danger': return palette.danger;
    default: return palette.textMuted;
  }
}

function pillBg(tone: PillTone): string {
  switch (tone) {
    case 'accent': return 'rgba(34,163,255,0.12)';
    case 'positive': return 'rgba(25,214,124,0.12)';
    case 'warning': return 'rgba(245,166,35,0.12)';
    case 'danger': return 'rgba(255,92,108,0.12)';
    default: return 'rgba(155,176,204,0.10)';
  }
}

/**
 * Severity, rendered from the server's own vocabulary.
 *
 * An unknown value normalises to `unavailable`, NEVER to healthy — a screen
 * that could not reach the truth must not draw a green tick.
 */
export function SeverityPill({ value }: { value: string | null | undefined }) {
  const sev: Severity = normaliseSeverity(value);
  const tone: PillTone =
    sev === 'healthy' ? 'positive'
      : sev === 'attention' ? 'warning'
        : sev === 'action_required' ? 'danger'
          : 'neutral';
  return <Pill label={SEVERITY_LABELS[sev]} tone={tone} />;
}

// ── actions ─────────────────────────────────────────────────────────────────

export function Button({
  label, onPress, variant = 'primary', disabled, loading, style,
}: {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
}) {
  const isDisabled = disabled || loading;
  return (
    <Pressable
      onPress={onPress}
      disabled={isDisabled}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!isDisabled, busy: !!loading }}
      style={({ pressed }) => [
        styles.button,
        variant === 'primary' && styles.buttonPrimary,
        variant === 'secondary' && styles.buttonSecondary,
        variant === 'ghost' && styles.buttonGhost,
        variant === 'danger' && styles.buttonDanger,
        isDisabled && styles.buttonDisabled,
        pressed && !isDisabled && styles.pressed,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={variant === 'primary' ? '#fff' : palette.accentSoft} />
      ) : (
        <Text
          style={[
            styles.buttonText,
            variant === 'primary' && { color: '#ffffff' },
            variant === 'secondary' && { color: palette.text },
            variant === 'ghost' && { color: palette.accentSoft },
            variant === 'danger' && { color: palette.danger },
          ]}
        >
          {label}
        </Text>
      )}
    </Pressable>
  );
}

// ── states ──────────────────────────────────────────────────────────────────

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <View style={styles.state}>
      <ActivityIndicator color={palette.accentSoft} />
      <Text style={styles.stateText}>{label}</Text>
    </View>
  );
}

export function EmptyState({ title, body }: { title: string; body?: string }) {
  return (
    <View style={styles.state}>
      <Text style={styles.stateTitle}>{title}</Text>
      {body ? <Text style={styles.stateText}>{body}</Text> : null}
    </View>
  );
}

/**
 * A failure the person can act on.
 *
 * The two cases are kept visibly apart because they call for different
 * behaviour: `offline` is worth retrying and nothing is wrong with the request,
 * while a refusal is the server's answer and retrying it is noise. Collapsing
 * them into "Something went wrong" is how a rep stands in a basement tapping a
 * button that was never going to work.
 */
export function ErrorState({
  error, onRetry,
}: { error: unknown; onRetry?: () => void }) {
  const offline = !!(error as { offline?: boolean })?.offline;
  const detail = (error as { detail?: string; message?: string })?.detail
    ?? (error as { message?: string })?.message
    ?? 'Something went wrong.';
  return (
    <View style={styles.state}>
      <Text style={styles.stateTitle}>
        {offline ? 'No connection' : 'Could not load this'}
      </Text>
      <Text style={styles.stateText}>{detail}</Text>
      {onRetry ? (
        <Button label={offline ? 'Try again' : 'Retry'} variant="secondary"
                onPress={onRetry} style={{ marginTop: space.lg }} />
      ) : null}
    </View>
  );
}

/**
 * "As of 14:32" — shown whenever what is on screen came from cache rather than
 * from the network. A rep making a decision on stale numbers should be able to
 * see that they are stale without guessing.
 */
export function StaleBanner({ at }: { at: number | null | undefined }) {
  if (!at) return null;
  const when = new Date(at);
  const hh = String(when.getHours()).padStart(2, '0');
  const mm = String(when.getMinutes()).padStart(2, '0');
  return (
    <View style={styles.stale}>
      <Text style={styles.staleText}>Showing data as of {hh}:{mm}</Text>
    </View>
  );
}

export function Divider() {
  return <View style={styles.divider} />;
}

export function KeyValue({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <View style={styles.kv}>
      <Text style={styles.kvLabel}>{label}</Text>
      {typeof value === 'string' || typeof value === 'number' ? (
        <Text style={styles.kvValue}>{String(value)}</Text>
      ) : (value ?? <Text style={styles.kvValue}>—</Text>)}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: palette.ground },
  scrollBody: { padding: space.lg, paddingBottom: space.xxl * 2, gap: space.md },

  screenTitle: { marginBottom: space.sm },
  titleText: { ...typography.display, color: palette.text },
  subtitleText: { ...typography.caption, color: palette.textMuted, marginTop: space.xs },

  sectionHeader: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: space.lg, marginBottom: space.xs,
  },
  sectionTitle: { ...typography.micro, color: palette.textFaint },

  card: {
    backgroundColor: palette.surface,
    borderRadius: radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    padding: space.lg,
    gap: space.sm,
    ...shadow.card,
  },

  row: {
    minHeight: HIT_SIZE + 12,
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    paddingVertical: space.md,
    paddingHorizontal: space.lg,
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.md,
  },
  rowMain: { flex: 1, gap: 2 },
  rowRight: { alignItems: 'flex-end', gap: space.xs },
  rowTitle: { ...typography.bodyStrong, color: palette.text },
  rowSubtitle: { ...typography.caption, color: palette.textMuted },
  rowMeta: { ...typography.caption, color: palette.textFaint },

  pill: {
    paddingHorizontal: space.md, paddingVertical: 5,
    borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth,
    alignSelf: 'flex-start',
  },
  pillText: { ...typography.micro },

  button: {
    minHeight: HIT_SIZE,
    borderRadius: radius.md,
    alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: space.xl,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.transparent,
  },
  buttonPrimary: { backgroundColor: palette.accent },
  buttonSecondary: { backgroundColor: palette.surfaceRaised, borderColor: palette.borderStrong },
  buttonGhost: { backgroundColor: palette.transparent },
  buttonDanger: { backgroundColor: 'rgba(255,92,108,0.12)', borderColor: palette.danger },
  buttonDisabled: { opacity: 0.45 },
  buttonText: { ...typography.bodyStrong },

  pressed: { opacity: 0.7 },

  state: { alignItems: 'center', justifyContent: 'center', paddingVertical: space.xxl, gap: space.sm },
  stateTitle: { ...typography.heading, color: palette.text, textAlign: 'center' },
  stateText: { ...typography.caption, color: palette.textMuted, textAlign: 'center' },

  stale: {
    backgroundColor: 'rgba(245,166,35,0.10)',
    borderRadius: radius.sm, paddingVertical: space.sm, paddingHorizontal: space.md,
  },
  staleText: { ...typography.caption, color: palette.warning },

  divider: { height: StyleSheet.hairlineWidth, backgroundColor: palette.border, marginVertical: space.sm },

  kv: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space.md, paddingVertical: 6 },
  kvLabel: { ...typography.caption, color: palette.textMuted },
  kvValue: { ...typography.body, color: palette.text, flexShrink: 1, textAlign: 'right' },
});

export const ui = { styles };
