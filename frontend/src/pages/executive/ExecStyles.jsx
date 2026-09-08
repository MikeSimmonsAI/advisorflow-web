/**
 * ExecStyles - the Executive surface stylesheet, injected once per app.
 *
 * ===========================================================================
 * THREE SURFACES, THREE PALETTES, ONE APPEARANCE AXIS
 * ===========================================================================
 *
 * The product has three operating layers and they must not be able to restyle
 * one another:
 *
 *     data-surface="platform"    the owner's control plane   (.gm-scope)
 *     data-surface="executive"   this file                   (.ex-scope)
 *     data-surface="workspace"   the customer's own tools    (tenant CSS)
 *
 * Every rule below is nested under [data-surface="executive"], and the tokens
 * are declared on that attribute rather than on :root. A colour changed here
 * cannot reach God Mode or a customer workspace, because the selector cannot
 * match outside this shell. That is a structural guarantee, not a convention
 * somebody has to remember.
 *
 * APPEARANCE IS THE ORTHOGONAL AXIS. data-appearance on <html> is already
 * resolved to a concrete light or dark by appearance.js before it reaches the
 * DOM, so there are no media queries here and no ambiguity about what a
 * screenshot shows. The two compose: surface decides WHICH palette, appearance
 * decides whether the room is lit.
 *
 * ===========================================================================
 * WHY EXECUTIVE LOOKS DIFFERENT FROM EVERYTHING ELSE
 * ===========================================================================
 *
 * The old Executive screens were inline-styled with hard-coded light values -
 * a washed-out #f5f6fa canvas, a #1a1f36 navy rail dropped into it, and a
 * blood-red observation banner. Three unrelated colour systems on one page,
 * none of which followed the appearance setting.
 *
 * This is a deliberate third identity. Not God Mode's dense navy control
 * panel, which is built for an operator running the platform. Not the sales
 * workspace's teal, which is built for somebody working a queue. An executive
 * reads: portfolio summaries, exceptions, comparisons, outcomes.
 *
 * So the direction is INDIGO ON PAPER. A cool, quiet ground; generous space;
 * one restrained accent; large tabular figures; signal colour used sparingly
 * and only where it means something. It should read like a board pack, not a
 * dashboard. The visual weight goes on the NUMBERS and the EXCEPTIONS, and
 * almost nothing else competes.
 */
import { useEffect } from 'react'

const CSS = `
/* -- LIGHT: the default, and the one an executive will actually use -------- */
[data-surface="executive"]{
  --ex-canvas:#f1f4f9;
  --ex-surface:#ffffff; --ex-surface2:#f7f9fc; --ex-surface3:#eaf0f8;
  --ex-field:#ffffff;
  --ex-ink:#16203a; --ex-ink2:#586a88; --ex-ink3:#8595ad; --ex-ink4:#a3b0c4;
  --ex-line:#dbe3ef; --ex-line2:#e9eef6; --ex-line-strong:#a9b9d0;
  --ex-accent:#3a5ba0; --ex-accent-ink:#ffffff;
  --ex-accent-bg:#eaf0fb; --ex-accent-bd:#c2d2ee;
  --ex-good:#16785d; --ex-good-bg:#e6f5ef; --ex-good-bd:#b6e0cf;
  --ex-warn:#8d6014; --ex-warn-bg:#fdf3e1; --ex-warn-bd:#eedcb1;
  --ex-bad:#a02c43;  --ex-bad-bg:#fdebef;  --ex-bad-bd:#f1c6d0;
  --ex-info:#2b608f; --ex-info-bg:#e8f1fa; --ex-info-bd:#c1daee;
  --ex-shadow:0 1px 2px rgba(22,32,58,.04),0 6px 20px rgba(22,32,58,.05);
  --ex-shadow-lift:0 2px 4px rgba(22,32,58,.06),0 12px 30px rgba(22,32,58,.10);
  color-scheme:light;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
  color:var(--ex-ink);
  background:var(--ex-canvas);
}

/* -- DARK: layered slate-indigo, never black on black ----------------------
   Surfaces STACK. The canvas is darkest, cards sit above it, hovers above
   those, and fields sit below the card they are on - the same depth ordering
   the light palette has, running the other way. An inverted light sheet gives
   flat grey-on-grey; this does not.
   The accent lifts rather than dims: #3a5ba0 on white becomes #86a6ea on
   slate, because the same hue at the same lightness disappears against a dark
   ground. Signal colours are re-chosen for the same reason. */
[data-appearance="dark"] [data-surface="executive"]{
  --ex-canvas:#0c1220;
  --ex-surface:#141d31; --ex-surface2:#101827; --ex-surface3:#1c2740;
  --ex-field:#0d1524;
  --ex-ink:#e8eef9; --ex-ink2:#9db0cf; --ex-ink3:#7d90b0; --ex-ink4:#64789a;
  --ex-line:#26324b; --ex-line2:#1c2639; --ex-line-strong:#3d5178;
  --ex-accent:#86a6ea; --ex-accent-ink:#0a1224;
  --ex-accent-bg:#16203a; --ex-accent-bd:#2f4573;
  --ex-good:#57ddab; --ex-good-bg:#0b2b23; --ex-good-bd:#1c6a54;
  --ex-warn:#f2c463; --ex-warn-bg:#2a2009; --ex-warn-bd:#7a5f1f;
  --ex-bad:#ff8fa6;  --ex-bad-bg:#2c1219;  --ex-bad-bd:#7a3549;
  --ex-info:#82bbf0; --ex-info-bg:#0e2135; --ex-info-bd:#254c71;
  --ex-shadow:0 1px 2px rgba(0,0,0,.3),0 6px 20px rgba(0,0,0,.35);
  --ex-shadow-lift:0 2px 6px rgba(0,0,0,.4),0 14px 34px rgba(0,0,0,.45);
  color-scheme:dark;
}

[data-surface="executive"] *{box-sizing:border-box}
[data-surface="executive"] button,
[data-surface="executive"] input,
[data-surface="executive"] select{font:inherit}
[data-surface="executive"] b,
[data-surface="executive"] strong,
[data-surface="executive"] td,
[data-surface="executive"] th,
[data-surface="executive"] .ex-n{
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
[data-surface="executive"] :focus-visible{
  outline:2px solid var(--ex-accent);outline-offset:2px;border-radius:8px}
@media(prefers-reduced-motion:reduce){
  [data-surface="executive"] *{transition:none!important;animation:none!important}
}

/* -- the shell ------------------------------------------------------------ */
.ex-shell{display:grid;grid-template-columns:246px minmax(0,1fr);
  min-height:100vh;background:var(--ex-canvas)}
.ex-rail{background:var(--ex-surface);border-right:1px solid var(--ex-line);
  display:flex;flex-direction:column;position:sticky;top:0;height:100vh;
  padding:20px 14px 14px}
.ex-brand{padding:0 8px 16px;border-bottom:1px solid var(--ex-line2);
  margin-bottom:14px}
.ex-brand b{display:block;font-size:15px;font-weight:700;letter-spacing:-.02em;
  line-height:1.25;color:var(--ex-ink)}
.ex-brand .ex-role{display:inline-block;margin-top:7px;font-size:9px;
  font-weight:800;letter-spacing:.14em;text-transform:uppercase;
  color:var(--ex-accent);background:var(--ex-accent-bg);
  border:1px solid var(--ex-accent-bd);border-radius:999px;padding:3px 9px}
.ex-navgroup{font-size:9px;font-weight:800;letter-spacing:.15em;
  text-transform:uppercase;color:var(--ex-ink4);padding:14px 10px 6px}
.ex-nav{display:flex;flex-direction:column;gap:2px}
.ex-nav a{display:flex;align-items:center;gap:9px;padding:9px 11px;
  border-radius:8px;text-decoration:none;font-size:13px;font-weight:500;
  color:var(--ex-ink2)}
.ex-nav a:hover{background:var(--ex-surface3);color:var(--ex-ink)}
.ex-nav a.on{background:var(--ex-accent-bg);color:var(--ex-accent);
  font-weight:650;box-shadow:inset 2px 0 var(--ex-accent)}
.ex-nav .ex-badge{margin-left:auto;font-size:9.5px;font-weight:800;
  border-radius:999px;padding:2px 7px;background:var(--ex-bad-bg);
  color:var(--ex-bad);border:1px solid var(--ex-bad-bd)}
.ex-railfill{flex:1 1 auto;min-height:14px}
.ex-railfoot{border-top:1px solid var(--ex-line2);padding-top:12px;
  display:flex;flex-direction:column;gap:10px}
.ex-who{font-size:11px;color:var(--ex-ink3);word-break:break-all}
.ex-main{min-width:0;padding:26px 30px 60px}

/* -- page furniture ------------------------------------------------------- */
.ex-head{display:flex;align-items:flex-start;justify-content:space-between;
  gap:20px;flex-wrap:wrap;margin-bottom:22px}
.ex-h1{margin:0;font-size:24px;font-weight:700;letter-spacing:-.025em;
  color:var(--ex-ink);line-height:1.15}
.ex-sub{margin:6px 0 0;font-size:13px;color:var(--ex-ink2);line-height:1.6;
  max-width:70ch}
.ex-eyebrow{font-size:9.5px;font-weight:800;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ex-ink4);margin:0 0 7px}
.ex-section{margin-top:28px}
.ex-section > h2{margin:0 0 12px;font-size:10px;font-weight:800;
  letter-spacing:.16em;text-transform:uppercase;color:var(--ex-ink3);
  display:flex;align-items:center;gap:10px}
.ex-section > h2 span{font-size:11px;letter-spacing:0;font-weight:500;
  text-transform:none;color:var(--ex-ink4)}

.ex-card{background:var(--ex-surface);border:1px solid var(--ex-line);
  border-radius:14px;box-shadow:var(--ex-shadow)}
.ex-card-h{padding:16px 18px;border-bottom:1px solid var(--ex-line2);
  display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.ex-card-h h3{margin:0;font-size:13px;font-weight:650;color:var(--ex-ink)}
.ex-card-h p{margin:3px 0 0;font-size:11.5px;color:var(--ex-ink3);
  line-height:1.5}
.ex-card-b{padding:18px}

/* -- the KPI band ---------------------------------------------------------
   A board pack leads with the number. These are deliberately large, tabular
   and quiet - no icon, no sparkline, no colour unless the figure has earned
   one. The label above and the qualifier below do the explaining. */
.ex-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:14px}
.ex-kpi{background:var(--ex-surface);border:1px solid var(--ex-line);
  border-radius:14px;padding:17px 18px 16px;box-shadow:var(--ex-shadow);
  text-align:left;width:100%;display:block;
  transition:border-color .13s,box-shadow .13s,transform .13s}
button.ex-kpi{cursor:pointer}
button.ex-kpi:hover{border-color:var(--ex-line-strong);
  box-shadow:var(--ex-shadow-lift);transform:translateY(-1px)}
.ex-kpi .ex-k{display:block;font-size:9.5px;font-weight:800;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ex-ink3)}
.ex-kpi .ex-v{display:block;margin-top:9px;font-size:32px;font-weight:700;
  letter-spacing:-.03em;line-height:1;color:var(--ex-ink);
  font-variant-numeric:tabular-nums}
.ex-kpi .ex-s{display:block;margin-top:8px;font-size:11.5px;color:var(--ex-ink3);
  line-height:1.5}
/* A FIGURE NOBODY CAN COMPUTE. Rendered as words, in the muted ink, at a
   smaller size - so it reads as an answer and never as a value. It is never
   a zero: "we cannot price this" and "this earns nothing" are opposite. */
.ex-kpi .ex-v.ex-none{font-size:17px;font-weight:600;color:var(--ex-ink4);
  font-style:italic;letter-spacing:0}
.ex-kpi.ex-lead .ex-v{color:var(--ex-accent)}
.ex-kpi.ex-alarm{border-color:var(--ex-bad-bd);background:var(--ex-bad-bg)}
.ex-kpi.ex-alarm .ex-v{color:var(--ex-bad)}
.ex-kpi.ex-alarm .ex-k,.ex-kpi.ex-alarm .ex-s{color:var(--ex-ink2)}

/* -- health pills --------------------------------------------------------- */
.ex-pill{display:inline-block;border-radius:999px;padding:3px 10px;
  font-size:9.5px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;
  white-space:nowrap;border:1px solid var(--ex-line);
  background:var(--ex-surface2);color:var(--ex-ink2)}
.ex-pill.h-healthy{background:var(--ex-good-bg);border-color:var(--ex-good-bd);
  color:var(--ex-good)}
.ex-pill.h-watch{background:var(--ex-warn-bg);border-color:var(--ex-warn-bd);
  color:var(--ex-warn)}
.ex-pill.h-at_risk{background:var(--ex-bad-bg);border-color:var(--ex-bad-bd);
  color:var(--ex-bad)}
.ex-pill.h-inactive{background:transparent;border-style:dashed;
  border-color:var(--ex-line);color:var(--ex-ink3)}
.ex-pill.h-onboarding{background:var(--ex-info-bg);border-color:var(--ex-info-bd);
  color:var(--ex-info)}

/* -- exception rows -------------------------------------------------------
   The one thing on the Command Center an executive is meant to ACT on, so it
   gets a severity rule and the organization's name at reading size. */
.ex-exc{display:flex;flex-direction:column;gap:10px}
.ex-exc-row{display:grid;grid-template-columns:3px minmax(0,1fr) auto;gap:16px;
  align-items:start;border:1px solid var(--ex-line);border-radius:12px;
  background:var(--ex-surface);overflow:hidden}
.ex-exc-bar{align-self:stretch;background:var(--ex-warn)}
.ex-exc-row.sv-action_required .ex-exc-bar{background:var(--ex-bad)}
.ex-exc-body{padding:14px 0}
.ex-exc-side{padding:14px 16px 14px 0;display:flex;align-items:center;gap:9px;
  flex-shrink:0}
.ex-exc-t{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ex-exc-t b{font-size:14px;font-weight:650;color:var(--ex-ink)}
.ex-exc-why{margin:5px 0 0;font-size:12px;color:var(--ex-ink2);line-height:1.6}
.ex-exc-items{margin:9px 0 0;padding:0;list-style:none;display:flex;
  flex-direction:column;gap:5px}
.ex-exc-items li{font-size:12px;color:var(--ex-ink2);line-height:1.55;
  padding-left:15px;position:relative}
.ex-exc-items li:before{content:"";position:absolute;left:2px;top:7px;
  width:5px;height:5px;border-radius:50%;background:var(--ex-warn)}
.ex-exc-items li.sv-action_required:before{background:var(--ex-bad)}
.ex-exc-items li.sv-action_required{color:var(--ex-ink)}

/* -- buttons -------------------------------------------------------------- */
.ex-btn{border:1px solid var(--ex-line);background:var(--ex-surface);
  color:var(--ex-ink);border-radius:9px;padding:8px 14px;font-size:12.5px;
  font-weight:600;cursor:pointer;white-space:nowrap}
.ex-btn:hover{border-color:var(--ex-line-strong);background:var(--ex-surface3)}
.ex-btn.ex-primary{background:var(--ex-accent);border-color:var(--ex-accent);
  color:var(--ex-accent-ink)}
.ex-btn.ex-primary:hover{filter:brightness(1.06)}
.ex-btn.ex-small{padding:6px 11px;font-size:11.5px;border-radius:8px}
.ex-btn:disabled{opacity:.5;cursor:default}
.ex-select{border:1px solid var(--ex-line);background:var(--ex-field);
  color:var(--ex-ink);border-radius:9px;padding:8px 11px;font-size:12.5px;
  max-width:100%}

/* -- filter tabs ---------------------------------------------------------- */
.ex-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
.ex-tab{border:1px solid var(--ex-line);background:var(--ex-surface);
  color:var(--ex-ink2);border-radius:999px;padding:7px 13px;font-size:12px;
  font-weight:600;cursor:pointer;display:inline-flex;align-items:center;gap:7px}
.ex-tab:hover{border-color:var(--ex-line-strong);color:var(--ex-ink)}
.ex-tab.on{background:var(--ex-accent);border-color:var(--ex-accent);
  color:var(--ex-accent-ink)}
.ex-tab .ex-c{font-size:10.5px;font-weight:800;opacity:.75;
  font-variant-numeric:tabular-nums}
/* A ZERO-COUNT TAB STAYS VISIBLE AND GOES QUIET. Hiding it would make the
   filter bar change shape as data changes, and "Billing (0)" is a useful
   answer an executive should be able to read without clicking. */
.ex-tab.ex-empty{opacity:.5}

/* -- tables --------------------------------------------------------------- */
.ex-tablewrap{overflow-x:auto;border:1px solid var(--ex-line);
  border-radius:14px;background:var(--ex-surface);box-shadow:var(--ex-shadow)}
.ex-table{width:100%;border-collapse:collapse;font-size:13px}
.ex-table th{text-align:left;font-size:9.5px;font-weight:800;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ex-ink3);padding:12px 14px;
  border-bottom:1px solid var(--ex-line);white-space:nowrap;
  background:var(--ex-surface)}
.ex-table td{padding:13px 14px;border-bottom:1px solid var(--ex-line2);
  vertical-align:top;color:var(--ex-ink2)}
.ex-table tbody tr:last-child td{border-bottom:0}
.ex-table tbody tr:hover{background:var(--ex-surface3)}
.ex-table .ex-num{text-align:right;font-variant-numeric:tabular-nums;
  white-space:nowrap;color:var(--ex-ink)}
.ex-table .ex-org{color:var(--ex-ink);font-weight:650;font-size:13.5px}
.ex-table .ex-why{display:block;margin-top:3px;font-size:11.5px;
  color:var(--ex-ink3);line-height:1.5;max-width:46ch}
.ex-orgbtn{background:none;border:0;padding:0;cursor:pointer;text-align:left;
  color:var(--ex-ink);font:inherit;font-weight:650;font-size:13.5px}
.ex-orgbtn:hover{color:var(--ex-accent);text-decoration:underline}
.ex-flag{display:inline-block;margin:2px 5px 0 0;font-size:10.5px;
  font-weight:700;border-radius:6px;padding:2px 7px;
  background:var(--ex-warn-bg);color:var(--ex-warn);
  border:1px solid var(--ex-warn-bd)}
.ex-flag.sv-action_required{background:var(--ex-bad-bg);color:var(--ex-bad);
  border-color:var(--ex-bad-bd)}

/* -- the drill-down header ------------------------------------------------
   Replaces a blood-red "READ ONLY" banner. The old one shouted a restriction
   at somebody who was never trying to edit anything; this states the context
   and gets out of the way. */
.ex-orghead{background:var(--ex-surface);border:1px solid var(--ex-line);
  border-radius:16px;padding:22px 24px;box-shadow:var(--ex-shadow);
  margin-bottom:8px}
.ex-orghead-top{display:flex;align-items:flex-start;justify-content:space-between;
  gap:18px;flex-wrap:wrap}
.ex-orghead h1{margin:0;font-size:26px;font-weight:700;letter-spacing:-.03em;
  color:var(--ex-ink);line-height:1.12}
.ex-orgmeta{display:flex;flex-wrap:wrap;gap:0 26px;margin-top:16px;
  padding-top:15px;border-top:1px solid var(--ex-line2)}
.ex-orgmeta div{min-width:0}
.ex-orgmeta span{display:block;font-size:9.5px;font-weight:800;
  letter-spacing:.12em;text-transform:uppercase;color:var(--ex-ink4)}
.ex-orgmeta b{display:block;margin-top:4px;font-size:13px;font-weight:600;
  color:var(--ex-ink)}
.ex-orgmeta b.ex-none{font-weight:500;font-style:italic;color:var(--ex-ink4)}
.ex-context{display:inline-flex;align-items:center;gap:8px;font-size:10px;
  font-weight:800;letter-spacing:.12em;text-transform:uppercase;
  color:var(--ex-info);background:var(--ex-info-bg);
  border:1px solid var(--ex-info-bd);border-radius:999px;padding:5px 12px}

/* -- comparison ----------------------------------------------------------- */
.ex-compare{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;
  font-size:11.5px;color:var(--ex-ink3);margin-top:7px}
.ex-compare b{font-size:11.5px;color:var(--ex-ink2)}
.ex-delta{font-weight:800;font-size:11.5px}
.ex-delta.up{color:var(--ex-good)}
.ex-delta.down{color:var(--ex-bad)}
.ex-delta.flat{color:var(--ex-ink3)}

/* -- states --------------------------------------------------------------- */
.ex-blank{text-align:center;padding:44px 24px}
.ex-blank b{display:block;font-size:15px;font-weight:650;color:var(--ex-ink)}
.ex-blank p{margin:9px auto 0;max-width:54ch;font-size:12.5px;
  color:var(--ex-ink2);line-height:1.65}
.ex-allclear{display:flex;gap:12px;align-items:flex-start;padding:16px 18px;
  border:1px solid var(--ex-good-bd);background:var(--ex-good-bg);
  border-radius:12px}
.ex-allclear b{display:block;font-size:13.5px;color:var(--ex-good)}
.ex-allclear p{margin:4px 0 0;font-size:12px;color:var(--ex-ink2);
  line-height:1.6}
.ex-err{border:1px solid var(--ex-bad-bd);background:var(--ex-bad-bg);
  color:var(--ex-bad);border-radius:12px;padding:14px 16px;font-size:13px;
  margin-bottom:16px}
.ex-muted{font-size:12.5px;color:var(--ex-ink3)}
.ex-none-inline{font-style:italic;color:var(--ex-ink4)}

/* -- centred full-page states (access, brand choice) ---------------------- */
.ex-full{min-height:100vh;display:flex;align-items:center;justify-content:center;
  padding:32px;background:var(--ex-canvas)}
.ex-panel{background:var(--ex-surface);border:1px solid var(--ex-line);
  border-radius:16px;box-shadow:var(--ex-shadow-lift);padding:36px 40px;
  max-width:460px;width:100%}
.ex-panel h2{margin:0 0 8px;font-size:19px;font-weight:700;color:var(--ex-ink);
  letter-spacing:-.02em}
.ex-panel p{margin:0 0 22px;font-size:13px;color:var(--ex-ink2);line-height:1.6}
.ex-choices{display:flex;flex-direction:column;gap:9px;margin-bottom:18px}
.ex-choice{background:var(--ex-surface2);border:1px solid var(--ex-line);
  border-radius:11px;padding:13px 17px;font-size:14.5px;font-weight:650;
  color:var(--ex-ink);cursor:pointer;text-align:left}
.ex-choice:hover{border-color:var(--ex-accent);background:var(--ex-accent-bg)}

@media(max-width:1000px){
  .ex-shell{grid-template-columns:1fr}
  .ex-rail{position:static;height:auto;flex-direction:column}
  .ex-main{padding:20px 18px 48px}
  .ex-orgmeta{gap:0 18px}
}
@media(max-width:640px){
  .ex-h1{font-size:21px}
  .ex-orghead h1{font-size:22px}
  .ex-kpi .ex-v{font-size:27px}
  .ex-exc-row{grid-template-columns:3px minmax(0,1fr)}
  .ex-exc-side{grid-column:2;padding:0 16px 14px 0}
}
`

let injected = false

export default function ExecStyles() {
  useEffect(() => {
    if (injected || document.getElementById('ex-styles')) return
    const el = document.createElement('style')
    el.id = 'ex-styles'
    el.textContent = CSS
    document.head.appendChild(el)
    injected = true
  }, [])
  return null
}
