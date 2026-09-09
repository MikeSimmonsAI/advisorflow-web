/**
 * THE MIRRORED VOCABULARY.
 *
 * These tests exist because the vocabulary is hand-copied from Python, and a
 * hand-copied constant is a constant that drifts. They assert the two things
 * drift would break first: the stage list matching `OPPORTUNITY_STAGES` exactly,
 * and severity never degrading toward "healthy".
 */

import {
  APPOINTMENT_STATUSES, CONFIRMATION_STATUSES, isKnownStage, normaliseSeverity,
  OPPORTUNITY_STAGES, SEVERITY_LABELS, stageLabel, STAGE_LABELS,
} from '../vocab';

describe('opportunity stages', () => {
  it('matches app/models/sales_models.py exactly, in order', () => {
    expect([...OPPORTUNITY_STAGES]).toEqual([
      'prospect', 'contacted', 'discovery', 'demo_build', 'demo_proposal',
      'closing', 'won', 'onboarding', 'live', 'lost',
    ]);
  });

  it('has a label for every stage and no labels for stages that do not exist', () => {
    for (const s of OPPORTUNITY_STAGES) expect(STAGE_LABELS[s]).toBeTruthy();
    expect(Object.keys(STAGE_LABELS).sort()).toEqual([...OPPORTUNITY_STAGES].sort());
  });

  it('renders an unknown stage as itself rather than hiding it', () => {
    // A server that gains a stage before the app does must not produce a blank
    // chip — a rep looking at a deal with no stage cannot tell what to do.
    expect(stageLabel('renegotiation')).toBe('renegotiation');
    expect(isKnownStage('renegotiation')).toBe(false);
    expect(stageLabel(null)).toBe('No stage');
  });
});

describe('appointments', () => {
  it('matches scheduling_models.APPOINTMENT_STATUSES', () => {
    expect([...APPOINTMENT_STATUSES]).toEqual(
      ['scheduled', 'completed', 'cancelled', 'no_show']);
  });

  it('matches scheduling_models.CONFIRMATION_STATUSES', () => {
    expect([...CONFIRMATION_STATUSES]).toEqual(
      ['pending', 'sent', 'confirmed', 'declined', 'cancelled', 'no_show']);
  });
});

describe('severity', () => {
  it('passes through the five the platform defines', () => {
    for (const s of ['healthy', 'attention', 'action_required', 'unavailable', 'no_data']) {
      expect(normaliseSeverity(s)).toBe(s);
    }
  });

  it('NEVER normalises an unknown value to healthy', () => {
    // The one rule severity.py states outright. A screen that cannot reach the
    // truth must not draw a green tick next to a customer's name.
    for (const bad of ['', 'ok', 'green', 'fine', null, undefined, 'HEALTHY']) {
      expect(normaliseSeverity(bad as string)).toBe('unavailable');
    }
  });

  it('has a human label for each severity', () => {
    expect(SEVERITY_LABELS.action_required).toBe('Action required');
    expect(SEVERITY_LABELS.unavailable).toBe('Unavailable');
  });
});
