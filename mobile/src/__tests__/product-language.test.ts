/**
 * A STATIC SWEEP OF THE SCREENS THEMSELVES.
 *
 * The unit tests above prove the adapter produces words. These prove no screen
 * goes around it — because the original defects were not in the adapter, they
 * were in a screen reaching into a payload and printing whatever came back.
 *
 * These read source files rather than rendering, deliberately: rendering
 * requires a payload, and the bug was always "the payload we assumed is not the
 * payload we get". Source is the one thing that cannot be mocked into passing.
 */

import fs from 'fs';
import path from 'path';

import { attentionKindLabel, humanText, looksLikeCode, roleLabel } from '../vocab';

const APP = path.join(__dirname, '..', '..', 'app');

function read(rel: string): string {
  return fs.readFileSync(path.join(APP, rel), 'utf8');
}

/** Comments are stripped: the explanation of why a code was removed contains
 *  that code, and a guard that fails on its own rationale teaches people to
 *  delete the rationale. */
function rendered(rel: string): string {
  return read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/^\s*\/\/.*$/gm, ' ');
}

const MANAGER_SCREENS = [
  '(manager)/home.tsx',
  '(manager)/pipeline.tsx',
  '(manager)/team.tsx',
  '(manager)/approvals.tsx',
];

describe('no manager screen keys a list on the loop index alone', () => {
  it.each(MANAGER_SCREENS)('%s', (file) => {
    const src = rendered(file);
    // `key={String(x ?? i)}` and `key={i}` are the two spellings that produced
    // the duplicate-key error; both fall back to a position, which is stable
    // only until the list reorders — and neither is unique when the id repeats.
    expect(src).not.toMatch(/key=\{\s*i\s*\}/);
    expect(src).not.toMatch(/key=\{String\([^)]*\?\?\s*i\s*\)\}/);
  });
});

describe('no manager screen prints a field the server means for logic', () => {
  it.each(MANAGER_SCREENS)('%s does not render `kind` as a label', (file) => {
    const src = rendered(file);
    // The exact expression that produced "Deal / video_failed".
    expect(src).not.toMatch(/pick\([^)]*'kind'/);
  });

  it.each(MANAGER_SCREENS)('%s does not render `role` raw', (file) => {
    const src = rendered(file);
    expect(src).not.toMatch(/subtitle=\{String\(r\.role\s*\?\?/);
  });
});

describe('the Team screen names its money', () => {
  const src = rendered('(manager)/team.tsx');

  it('says "open" next to the figure rather than showing a bare amount', () => {
    expect(src).toMatch(/open/i);
    // A Pill whose label is nothing but asMoney(...) is the unlabelled badge.
    expect(src).not.toMatch(/<Pill label=\{asMoney\(r\.pipeline_value\)\}/);
  });

  it('explains the figure once in prose', () => {
    expect(src).toMatch(/OPEN PIPELINE/);
  });
});

describe('the Pipeline screen explains a missing weighted value', () => {
  const src = rendered('(manager)/pipeline.tsx');

  it('routes weighted through the adapter rather than asMoney(undefined)', () => {
    expect(src).toMatch(/weightedDisplay/);
    expect(src).not.toMatch(/asMoney\(weighted\)/);
  });

  it('draws the by-stage section from grouped deals', () => {
    expect(src).toMatch(/stageGroups/);
    expect(src).not.toMatch(/'by_stage'/);
  });
});

describe('the Approvals screen sends the field the route reads', () => {
  const src = rendered('(manager)/approvals.tsx');

  it('posts `approve`, which is the only key decide_approval looks at', () => {
    expect(src).toMatch(/approve:\s*approved/);
  });

  it('no longer posts a decision spelling the server ignores', () => {
    expect(src).not.toMatch(/decision:\s*approved\s*\?/);
  });

  it('reads the amounts request_out actually serialises', () => {
    expect(src).toMatch(/base_amount/);
    expect(src).toMatch(/requested_total/);
    expect(src).not.toMatch(/floor_amount/);
    expect(src).not.toMatch(/requested_amount\b/);
  });
});

describe('More is not a diagnostics page', () => {
  const src = rendered(path.join('..', 'src', 'components', 'MoreScreen.tsx'));

  it('does not print the runtime reason at the person', () => {
    expect(src).not.toMatch(/push\.reason/);
    expect(src).not.toMatch(/uploads\.reason/);
  });

  it('uses concise product states', () => {
    expect(src).toMatch(/Not configured/);
  });
});

// ── the label tables themselves ─────────────────────────────────────────────

describe('every code this app can receive has a phrase', () => {
  const KINDS = [
    'proposal_declined', 'change_requested', 'proposal_expired',
    'proposal_unopened', 'proposal_ready', 'overdue_action', 'no_next_action',
    'stalled', 'no_activity', 'calendar_sync', 'video_failed',
  ];

  it.each(KINDS)('%s reads as words', (kind) => {
    const label = attentionKindLabel(kind);
    expect(label).toBeTruthy();
    expect(looksLikeCode(label)).toBe(false);
  });

  it.each(['sales_rep', 'sales_manager', 'brand_executive', 'god_admin'])(
    '%s reads as words', (role) => {
      expect(looksLikeCode(roleLabel(role))).toBe(false);
    });

  it('degrades an unknown code to words rather than showing it raw', () => {
    expect(looksLikeCode(attentionKindLabel('some_future_kind'))).toBe(false);
    expect(looksLikeCode(roleLabel('regional_director'))).toBe(false);
  });

  it('never returns empty, which would render as a blank row', () => {
    expect(attentionKindLabel(null)).toBeTruthy();
    expect(attentionKindLabel('')).toBeTruthy();
    expect(roleLabel(undefined)).toBeTruthy();
  });
});

describe('looksLikeCode', () => {
  it('catches the codes Mike photographed', () => {
    for (const c of ['video_failed', 'overdue_action', 'proposal_unopened',
                     'sales_rep', 'sales_manager']) {
      expect(looksLikeCode(c)).toBe(true);
    }
  });

  it('does not flag real prose', () => {
    for (const s of ['Video meeting failed', 'Follow-up overdue', 'Walmart',
                     'Sales Representative', 'Sent 6 days ago. No portal activity.']) {
      expect(looksLikeCode(s)).toBe(false);
    }
  });

  it('humanText swaps a code for the fallback and keeps real text', () => {
    expect(humanText('video_failed', 'Video meeting failed')).toBe('Video meeting failed');
    expect(humanText('Video meeting failed', 'x')).toBe('Video meeting failed');
    expect(humanText(null, 'x')).toBe('x');
  });
});
