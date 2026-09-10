/**
 * The operational components — metrics that cannot lie, and the three shapes
 * every operating screen is made of: a number, a thing to do, a thing that
 * happened.
 *
 * WHY A METRIC IS ITS OWN COMPONENT. Every metric on this platform has six
 * possible states (see api/state.ts) and only two of them are numbers. Left to
 * each screen, five of the six get forgotten and the sixth — a value — is
 * rendered for all of them. One component, one decision, everywhere.
 *
 * A METRIC THAT CANNOT BE ACTED ON DOES NOT BELONG ON A PHONE. `onPress` is
 * therefore first-class rather than an afterthought: a tile that goes nowhere
 * is a poster, and this app already had a screenful of posters.
 */

import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { palette, radius, space, type as typography, HIT_SIZE } from '../theme/tokens';
import type { Field, FieldState } from '../api/state';
import { stateLabel } from '../api/state';
import { Button, Pill, type PillTone } from './ui';

// ── metrics ─────────────────────────────────────────────────────────────────

function toneForState(state: FieldState): string {
  switch (state) {
    case 'ready': return palette.text;
    case 'zero': return palette.textMuted;
    case 'error': return palette.danger;
    default: return palette.textFaint;
  }
}

export function Metric({
  label, field, format, onPress, hint, emphasis,
}: {
  label: string;
  field: Field<number | string>;
  /** How a READY value is written. Never called for a non-value state. */
  format?: (value: number | string) => string;
  onPress?: () => void;
  hint?: string;
  emphasis?: boolean;
}) {
  const isValue = field.state === 'ready' || field.state === 'zero';
  const shown = isValue
    ? (format ? format(field.value as number | string) : String(field.value))
    : field.state === 'loading' ? '·  ·  ·' : stateLabel(field.state);

  const body = (
    <View style={[styles.metric, emphasis && styles.metricWide]}>
      <Text style={styles.metricLabel} numberOfLines={2}>{label.toUpperCase()}</Text>
      <Text
        style={[
          styles.metricValue,
          { color: toneForState(field.state) },
          !isValue && field.state !== 'loading' && styles.metricValueWord,
          emphasis && styles.metricValueBig,
        ]}
        numberOfLines={1}
        adjustsFontSizeToFit
      >
        {shown}
      </Text>
      {/* THE REASON TRAVELS WITH THE STATE. "Unavailable" on its own is the
          thing Mike called out; it says something is wrong without saying
          what, and leaves nothing to do about it. */}
      {!isValue && field.reason ? (
        <Text style={styles.metricReason} numberOfLines={2}>{field.reason}</Text>
      ) : hint ? (
        <Text style={styles.metricHint} numberOfLines={2}>{hint}</Text>
      ) : null}
    </View>
  );

  if (!onPress || !isValue) return body;
  return (
    <Pressable onPress={onPress} accessibilityRole="button"
               style={({ pressed }) => [pressed && { opacity: 0.7 }]}>
      {body}
    </Pressable>
  );
}

export function MetricGrid({ children }: { children: React.ReactNode }) {
  return <View style={styles.grid}>{children}</View>;
}

/**
 * One retry for a whole section.
 *
 * Shown only when something actually failed — an `unavailable` field is not
 * fixed by asking again, so offering the button there would be theatre.
 */
export function SectionRetry({ show, onRetry, note }: {
  show: boolean; onRetry: () => void; note?: string;
}) {
  if (!show) return null;
  return (
    <View style={styles.retry}>
      <Text style={styles.retryText}>
        {note ?? 'Some of this could not be loaded.'}
      </Text>
      <Button label="Retry" variant="secondary" onPress={onRetry} />
    </View>
  );
}

// ── the two operational row shapes ──────────────────────────────────────────

/**
 * SOMETHING TO DO. Always carries a verb and always goes somewhere. If a screen
 * cannot say what tapping it does, it is activity, not attention.
 */
export function AttentionItem({
  title, why, tone = 'warning', action, onPress, count,
}: {
  title: string;
  why?: string | null;
  tone?: PillTone;
  action?: string;
  onPress?: () => void;
  count?: number;
}) {
  const accent = tone === 'danger' ? palette.danger
    : tone === 'positive' ? palette.positive : palette.warning;
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      accessibilityRole={onPress ? 'button' : undefined}
      style={({ pressed }) => [styles.attention, { borderLeftColor: accent },
                               pressed && onPress ? { opacity: 0.7 } : null]}
    >
      <View style={styles.attentionMain}>
        <Text style={styles.attentionTitle} numberOfLines={2}>{title}</Text>
        {why ? <Text style={styles.attentionWhy} numberOfLines={3}>{why}</Text> : null}
        {action ? <Text style={[styles.attentionAction, { color: accent }]}>{action}</Text> : null}
      </View>
      {count !== undefined ? (
        <View style={styles.attentionCount}>
          <Text style={[styles.attentionCountText, { color: accent }]}>{count}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

/** SOMETHING THAT HAPPENED. No verb, no destination required. */
export function ActivityItem({
  title, detail, when, tone,
}: {
  title: string; detail?: string | null; when?: string | null; tone?: PillTone;
}) {
  return (
    <View style={styles.activity}>
      <View style={styles.activityDot} />
      <View style={styles.activityMain}>
        <Text style={styles.activityTitle} numberOfLines={2}>{title}</Text>
        {detail ? <Text style={styles.activityDetail} numberOfLines={2}>{detail}</Text> : null}
      </View>
      {when ? <Text style={styles.activityWhen} numberOfLines={1}>{when}</Text> : null}
      {tone ? <Pill label="" tone={tone} /> : null}
    </View>
  );
}

// ── filters ─────────────────────────────────────────────────────────────────

export function FilterChips<T extends string>({
  options, value, onChange,
}: {
  options: Array<{ key: T; label: string; count?: number }>;
  value: T;
  onChange: (key: T) => void;
}) {
  return (
    <View style={styles.chips}>
      {options.map((o) => {
        const on = o.key === value;
        return (
          <Pressable
            key={o.key}
            onPress={() => onChange(o.key)}
            accessibilityRole="button"
            accessibilityState={{ selected: on }}
            style={({ pressed }) => [
              styles.chip, on && styles.chipOn, pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={[styles.chipText, on && styles.chipTextOn]}>
              {o.label}{o.count !== undefined ? ` ${o.count}` : ''}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

// ── skeleton ────────────────────────────────────────────────────────────────

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <View style={{ gap: space.sm }}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={[styles.skeleton, { opacity: 1 - i * 0.18 }]} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: space.sm },
  metric: {
    flexGrow: 1, flexBasis: '47%', minWidth: 140,
    backgroundColor: palette.surface,
    borderRadius: radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border,
    paddingVertical: space.md, paddingHorizontal: space.lg,
    gap: 4, minHeight: 84, justifyContent: 'center',
  },
  metricWide: { flexBasis: '100%' },
  metricLabel: { ...typography.micro, color: palette.textFaint },
  metricValue: { ...typography.display, fontSize: 26, lineHeight: 30 },
  metricValueBig: { fontSize: 34, lineHeight: 38 },
  metricValueWord: { ...typography.body, fontSize: 15, lineHeight: 20, fontStyle: 'italic' },
  metricReason: { ...typography.caption, color: palette.textFaint },
  metricHint: { ...typography.caption, color: palette.textMuted },

  retry: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: space.md, backgroundColor: 'rgba(255,92,108,0.08)',
    borderRadius: radius.md, padding: space.md,
    borderWidth: StyleSheet.hairlineWidth, borderColor: palette.danger,
  },
  retryText: { ...typography.caption, color: palette.text, flex: 1 },

  attention: {
    flexDirection: 'row', alignItems: 'center', gap: space.md,
    minHeight: HIT_SIZE + 12,
    backgroundColor: palette.surface,
    borderRadius: radius.md, borderLeftWidth: 3,
    borderWidth: StyleSheet.hairlineWidth, borderColor: palette.border,
    paddingVertical: space.md, paddingHorizontal: space.lg,
  },
  attentionMain: { flex: 1, gap: 3 },
  attentionTitle: { ...typography.bodyStrong, color: palette.text },
  attentionWhy: { ...typography.caption, color: palette.textMuted },
  attentionAction: { ...typography.micro, marginTop: 2 },
  attentionCount: { minWidth: 34, alignItems: 'flex-end' },
  attentionCountText: { ...typography.display, fontSize: 22, lineHeight: 26 },

  activity: {
    flexDirection: 'row', alignItems: 'flex-start', gap: space.md,
    paddingVertical: space.sm, paddingHorizontal: space.xs,
  },
  activityDot: {
    width: 6, height: 6, borderRadius: 3, marginTop: 7,
    backgroundColor: palette.accentSoft,
  },
  activityMain: { flex: 1, gap: 2 },
  activityTitle: { ...typography.body, color: palette.text },
  activityDetail: { ...typography.caption, color: palette.textMuted },
  activityWhen: { ...typography.caption, color: palette.textFaint },

  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.xs },
  chip: {
    paddingHorizontal: space.md, paddingVertical: 8,
    borderRadius: radius.pill, borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.border, backgroundColor: palette.surface,
    minHeight: 36, justifyContent: 'center',
  },
  chipOn: { backgroundColor: palette.accent, borderColor: palette.accent },
  chipText: { ...typography.caption, color: palette.textMuted },
  chipTextOn: { color: '#ffffff', fontWeight: '700' },

  skeleton: {
    height: 64, borderRadius: radius.md,
    backgroundColor: palette.surfaceRaised,
    borderWidth: StyleSheet.hairlineWidth, borderColor: palette.border,
  },
});
