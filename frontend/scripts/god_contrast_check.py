"""Contrast audit for the God Mode token palette.

God Mode is permanently light, and "light" is not the same claim as "readable".
This reads the token values straight out of `src/pages/god/godTokens.css` — so
the numbers can never drift from what actually ships — and checks every text
token against every surface it is allowed to land on, plus every control
boundary against its own fill.

FLOORS, from WCAG 2.1:
  4.5:1  normal text (1.4.3)
  3:1    UI component boundaries and focus indicators (1.4.11)

Exits non-zero if anything is below target, so it can gate a deploy.

    python frontend/scripts/god_contrast_check.py
"""
import os
import sys as _sys

# Run from anywhere: the sheet is found relative to this file, not to the cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.dirname(_HERE))
import re
import sys

CSS = open('src/pages/god/godTokens.css', encoding='utf-8').read()


def block(anchor):
    i = CSS.index(anchor)
    j = CSS.index('}', i)
    return dict(re.findall(r'(--[\w-]+)\s*:\s*([^;]+);', CSS[i:j]))


LIGHT = block('.gm-shell,\n.gm-scope,\n.go-scope {')


def rgb(h):
    h = h.strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(rgb(a)), lum(rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def solid(tokens, name):
    """The token's value if it is a plain hex we can measure, else None."""
    v = tokens.get(name, '').strip()
    return v if re.fullmatch(r'#[0-9a-fA-F]{3,6}', v) else None


# Text token -> the surfaces it is allowed to sit on, and the floor it must clear.
CHECKS = [
    ('--gm-head', ['--gm-panel', '--gm-panel-2', '--gm-thead'], 4.5),
    ('--gm-text', ['--gm-panel', '--gm-panel-2', '--gm-panel-3', '--gm-thead'], 4.5),
    ('--gm-dim', ['--gm-panel', '--gm-panel-2', '--gm-thead'], 4.5),
    ('--gm-ghost', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-faint', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-field-fg', ['--gm-field'], 4.5),
    ('--gm-placeholder', ['--gm-field'], 4.5),
    ('--gm-btn-fg', ['--gm-btn'], 4.5),
    ('--gm-blue', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-teal', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-amber', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-red', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-purple', ['--gm-panel', '--gm-panel-2'], 4.5),
    ('--gm-pill-teal-fg', ['--gm-pill-teal-bg'], 4.5),
    ('--gm-pill-gold-fg', ['--gm-pill-gold-bg'], 4.5),
    ('--gm-pill-blue-fg', ['--gm-pill-blue-bg'], 4.5),
    ('--gm-pill-red-fg', ['--gm-pill-red-bg'], 4.5),
    ('--gm-pill-purple-fg', ['--gm-pill-purple-bg'], 4.5),
    ('--gm-pill-off-fg', ['--gm-pill-off-bg'], 4.5),
    ('--gm-ink-on-accent', ['--gm-btn-primary'], 4.5),
    ('--gm-btn-primary-fg', ['--gm-btn-primary', '--gm-btn-primary-hover'], 4.5),
    ('--gm-btn-gold-fg', ['--gm-btn-gold', '--gm-btn-gold-hover'], 4.5),
    ('--gm-btn-disabled-fg', ['--gm-btn-disabled'], 4.5),
    ('--gm-faint', ['--gm-rail', '--gm-panel'], 4.5),
    ('--gm-dim', ['--gm-rail', '--gm-thead', '--gm-row-lvl0'], 4.5),
    ('--gm-head', ['--gm-rail', '--gm-row-lvl0', '--gm-bg'], 4.5),
    ('--gm-text', ['--gm-bg', '--gm-row-hover-flat', '--gm-row-lvl2'], 4.5),
    ('--gm-blue', ['--gm-rail', '--gm-row-lvl0', '--gm-bg', '--gm-pill-blue-bg'], 4.5),
    ('--gm-gold', ['--gm-rail', '--gm-btn-gold-soft'], 4.5),
    ('--go-text', ['--go-panel', '--go-panel-2', '--go-bg'], 4.5),
    ('--go-dim', ['--go-panel', '--go-panel-2', '--go-bg'], 4.5),
    ('--go-blue', ['--go-panel', '--go-bg'], 4.5),
    ('--go-green', ['--go-panel', '--go-bg'], 4.5),
    ('--go-amber', ['--go-panel', '--go-bg'], 4.5),
    ('--go-red', ['--go-panel', '--go-bg'], 4.5),
    ('--go-purple', ['--go-panel', '--go-bg'], 4.5),
    ('--go-on-primary', ['--go-primary'], 4.5),
    # Non-text boundaries: 3:1 is the WCAG 1.4.11 floor.
    ('--gm-field-line', ['--gm-field', '--gm-panel'], 3.0),
    ('--gm-btn-line', ['--gm-btn'], 3.0),
    ('--gm-btn-disabled-line', ['--gm-btn-disabled'], 1.0),
    ('--gm-card-line', ['--gm-panel'], 1.0),
    ('--gm-focus-ring', ['--gm-panel', '--gm-bg'], 3.0),
    ('--go-field-line', ['--go-field', '--go-panel'], 3.0),
]

fails = []
rows = []
for label, tokens in (('LIGHT', LIGHT),):
    for fg, bgs, floor in CHECKS:
        f = solid(tokens, fg)
        if not f:
            continue
        for bg in bgs:
            b = solid(tokens, bg)
            if not b:
                continue
            r = ratio(f, b)
            ok = r >= floor
            rows.append(f"{label:5} {fg:22} on {bg:18} {r:5.2f}:1  need {floor}  {'OK' if ok else 'FAIL'}")
            if not ok:
                fails.append((label, fg, bg, round(r, 2), floor))

print('\n'.join(rows))
print()
print(f"{len(rows)} pairs checked · {len(fails)} below target")
for f in fails:
    print('  FAIL', f)
sys.exit(1 if fails else 0)
