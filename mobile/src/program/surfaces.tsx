/** SCI program-only visual language. Lightweight, bright and readable.
 * Kept separate from other brands so this pilot doesn't repaint EvoSys Pro.
 */
import React from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, StyleSheet, Text, View, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

export const ink = '#19334D';
export const muted = '#60758A';
export const blue = '#1265A8';
export const canvas = '#F5F8FC';
const white = '#FFFFFF';

export function Page({ children, refresh, refreshing }: {
  children: React.ReactNode; refresh?: () => void; refreshing?: boolean;
}) {
  return <SafeAreaView edges={['top']} style={{ flex: 1, backgroundColor: canvas }}>
    <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled"
      refreshControl={refresh ? <RefreshControl refreshing={!!refreshing} onRefresh={refresh} /> : undefined}>
      {children}
    </ScrollView>
  </SafeAreaView>;
}
export function Heading({ title, subtitle }: { title: string; subtitle?: string }) {
  return <View style={{ gap: 4, marginBottom: 6 }}>
    <Text style={styles.eyebrow}>SCI PROGRAM • MOBILE PREVIEW</Text>
    <Text style={styles.heading}>{title}</Text>
    {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
  </View>;
}
export function Panel({ children }: { children: React.ReactNode }) {
  return <View style={styles.panel}>{children}</View>;
}
export function Section({ title, hint }: { title: string; hint?: string }) {
  return <View style={styles.section}><Text style={styles.sectionTitle}>{title}</Text>
    {hint ? <Text style={styles.small}>{hint}</Text> : null}</View>;
}
export function Metric({ value, label, tone }: { value: number | null; label: string; tone?: 'strong' | 'normal' }) {
  return <View style={[styles.metric, tone === 'strong' && styles.metricStrong]}>
    <Text style={[styles.metricNumber, tone === 'strong' && { color: white }]}>{value ?? '—'}</Text>
    <Text style={[styles.metricLabel, tone === 'strong' && { color: '#E3F0FF' }]}>{label}</Text>
  </View>;
}
export function Line({ title, detail, meta, onPress, urgent }: {
  title: string; detail?: string | null; meta?: string | null; onPress?: () => void; urgent?: boolean;
}) {
  const body = <View style={styles.line}>
    <View style={{ flex: 1, gap: 4 }}>
      <Text style={styles.rowTitle} numberOfLines={1}>{title}</Text>
      {detail ? <Text style={styles.rowDetail} numberOfLines={2}>{detail}</Text> : null}
      {meta ? <Text style={[styles.small, urgent && { color: '#B45309' }]} numberOfLines={2}>{meta}</Text> : null}
    </View>
    {onPress ? <Ionicons name="chevron-forward" color={muted} size={20} /> : null}
  </View>;
  return onPress ? <Pressable onPress={onPress} accessibilityRole="button">{body}</Pressable> : body;
}
export function Notice({ text, danger }: { text: string; danger?: boolean }) {
  return <View style={[styles.notice, danger && { borderColor: '#F2C7B2', backgroundColor: '#FFF4ED' }]}>
    <Text style={[styles.subtitle, danger && { color: '#9A3412' }]}>{text}</Text>
  </View>;
}
export function State({ title, detail, retry, busy }: {
  title: string; detail?: string; retry?: () => void; busy?: boolean;
}) {
  return <View style={{ paddingVertical: 25, gap: 10, alignItems: 'center' }}>
    {busy ? <ActivityIndicator color={blue} /> : null}
    <Text style={styles.rowTitle}>{title}</Text>
    {detail ? <Text style={[styles.subtitle, { textAlign: 'center' }]}>{detail}</Text> : null}
    {retry ? <Pressable onPress={retry} accessibilityRole="button"><Text style={{ color: blue, fontWeight: '700' }}>Try again</Text></Pressable> : null}
  </View>;
}
export function Action({ label, onPress }: { label: string; onPress: () => void }) {
  return <Pressable style={styles.action} onPress={onPress} accessibilityRole="button">
    <Text style={{ color: blue, fontWeight: '700', fontSize: 15 }}>{label}</Text>
    <Ionicons name="arrow-forward" size={17} color={blue} />
  </Pressable>;
}
export const styles = StyleSheet.create({
  page: { padding: 20, paddingBottom: 45, gap: 16 },
  eyebrow: { fontSize: 10, color: blue, fontWeight: '800', letterSpacing: 1.2 },
  heading: { fontSize: 30, lineHeight: 35, fontWeight: '800', color: ink },
  subtitle: { fontSize: 14, lineHeight: 21, color: muted },
  small: { fontSize: 12, lineHeight: 17, color: muted },
  section: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginTop: 3 },
  sectionTitle: { fontSize: 17, fontWeight: '700', color: ink },
  panel: { backgroundColor: white, borderColor: '#E4EBF3', borderWidth: 1,
    borderRadius: 18, padding: 16, gap: 12 },
  metric: { minWidth: '46%', flexGrow: 1, backgroundColor: '#FFFFFF', padding: 16,
    borderColor: '#E4EBF3', borderWidth: 1, borderRadius: 16, gap: 6 },
  metricStrong: { backgroundColor: '#185D95', borderColor: '#185D95' },
  metricNumber: { fontSize: 29, color: ink, fontWeight: '800' },
  metricLabel: { fontSize: 12, color: muted, fontWeight: '600' },
  line: { flexDirection: 'row', alignItems: 'center', minHeight: 55, paddingVertical: 8, gap: 12,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: '#E5ECF3' },
  rowTitle: { fontSize: 15, fontWeight: '700', color: ink },
  rowDetail: { color: muted, fontSize: 13, lineHeight: 19 },
  notice: { padding: 14, backgroundColor: '#E9F2FB', borderColor: '#C7E0F8', borderWidth: 1,
    borderRadius: 12, gap: 6 },
  action: { flexDirection: 'row', padding: 14, justifyContent: 'space-between', alignItems: 'center',
    minHeight: 50, backgroundColor: white, borderColor: '#DDE8F3', borderWidth: 1, borderRadius: 14 },
});
