/**
 * SalesStyles — injects the Sales Workspace stylesheet once per app.
 *
 * Every rule is prefixed `sw-` and every selector is nested under `.sw-scope`,
 * so nothing here can reach the tenant app's screens or God Mode. This is the
 * whole reason the approved prototype's look can live beside a differently
 * themed application without either one restyling the other.
 *
 * Palette and layout come from EvoSysPro_Salesperson_Workspace_Prototype.html:
 * dark navy rail, light canvas, white cards, teal accent. Do not "harmonise"
 * this with the dark tenant theme — the prototype was approved as-is.
 */
import { useEffect } from 'react'

const CSS = `
/* ── THE PALETTE, ON TWO AXES ────────────────────────────────────────────────
   BRAND lives on data-theme and decides whose colours these are.
   APPEARANCE lives on data-appearance and decides whether the room is lit.
   They are independent: Mike wanting a bright interface must not repaint his
   customers' branding, and a brand must not be able to force somebody's eyes
   into a dark room.

   data-appearance is always CONCRETE - appearance.js resolves 'system' to
   light or dark before it touches the DOM - so there is no media query here
   and a screenshot is never ambiguous about what was actually rendered.

   LIGHT IS THE APPROVED PROTOTYPE, VALUE FOR VALUE. Every light token below is
   the literal that was already in this sheet; tokenizing changed nothing about
   how the workspace looks on its own page. What it bought is the dark block
   further down - without which the Compensation Command Center rendered as
   white rectangles floating inside dark God Mode. */
.sw-scope{
  --sw-nav1:#08121f; --sw-nav2:#0a1726; --sw-navline:#1b2d3e;
  --sw-canvas:#eef2f6;
  --sw-surface:#ffffff; --sw-surface2:#f7f9fb; --sw-surface3:#f1f5f8;
  --sw-btn-bg:#ffffff; --sw-field:#ffffff;
  --sw-ink:#182330; --sw-ink2:#5f7182; --sw-ink3:#80909d; --sw-ink4:#93a3b0;
  --sw-line:#d7e0e7; --sw-line2:#e6ebef; --sw-line-strong:#9fb0bf;
  --sw-shadow:0 5px 18px rgba(26,45,65,.045);
  --sw-shadow-lift:0 5px 16px rgba(31,50,70,.09);
  --sw-shadow-modal:0 24px 60px rgba(10,22,36,.35);
  --sw-ok-bg:#eaf9f2; --sw-ok-bd:#b9ead5; --sw-ok-fg:#207e5d;
  --sw-warn-bg:#fff6e9; --sw-warn-bd:#f2d5aa; --sw-warn-fg:#9e6722;
  --sw-bad-bg:#ffeff1; --sw-bad-bd:#efc0c6; --sw-bad-fg:#9d3f4b;
  --sw-info-bg:#edf7ff; --sw-info-bd:#c2dff3; --sw-info-fg:#276c9f;
  --sw-neu-bg:#f7f9fb; --sw-neu-bd:#cfd9e0; --sw-neu-fg:#536677;
  --sw-accent-bg:#f1fbf9; --sw-accent-bd:#b7e4dc;
  --sw-appr-bg:#fffdf7;
  --sw-teal:#1A9B8E; --sw-teal2:#2cc9b8; --sw-teal-deep:#0f7e73;
  --sw-amber:#e7aa50; --sw-green:#55c79a; --sw-blue:#4ea7e8; --sw-red:#e46872;
  color-scheme:light;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
  color:var(--sw-ink);
}
.sw-scope b,.sw-scope strong,.sw-scope td,.sw-scope th,.sw-scope time{
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1
}

/* ── DARK ────────────────────────────────────────────────────────────────────
   NOT AN INVERSION. Inverting the light sheet gives you grey text on grey
   cards and signal colours that fluoresce. This is a designed counterpart:

   - Surfaces STACK by lightness. Canvas is darkest, cards sit above it,
     hovers above those, and fields sit BELOW the card they are on. In light
     the same three tokens run the other way. Depth stays legible either way,
     which is what stops a dark UI reading as one flat sheet.
   - Signal colours are re-chosen, not dimmed. Light needs saturated ink on a
     pale wash; dark needs a bright fill on a deep wash, or the pill loses its
     meaning at a glance.
   - Borders get MORE contrast relative to their surface than in light. A
     hairline that reads clearly on white disappears on navy, and the table
     rules in this workspace are load-bearing.

   The palette sits in the same navy family as God Mode on purpose: the
   Compensation Command Center renders embedded inside that shell, and a
   second blue would look like a screenshot pasted into the page. */
[data-appearance="dark"] .sw-scope{
  --sw-nav1:#060e18; --sw-nav2:#08131f; --sw-navline:#18293a;
  --sw-canvas:#0a1320;
  --sw-surface:#111e2d; --sw-surface2:#0d1826; --sw-surface3:#18293b;
  --sw-btn-bg:#1a2b3d; --sw-field:#0c1826;
  --sw-ink:#e8f1f8; --sw-ink2:#9db3c8; --sw-ink3:#7c93aa; --sw-ink4:#657e96;
  --sw-line:#24384e; --sw-line2:#1b2c3f; --sw-line-strong:#3a5877;
  --sw-shadow:0 2px 10px rgba(0,0,0,.4);
  --sw-shadow-lift:0 6px 20px rgba(0,0,0,.5);
  --sw-shadow-modal:0 24px 60px rgba(0,0,0,.62);
  --sw-ok-bg:#0c2b23; --sw-ok-bd:#1e7059; --sw-ok-fg:#54e3b8;
  --sw-warn-bg:#2b2109; --sw-warn-bd:#7d6220; --sw-warn-fg:#f4c862;
  --sw-bad-bg:#2d1219; --sw-bad-bd:#7c374d; --sw-bad-fg:#ff90a6;
  --sw-info-bg:#0d2137; --sw-info-bd:#254d73; --sw-info-fg:#84bcf0;
  --sw-neu-bg:#152131; --sw-neu-bd:#2c4158; --sw-neu-fg:#9db3c8;
  --sw-accent-bg:#0b2b28; --sw-accent-bd:#1c6f66;
  --sw-appr-bg:#1d1708;
  --sw-teal:#2cc9b8; --sw-teal2:#54e6d4; --sw-teal-deep:#1fa294;
  --sw-amber:#f0b862; --sw-green:#5fd9a9; --sw-blue:#6ab4f0; --sw-red:#f08292;
  color-scheme:dark;
}
/* A primary button is teal in both appearances, so its LABEL has to stay
   readable against teal - not against the page. Left as an explicit literal
   rather than a token for exactly that reason. */
[data-appearance="dark"] .sw-scope .sw-btn.sw-primary,
[data-appearance="dark"] .sw-scope .sw-tiny.sw-primary{color:#04201c}
/* The rail is already dark in light mode, where it is the one dark element on
   a pale page. In dark mode it must stay distinguishable from the canvas
   rather than merging into it, which is why nav1/nav2 above go DARKER than
   the canvas instead of lighter. */
[data-appearance="dark"] .sw-scope .sw-topbar{background:var(--sw-surface)}

/* Keyboard focus, both appearances. The prototype had none, which is an
   accessibility failure on a screen that settles payments. */
.sw-scope :focus-visible{outline:2px solid var(--sw-teal);outline-offset:2px;
  border-radius:6px}
/* Motion is decoration on these screens - the tile lift, the row hover. Anyone
   who has asked their machine to stop moving things should not have to ask
   twice, and a finance screen has no animation worth defending. */
@media(prefers-reduced-motion:reduce){
  .sw-scope *{transition:none!important;animation:none!important}
  .sw-scope .sw-tile:active{transform:none}
}
.sw-scope *{box-sizing:border-box}
.sw-scope button,.sw-scope input,.sw-scope select,.sw-scope textarea{font:inherit}

/* frame */
.sw-app{display:grid;grid-template-columns:248px 1fr;min-height:100vh;background:var(--sw-canvas)}
.sw-sidebar{background:linear-gradient(180deg,var(--sw-nav1),var(--sw-nav2));
  border-right:1px solid var(--sw-navline);padding:20px 14px;position:sticky;top:0;
  height:100vh;display:flex;flex-direction:column;color:#eef5fb}
.sw-brand{display:flex;align-items:center;gap:10px;padding:0 8px 18px;flex:0 0 auto}
.sw-brandmark{width:36px;height:36px;border-radius:10px;flex:0 0 auto;
  background:linear-gradient(135deg,var(--sw-teal2),#0e6b63);display:grid;place-items:center;
  font-weight:900;color:#05110f;font-size:15px}
.sw-brand b{font-size:15px;display:block;line-height:1.1}
.sw-brand small{display:block;color:#6f879d;font-size:10px;margin-top:3px}
.sw-profile{background:#0d1b2b;border:1px solid #203449;border-radius:12px;padding:12px;
  margin-bottom:16px;flex:0 0 auto}
.sw-who{display:flex;align-items:center;gap:10px}
.sw-avatar{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;
  background:#1a3349;color:#bceafa;font-weight:800;font-size:12px;flex:0 0 auto}
.sw-profile b{font-size:12px;display:block;line-height:1.2}
.sw-profile small{display:block;color:#7e95aa;font-size:10px;margin-top:2px}
.sw-navtitle{font-size:9px;color:#557089;letter-spacing:.14em;padding:10px 10px 7px;flex:0 0 auto}
.sw-nav{display:flex;flex-direction:column;gap:4px;flex:0 0 auto}
.sw-nav a,.sw-nav button{border:0;background:transparent;color:#8da4b8;text-align:left;
  padding:10px 11px;border-radius:8px;cursor:pointer;font-size:12px;display:flex;
  align-items:center;gap:9px;text-decoration:none;width:100%}
.sw-nav a:hover,.sw-nav button:hover,.sw-nav a.sw-on{background:#11273a;color:#f4fbff}
.sw-nav a.sw-on{box-shadow:inset 3px 0 var(--sw-teal)}
.sw-nav .sw-count{margin-left:auto;background:#22394d;color:#b5ccdf;border-radius:20px;
  padding:2px 7px;font-size:9px}
.sw-nav .sw-soon{margin-left:auto;color:#5d768d;font-size:8px;letter-spacing:.08em}
.sw-nav a.sw-disabled{opacity:.45;cursor:default;pointer-events:none}
.sw-sidefill{flex:1 1 auto;min-height:12px}
.sw-mini{font-size:9px;color:#5f778d;border-top:1px solid #1d3041;padding:12px 8px 0;flex:0 0 auto}
.sw-mini button{color:#7e97ad;background:none;border:0;padding:0;font-size:9px;cursor:pointer;
  text-decoration:underline}

.sw-main{min-width:0;display:flex;flex-direction:column}
.sw-topbar{min-height:68px;background:var(--sw-surface);border-bottom:1px solid var(--sw-line);display:flex;
  align-items:center;padding:14px 26px;gap:12px;position:sticky;top:0;z-index:5;flex-wrap:wrap}
.sw-topbar h1{font-size:18px;margin:0}
.sw-topbar p{font-size:11px;color:var(--sw-ink2);margin:3px 0 0}
.sw-spacer{flex:1}
.sw-body{padding:22px 26px 60px;flex:1}

/* controls */
.sw-btn{border:1px solid var(--sw-line);background:var(--sw-btn-bg);color:var(--sw-ink);border-radius:8px;
  padding:8px 12px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap}
.sw-btn:hover{border-color:var(--sw-line-strong)}
.sw-btn.sw-primary{background:var(--sw-teal);border-color:var(--sw-teal);color:#fff}
.sw-btn.sw-primary:hover{background:var(--sw-teal-deep);border-color:var(--sw-teal-deep)}
.sw-btn:disabled{opacity:.5;cursor:default}
.sw-tiny{padding:5px 8px;border-radius:6px;border:1px solid var(--sw-line);background:var(--sw-btn-bg);
  font-size:9px;font-weight:700;cursor:pointer;color:var(--sw-ink);white-space:nowrap}
.sw-tiny.sw-primary{background:var(--sw-teal-deep);color:#fff;border-color:var(--sw-teal-deep)}
.sw-input,.sw-select,.sw-textarea{width:100%;padding:9px;border:1px solid var(--sw-line);
  border-radius:7px;background:var(--sw-field);font-size:11px;color:var(--sw-ink)}
.sw-textarea{min-height:64px;resize:vertical;line-height:1.5}
.sw-field{margin-top:12px}
.sw-field label{display:block;font-size:9px;font-weight:800;color:var(--sw-ink2);
  margin-bottom:5px;letter-spacing:.05em}

/* cards */
.sw-card{background:var(--sw-surface);border:1px solid var(--sw-line);border-radius:12px;
  box-shadow:var(--sw-shadow)}
.sw-card-h{padding:14px 16px;border-bottom:1px solid var(--sw-line2);display:flex;
  align-items:center;gap:10px;flex-wrap:wrap}
.sw-card-h h3{margin:0;font-size:12px;letter-spacing:.03em}
.sw-card-h small{color:var(--sw-ink2);font-size:10px;display:block;margin-top:2px}
.sw-card-b{padding:16px}
.sw-mt{margin-top:16px}

.sw-chip{border-radius:999px;padding:4px 8px;font-size:9px;font-weight:700;
  border:1px solid var(--sw-neu-bd);background:var(--sw-neu-bg);color:var(--sw-neu-fg);display:inline-block;white-space:nowrap}
.sw-chip.sw-green{color:var(--sw-ok-fg);background:var(--sw-ok-bg);border-color:var(--sw-ok-bd)}
.sw-chip.sw-amber{color:var(--sw-warn-fg);background:var(--sw-warn-bg);border-color:var(--sw-warn-bd)}
.sw-chip.sw-red{color:var(--sw-bad-fg);background:var(--sw-bad-bg);border-color:var(--sw-bad-bd)}
.sw-chip.sw-blue{color:var(--sw-info-fg);background:var(--sw-info-bg);border-color:var(--sw-info-bd)}
.sw-chips{display:flex;gap:6px;flex-wrap:wrap}

.sw-metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:0 0 16px}
.sw-metric{background:var(--sw-surface);border:1px solid var(--sw-line);border-radius:10px;padding:13px}
.sw-metric span{display:block;font-size:9px;color:var(--sw-ink2);letter-spacing:.05em}
.sw-metric b{display:block;font-size:22px;margin-top:5px}
.sw-metric small{font-size:9px;color:var(--sw-ink3)}
.sw-metric.sw-attn{border-left:3px solid var(--sw-amber)}
.sw-metric-link{cursor:pointer;text-align:left;width:100%;
  transition:border-color .1s,box-shadow .1s}
.sw-metric-link:hover{border-color:var(--sw-teal);
  box-shadow:0 0 0 3px var(--sw-accent-bg)}

.sw-grid2{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}
.sw-grid-even{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.sw-row{display:grid;grid-template-columns:1fr auto;gap:10px;padding:12px 14px;
  border-bottom:1px solid var(--sw-line2);align-items:center}
.sw-row:last-child{border-bottom:0}
.sw-row b{font-size:11px;display:block}
.sw-row p{font-size:9px;color:var(--sw-ink2);margin:3px 0 0}
.sw-rowlink{cursor:pointer;background:none;border:0;text-align:left;padding:0;width:100%}
.sw-rowlink:hover b{color:var(--sw-teal-deep);text-decoration:underline}
.sw-actions{display:flex;gap:5px;align-items:center;flex-wrap:wrap}

/* pipeline board */
.sw-pipeline{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(200px,1fr);
  gap:10px;overflow-x:auto;padding-bottom:10px;align-items:start}
.sw-stage{min-width:200px}
.sw-stage-h{display:flex;justify-content:space-between;align-items:center;padding:9px 10px;
  font-size:10px;font-weight:800;color:var(--sw-ink2);letter-spacing:.04em}
.sw-stage-h span{background:var(--sw-neu-bd);border-radius:20px;padding:2px 7px}
.sw-deal{background:var(--sw-surface);border:1px solid var(--sw-line);border-radius:10px;padding:11px;
  margin-bottom:9px;box-shadow:var(--sw-shadow);cursor:pointer;
  width:100%;text-align:left;display:block}
.sw-deal:hover{border-color:var(--sw-line-strong);box-shadow:var(--sw-shadow-lift)}
.sw-deal .sw-company{font-size:11px;font-weight:800}
.sw-deal .sw-contact{font-size:9px;color:var(--sw-ink3);margin-top:3px}
.sw-deal .sw-value{font-size:10px;font-weight:800;margin-top:9px}
.sw-deal footer{display:flex;justify-content:space-between;align-items:center;
  margin-top:9px;border-top:1px solid var(--sw-line2);padding-top:8px;gap:6px}
.sw-deal footer small{font-size:8px;color:var(--sw-ink3)}
.sw-deal.sw-hot{border-top:3px solid var(--sw-teal)}
.sw-deal.sw-warn{border-top:3px solid var(--sw-amber)}
.sw-stage-empty{border:1px dashed var(--sw-line);border-radius:10px;padding:14px;text-align:center;
  font-size:9px;color:var(--sw-ink4);background:var(--sw-surface2)}

/* record */
.sw-head{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:start}
.sw-head h2{font-size:22px;margin:0}
.sw-head p{font-size:11px;color:var(--sw-ink2);margin:5px 0 0}
.sw-infogrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.sw-info{padding:11px;border:1px solid var(--sw-line);background:var(--sw-surface2);border-radius:8px}
.sw-info span{display:block;font-size:8px;color:var(--sw-ink3);letter-spacing:.06em}
.sw-info b{display:block;font-size:11px;margin-top:4px;word-break:break-word}

.sw-timeline{position:relative;padding-left:18px}
.sw-timeline:before{content:"";position:absolute;top:4px;bottom:4px;left:5px;width:1px;background:var(--sw-line)}
.sw-event{position:relative;margin-bottom:14px}
.sw-event:last-child{margin-bottom:0}
.sw-event:before{content:"";position:absolute;left:-17px;top:3px;width:8px;height:8px;
  background:var(--sw-surface);border:2px solid var(--sw-teal);border-radius:50%}
.sw-event b{font-size:10px}
.sw-event p{font-size:9px;color:var(--sw-ink2);margin:3px 0 0}

.sw-life{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.sw-life-step{border:1px solid var(--sw-line);border-radius:9px;padding:10px;background:var(--sw-surface2)}
.sw-life-step b{font-size:9px;display:block;letter-spacing:.04em}
.sw-life-step small{display:block;font-size:8px;color:var(--sw-ink3);margin-top:4px}
.sw-life-step.sw-done{border-top:3px solid var(--sw-green);background:var(--sw-accent-bg)}
.sw-life-step.sw-current{border-top:3px solid var(--sw-teal);background:var(--sw-accent-bg)}
.sw-life-step.sw-pending{border-top:3px solid var(--sw-line)}

/* states */
.sw-empty{padding:34px 20px;text-align:center;color:var(--sw-ink3)}
.sw-empty b{display:block;font-size:12px;color:var(--sw-ink2)}
.sw-empty p{font-size:10px;margin:6px auto 0;max-width:420px;line-height:1.6}
.sw-notbuilt{border:1px dashed var(--sw-line);background:var(--sw-surface2);border-radius:10px;padding:16px}
.sw-notbuilt b{font-size:10px;color:var(--sw-ink2);letter-spacing:.08em;display:block}
.sw-notbuilt p{font-size:10px;color:var(--sw-ink3);margin:6px 0 0;line-height:1.6}
.sw-err{border:1px solid var(--sw-bad-bd);background:var(--sw-bad-bg);color:var(--sw-bad-fg);border-radius:9px;
  padding:12px 14px;font-size:11px;margin-bottom:14px}
.sw-subtle{font-size:9px;color:var(--sw-ink3)}
.sw-flex{display:flex;align-items:center;gap:8px}
.sw-between{justify-content:space-between}

.sw-modal-back{position:fixed;inset:0;background:rgba(10,20,32,.55);z-index:60;
  display:flex;align-items:flex-start;justify-content:center;padding:60px 20px;overflow-y:auto}
.sw-modal{background:var(--sw-surface);border-radius:14px;width:100%;max-width:560px;
  box-shadow:var(--sw-shadow-modal)}

@media(max-width:1240px){
  .sw-metrics{grid-template-columns:repeat(3,1fr)}
  .sw-grid2,.sw-grid-even{grid-template-columns:1fr}
  .sw-infogrid{grid-template-columns:repeat(2,1fr)}
  .sw-life{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:820px){
  .sw-app{grid-template-columns:1fr}
  .sw-sidebar{position:static;height:auto}
  .sw-metrics{grid-template-columns:repeat(2,1fr)}
  .sw-infogrid{grid-template-columns:1fr}
}

/* ── Team Command (Checkpoint 5) ─────────────────────────────────────────────
   Built to collapse to one column on a phone rather than to be redesigned for
   one. A manager approving a discount from a car park is the realistic case;
   a horizontally-scrolling approval button is not a manager tool. */
.sw-scope .sw-muted{color:var(--sw-ink2);font-size:12px}
.sw-scope .sw-pad{padding:8px 2px}
.sw-scope .sw-note{background:var(--sw-accent-bg);border:1px solid var(--sw-accent-bd);color:#0f7e73;
  padding:10px 12px;border-radius:8px;margin-bottom:14px;font-size:13px}
.sw-scope .sw-btn.sw-ghost{background:transparent;border-color:transparent;
  color:var(--sw-ink2)}
.sw-scope .sw-btn.sw-ghost:hover{border-color:var(--sw-line);color:var(--sw-ink)}
.sw-scope .sw-two{display:grid;grid-template-columns:1.35fr 1fr;gap:16px;
  align-items:start}

.sw-scope .sw-appr{border:1px solid var(--sw-line);border-radius:10px;
  padding:12px;margin-bottom:10px;background:var(--sw-appr-bg)}
.sw-scope .sw-appr-head{display:flex;justify-content:space-between;
  align-items:center;gap:10px;margin-bottom:8px}
.sw-scope .sw-appr-money{display:grid;grid-template-columns:repeat(3,1fr);
  gap:8px;margin-bottom:8px}
.sw-scope .sw-quote{margin:0 0 10px;padding:8px 12px;border-left:3px solid var(--sw-teal);
  background:var(--sw-surface3);color:var(--sw-ink);font-size:13px}
.sw-scope .sw-appr-act{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.sw-scope .sw-appr-act .sw-input{flex:1 1 220px;min-width:0}

.sw-scope .sw-attgroup{margin-bottom:14px}
.sw-scope .sw-attgroup-h{display:flex;align-items:center;gap:8px;
  font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--sw-ink2);margin:0 0 6px;padding-bottom:4px;
  border-bottom:1px solid var(--sw-line2)}
.sw-scope .sw-attrow{display:flex;gap:10px;align-items:flex-start;
  justify-content:space-between;padding:8px 0;
  border-bottom:1px solid var(--sw-line2)}
.sw-scope .sw-attrow:last-child{border-bottom:none}
.sw-scope .sw-attrow-main{min-width:0}
.sw-scope .sw-attrow-t{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sw-scope .sw-attrow-meta{font-size:12px;color:var(--sw-ink2);margin-top:3px}
.sw-scope .sw-do{color:var(--sw-teal-deep);font-weight:600}

.sw-scope .sw-tablewrap{overflow-x:auto}
.sw-scope .sw-table{width:100%;border-collapse:collapse;font-size:13px}
.sw-scope .sw-table th{text-align:left;font-size:11px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--sw-ink2);font-weight:600;
  padding:6px 8px;border-bottom:1px solid var(--sw-line)}
.sw-scope .sw-table td{padding:8px;vertical-align:top;
  border-bottom:1px solid var(--sw-line2)}

.sw-scope .sw-person{border-bottom:1px solid var(--sw-line2);padding:8px 0}
.sw-scope .sw-person:last-child{border-bottom:none}
.sw-scope .sw-person-h,.sw-scope .sw-rep-h{display:flex;align-items:center;
  gap:10px;margin-bottom:6px}
.sw-scope .sw-person-h small,.sw-scope .sw-rep-h small{display:block;
  color:var(--sw-ink2);font-size:11px}
.sw-scope .sw-person-h .sw-count,.sw-scope .sw-rep-h .sw-chip{margin-left:auto}
.sw-scope .sw-meet{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  padding:4px 0 4px 42px;font-size:13px}
.sw-scope .sw-meet-t{font-variant-numeric:tabular-nums;font-weight:600;
  min-width:66px}

.sw-scope .sw-rep{border-bottom:1px solid var(--sw-line2);padding:10px 0}
.sw-scope .sw-rep:last-child{border-bottom:none}
.sw-scope .sw-rep-h{cursor:pointer}
.sw-scope .sw-rep-id{min-width:0}
.sw-scope .sw-rep-nums{display:grid;grid-template-columns:repeat(3,1fr);
  gap:6px;margin:6px 0}
.sw-scope .sw-rep-foot{display:flex;justify-content:space-between;
  align-items:center;gap:8px;flex-wrap:wrap}
.sw-scope .sw-repdrill{margin-top:8px;border-top:1px dashed var(--sw-line);
  padding-top:8px}
.sw-scope .sw-drillrow{display:flex;justify-content:space-between;gap:10px;
  align-items:center;padding:6px 0;cursor:pointer;
  border-bottom:1px solid var(--sw-line2)}
.sw-scope .sw-drillrow:last-child{border-bottom:none}
.sw-scope .sw-drillrow:hover{background:var(--sw-surface3)}
.sw-scope .sw-drillmeta{display:flex;gap:8px;align-items:center;flex-shrink:0}
.sw-scope .sw-decided{display:flex;gap:8px;align-items:center;flex-wrap:wrap;
  padding:5px 0;font-size:13px;border-bottom:1px solid var(--sw-line2)}
.sw-scope .sw-decided:last-child{border-bottom:none}

/* ── Sales Workspace completion ──────────────────────────────────────────────
   Team Calendar, Demos / Proposals, Salespeople, Prospects and the reassign
   control. Everything below reuses the existing palette and card vocabulary;
   nothing here introduces a second visual language for the same workspace. */

/* the "— as a seller" / "— as a manager" qualifier on the nav group headings.
   A manager is both, and the sidebar should say so rather than leave them
   wondering why a pipeline appears in two places. */
.sw-scope .sw-navhint{color:#42596e;letter-spacing:0;font-size:8px}

/* day strip — Team Calendar */
.sw-scope .sw-daystrip{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
.sw-scope .sw-daybtn{border:1px solid var(--sw-line);background:var(--sw-btn-bg);border-radius:9px;
  padding:8px 10px;min-width:74px;cursor:pointer;text-align:center;flex:0 0 auto}
.sw-scope .sw-daybtn:hover{border-color:var(--sw-line-strong)}
.sw-scope .sw-daybtn.sw-on{border-color:var(--sw-teal);background:var(--sw-accent-bg);
  box-shadow:inset 0 -3px var(--sw-teal)}
.sw-scope .sw-daybtn b{display:block;font-size:13px}
.sw-scope .sw-daybtn small{display:block;font-size:9px;color:var(--sw-ink2);margin-top:2px}
.sw-scope .sw-daybtn .sw-dot{display:inline-block;width:5px;height:5px;border-radius:50%;
  background:var(--sw-teal);margin-top:4px}
.sw-scope .sw-daybtn .sw-dot.sw-warn{background:var(--sw-amber)}

/* one meeting row on Team Calendar */
.sw-scope .sw-cal{display:grid;grid-template-columns:84px 1fr auto;gap:12px;
  padding:11px 0;border-bottom:1px solid var(--sw-line2);align-items:start}
.sw-scope .sw-cal:last-child{border-bottom:none}
.sw-scope .sw-cal-when b{display:block;font-size:12px;font-variant-numeric:tabular-nums}
.sw-scope .sw-cal-when small{display:block;font-size:9px;color:var(--sw-ink3);margin-top:2px}
.sw-scope .sw-cal-main{min-width:0}
.sw-scope .sw-cal-main b{font-size:12px}
.sw-scope .sw-cal-meta{font-size:11px;color:var(--sw-ink2);margin-top:3px}
.sw-scope .sw-cal-parts{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.sw-scope .sw-part{border:1px solid var(--sw-line);border-radius:20px;padding:2px 8px;
  font-size:9px;background:var(--sw-surface2);color:var(--sw-ink2);white-space:nowrap}
.sw-scope .sw-part.sw-req{border-color:var(--sw-info-bd);background:var(--sw-info-bg);color:var(--sw-info-fg)}
.sw-scope .sw-part.sw-bad{border-color:var(--sw-bad-bd);background:var(--sw-bad-bg);color:var(--sw-bad-fg)}
.sw-scope .sw-cal-act{display:flex;gap:6px;align-items:center;flex-shrink:0;flex-wrap:wrap}

/* queue columns — Demos / Proposals */
.sw-scope .sw-queues{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;
  align-items:start}
.sw-scope .sw-qrow{display:flex;justify-content:space-between;gap:8px;
  align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--sw-line2);
  cursor:pointer;width:100%;background:none;border-left:0;border-right:0;
  border-top:0;text-align:left}
.sw-scope .sw-qrow:last-child{border-bottom:none}
.sw-scope .sw-qrow:hover{background:var(--sw-surface3)}
.sw-scope .sw-qrow b{font-size:11px;display:block}
.sw-scope .sw-qrow .sw-why{font-size:10px;color:var(--sw-ink2);margin-top:3px}
.sw-scope .sw-qrow .sw-who{font-size:9px;color:var(--sw-ink3);margin-top:3px;display:block}

/* people grid — Salespeople */
.sw-scope .sw-people{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.sw-scope .sw-pcard{border:1px solid var(--sw-line);border-radius:11px;background:var(--sw-surface);
  padding:14px}
.sw-scope .sw-pcard-h{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.sw-scope .sw-pcard-h b{font-size:12px;display:block}
.sw-scope .sw-pcard-h small{display:block;font-size:10px;color:var(--sw-ink2)}
.sw-scope .sw-pnums{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.sw-scope .sw-pfoot{margin-top:10px;padding-top:9px;border-top:1px solid var(--sw-line2);
  display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}

/* filter bar — Prospects and Team Pipeline */
.sw-scope .sw-filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;
  margin-bottom:14px}
.sw-scope .sw-filters .sw-select,.sw-scope .sw-filters .sw-input{width:auto;min-width:150px}

/* the reassign control */
.sw-scope .sw-reassign{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sw-scope .sw-reassign .sw-select{min-width:190px;width:auto}

@media(max-width:1240px){
  .sw-two{grid-template-columns:1fr}
  .sw-rep-nums{grid-template-columns:repeat(2,1fr)}
  .sw-queues{grid-template-columns:repeat(2,1fr)}
  .sw-people{grid-template-columns:1fr}
}
@media(max-width:900px){
  .sw-queues{grid-template-columns:1fr}
  .sw-cal{grid-template-columns:1fr;gap:6px}
  .sw-cal-act{justify-content:flex-start}
}
@media(max-width:820px){
  .sw-appr-money{grid-template-columns:1fr}
  .sw-appr-act{flex-direction:column;align-items:stretch}
  .sw-appr-act .sw-input{flex:1 1 auto}
  .sw-attrow{flex-direction:column;align-items:stretch}
  .sw-attrow .sw-btn{align-self:flex-start}
  .sw-meet{padding-left:0}
  .sw-rep-nums{grid-template-columns:repeat(2,1fr)}
}

/* ── Billing options ────────────────────────────────────────────────────────
   Two cards, not a dropdown. The month-to-month rate is the package's normal
   price and is rendered at the same size and weight as the contracted one, so
   neither can be mistaken for "the" price. The saving badge is the only thing
   that draws the eye to the agreement, and it is earned. */
.sw-billing-options{display:flex;align-items:stretch;gap:12px;flex-wrap:wrap}
.sw-billing-options > div{display:flex;align-items:stretch;gap:12px;flex:1;min-width:220px}
.sw-billing-or{display:flex;align-items:center;font-size:9px;letter-spacing:.14em;
  color:var(--sw-ink3);font-weight:700}
.sw-billing-card{flex:1;min-width:200px;text-align:left;cursor:pointer;
  border:1.5px solid var(--sw-line);background:var(--sw-surface);border-radius:12px;padding:14px 16px;
  display:flex;flex-direction:column;gap:6px;transition:border-color .12s,box-shadow .12s}
.sw-billing-card:hover:not(:disabled){border-color:var(--sw-line-strong)}
.sw-billing-card:disabled{opacity:.6;cursor:default}
.sw-billing-card.is-active{border-color:var(--sw-info-fg);box-shadow:0 0 0 3px rgba(47,111,176,.12)}
.sw-billing-name{font-size:9px;letter-spacing:.1em;color:var(--sw-ink2);font-weight:700;
  text-transform:uppercase}
.sw-billing-rate{font-size:22px;font-weight:700;color:var(--sw-ink);line-height:1.1}
.sw-billing-rate em{font-style:normal;font-size:11px;font-weight:500;color:var(--sw-ink2);
  margin-left:3px}
.sw-billing-save{align-self:flex-start;font-size:9px;font-weight:700;letter-spacing:.08em;
  color:var(--sw-ok-fg);background:var(--sw-ok-bg);border:1px solid var(--sw-ok-bd);border-radius:20px;
  padding:3px 9px}
.sw-billing-terms{font-size:9px;color:var(--sw-ink3);line-height:1.5}
.sw-billing-setup{display:flex;justify-content:space-between;align-items:center;
  margin-top:12px;padding:10px 14px;border:1px dashed var(--sw-line);border-radius:10px;
  background:var(--sw-surface2);font-size:10px;color:var(--sw-ink2)}
.sw-billing-setup b{font-size:12px;color:var(--sw-ink)}
.sw-billing-setup em{font-style:normal;color:var(--sw-ink3)}
.sw-billing-summary{margin-top:12px;border:1px solid var(--sw-line2);border-radius:10px;
  padding:4px 14px;background:var(--sw-surface2)}
.sw-billing-row{display:flex;justify-content:space-between;align-items:center;
  padding:8px 0;font-size:10px;color:var(--sw-ink2);border-bottom:1px solid var(--sw-line2)}
.sw-billing-row:last-child{border-bottom:none}
.sw-billing-row b{font-size:11px;color:var(--sw-ink)}
.sw-billing-row.is-primary{padding:12px 0}
.sw-billing-row.is-primary span{font-size:9px;letter-spacing:.1em;font-weight:700;
  color:var(--sw-ink)}
.sw-billing-row.is-primary b{font-size:18px}

/* STANDARD PACKAGE vs CUSTOM DEAL — the mode badge on the pricing card.
   Two visibly different states, because a salesperson should never have to
   infer which one they are selling from whether a field happens to be filled
   in. Custom is warmer and heavier: it is the exception, and the exception is
   the one worth noticing. */
.sw-pill{display:inline-block;padding:3px 9px;border-radius:999px;
  font-size:9.5px;font-weight:800;letter-spacing:.1em;
  background:var(--sw-neu-bg);color:var(--sw-neu-fg);border:1px solid var(--sw-neu-bd)}
.sw-pill.is-custom{background:var(--sw-warn-bg);color:var(--sw-warn-fg);border-color:var(--sw-warn-bd)}

/* ── COMPENSATION COMMAND CENTER ─────────────────────────────────────────────
   THE PROBLEM THIS SOLVES. The previous screen drew every figure as a plain
   card at the same size, in the same colour, on one flat row: five identical
   rectangles, 9px labels, and no way to tell at a glance which number was a
   forecast and which was a liability. On the dark God Mode shell it was worse
   — the cards were literally white slabs on navy.

   THE HIERARCHY IS NOW STRUCTURAL, NOT DECORATIVE.

     PAYABLE NOW is the largest figure on the page, because it is the only one
     anybody acts on today. It is the only tile that carries the accent.

     PROJECTED is set apart before the ledger figures and drawn with a DASHED
     border and muted ink. A dashed edge reads as provisional in a way no
     caption can, and this is the one number on the page owed to nobody.

     EARNED / ON HOLD / PAID are peers, sized below payable. Each carries a
     coloured top rule in its own state's hue, so the four ledger buckets are
     distinguishable without reading the labels.

   NUMBERS ARE TABULAR AND BIG ENOUGH TO READ. 9px financial figures were the
   single most-cited complaint about the old screen. The tile value is 30px,
   the ledger 13px, and every column of digits is tabular-nums so the decimal
   points line up down the page.

   EVERY TILE IS A BUTTON and filters the ledger below. That is not a
   flourish — it is what makes "PAYABLE NOW = $8,450" provable on the same
   screen instead of taken on trust. */

.sw-cc-title{font-size:19px;font-weight:700;letter-spacing:-.01em;margin:0}
.sw-cc-sub{font-size:12px;color:var(--sw-ink2);margin:4px 0 0;max-width:62ch;
  line-height:1.55}
.sw-cc-band{display:flex;align-items:flex-start;justify-content:space-between;
  gap:16px;flex-wrap:wrap;margin-bottom:18px}

/* the forecast, held apart from the ledger figures */
.sw-cc-forecast{display:grid;grid-template-columns:minmax(200px,260px) 1fr;
  gap:14px;align-items:stretch;margin-bottom:14px}
.sw-cc-forecast-note{display:flex;align-items:center;font-size:11.5px;
  color:var(--sw-ink2);line-height:1.6;padding:2px 2px 2px 0;max-width:70ch}

.sw-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.sw-tile{position:relative;text-align:left;width:100%;background:var(--sw-surface);
  border:1px solid var(--sw-line);border-radius:12px;padding:15px 16px 14px;
  box-shadow:var(--sw-shadow);cursor:pointer;overflow:hidden;
  transition:border-color .12s,box-shadow .12s,transform .12s}
.sw-tile:hover{border-color:var(--sw-line-strong);box-shadow:var(--sw-shadow-lift)}
.sw-tile:active{transform:translateY(1px)}
.sw-tile[disabled],.sw-tile.is-static{cursor:default;transform:none}
.sw-tile[disabled]:hover,.sw-tile.is-static:hover{border-color:var(--sw-line);
  box-shadow:var(--sw-shadow)}
/* the state rule — 3px of colour that says which bucket this is */
.sw-tile:before{content:"";position:absolute;inset:0 0 auto 0;height:3px;
  background:var(--sw-tile-rule,transparent)}
.sw-tile.t-earned{--sw-tile-rule:var(--sw-blue)}
.sw-tile.t-hold{--sw-tile-rule:var(--sw-amber)}
.sw-tile.t-payable{--sw-tile-rule:var(--sw-teal)}
.sw-tile.t-paid{--sw-tile-rule:var(--sw-neu-bd)}
.sw-tile.is-on{border-color:var(--sw-teal);
  box-shadow:0 0 0 3px var(--sw-accent-bg),var(--sw-shadow)}
.sw-tile-label{font-size:10px;font-weight:800;letter-spacing:.11em;
  color:var(--sw-ink2);text-transform:uppercase;display:block}
.sw-tile-value{font-size:30px;font-weight:700;line-height:1.05;margin-top:7px;
  letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--sw-ink)}
.sw-tile-sub{font-size:11px;color:var(--sw-ink3);margin-top:5px;line-height:1.45}
/* PAYABLE NOW is the only figure anybody acts on today, so it is the only one
   that gets extra weight and the accent. */
.sw-tile.is-lead .sw-tile-value{font-size:34px;color:var(--sw-teal-deep)}
[data-appearance="dark"] .sw-scope .sw-tile.is-lead .sw-tile-value{color:var(--sw-teal2)}
/* A FORECAST LOOKS LIKE A FORECAST. Dashed, muted, no shadow: it is not
   sitting on the ledger with the others. */
.sw-tile.is-forecast{border-style:dashed;background:transparent;box-shadow:none;
  cursor:default}
.sw-tile.is-forecast:hover{border-color:var(--sw-line);box-shadow:none}
.sw-tile.is-forecast .sw-tile-value{color:var(--sw-ink2);font-size:26px}

/* BECOMING PAYABLE — three windows, read left to right as time passes */
.sw-becoming{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;
  background:var(--sw-line2);border:1px solid var(--sw-line);border-radius:10px;
  overflow:hidden;margin-top:12px}
.sw-becoming-cell{background:var(--sw-surface);padding:12px 14px}
.sw-becoming-cell span{display:block;font-size:10px;font-weight:800;
  letter-spacing:.09em;color:var(--sw-ink2);text-transform:uppercase}
.sw-becoming-cell b{display:block;font-size:19px;font-weight:700;margin-top:5px;
  font-variant-numeric:tabular-nums}
.sw-becoming-cell small{display:block;font-size:11px;color:var(--sw-ink3);
  margin-top:2px}

/* ATTENTION REQUIRED — worst first, each row saying what and what to do */
.sw-att{display:flex;flex-direction:column;gap:10px;margin-top:12px}
.sw-att-row{display:grid;grid-template-columns:3px 1fr auto;gap:14px;
  align-items:start;border:1px solid var(--sw-line);border-radius:10px;
  background:var(--sw-surface2);padding:0;overflow:hidden}
.sw-att-bar{background:var(--sw-neu-bd);align-self:stretch}
.sw-att-row.sv-action_required .sw-att-bar{background:var(--sw-red)}
.sw-att-row.sv-attention .sw-att-bar{background:var(--sw-amber)}
.sw-att-body{padding:12px 0 12px 0;min-width:0}
.sw-att-side{padding:12px 14px 12px 0;display:flex;align-items:center;gap:8px;
  flex-shrink:0}
.sw-att-t{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.sw-att-t b{font-size:13px;font-weight:650;line-height:1.35}
.sw-att-d{font-size:11.5px;color:var(--sw-ink2);margin:5px 0 0;line-height:1.6;
  max-width:78ch}

/* the severity pill — one vocabulary, served by the server */
.sw-sev{display:inline-block;border-radius:999px;padding:2px 9px;font-size:9.5px;
  font-weight:800;letter-spacing:.09em;text-transform:uppercase;white-space:nowrap;
  border:1px solid var(--sw-neu-bd);background:var(--sw-neu-bg);
  color:var(--sw-neu-fg)}
.sw-sev.sv-healthy{background:var(--sw-ok-bg);border-color:var(--sw-ok-bd);
  color:var(--sw-ok-fg)}
.sw-sev.sv-attention{background:var(--sw-warn-bg);border-color:var(--sw-warn-bd);
  color:var(--sw-warn-fg)}
.sw-sev.sv-action_required{background:var(--sw-bad-bg);border-color:var(--sw-bad-bd);
  color:var(--sw-bad-fg)}
.sw-sev.sv-unavailable{background:var(--sw-neu-bg);border-color:var(--sw-neu-bd);
  color:var(--sw-ink3)}
.sw-sev.sv-no_data{background:transparent;border-style:dashed;
  border-color:var(--sw-line);color:var(--sw-ink3)}

/* NOTHING OUTSTANDING is a real answer and gets a real state, not a blank
   panel. A finance screen that can say "there is nothing to do" is more
   trustworthy than one that only ever shows problems. */
.sw-allclear{display:flex;align-items:center;gap:11px;padding:14px 16px;
  border:1px solid var(--sw-ok-bd);background:var(--sw-ok-bg);border-radius:10px;
  margin-top:12px}
.sw-allclear b{font-size:12.5px;color:var(--sw-ok-fg);display:block}
.sw-allclear p{font-size:11.5px;color:var(--sw-ink2);margin:3px 0 0;line-height:1.55}

/* THE LEDGER */
.sw-ledger-h{display:flex;align-items:center;justify-content:space-between;
  gap:12px;flex-wrap:wrap;margin-bottom:12px}
.sw-ledger-h h3{margin:0;font-size:13px;font-weight:700}
.sw-ledger-h .sw-proof{font-size:11.5px;color:var(--sw-ink2);
  font-variant-numeric:tabular-nums}
.sw-scope .sw-table td.sw-num,.sw-scope .sw-table th.sw-num{text-align:right;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.sw-scope .sw-table tbody tr:hover{background:var(--sw-surface3)}
.sw-scope .sw-table thead th{position:sticky;top:0;background:var(--sw-surface);
  z-index:1}
.sw-cap{font-size:10.5px;color:var(--sw-warn-fg);display:block;margin-top:2px}

/* A BIG, HONEST EMPTY STATE. The old one was a 9px grey sentence that looked
   like a loading glitch. */
.sw-blank{text-align:center;padding:38px 22px}
.sw-blank b{display:block;font-size:14px;color:var(--sw-ink);font-weight:650}
.sw-blank p{font-size:12px;color:var(--sw-ink2);margin:8px auto 0;max-width:52ch;
  line-height:1.65}

/* ── THE SELLER'S OPPORTUNITY WORKSPACE ──────────────────────────────────────
   WHAT THIS REPLACES. The Opportunity page used to render every panel it had,
   open, at full height: fourteen empty discovery textareas, a five-field demo
   work order, pricing, proposal, closing and billing, in one column. It was
   several screens of empty boxes with no answer to "what do I do next".

   THE RULES HERE ARE THE FIX.
   - The command area at the top answers who / where / what next before any
     scrolling, and carries exactly ONE primary button.
   - A stage is a SECTION with a state. Done collapses to a summary line, the
     current one is open, the ones ahead are closed but never locked.
   - A choice is a chip, not a textarea. Free text is one line, and on most
     questions it is not on screen until somebody asks for it.
   Nothing below introduces a second visual language: every colour is an
   existing token and every control reuses the workspace's own vocabulary. */

/* the command area */
.sw-cmd{background:var(--sw-surface);border:1px solid var(--sw-line);
  border-radius:12px;box-shadow:var(--sw-shadow);padding:16px 18px}
.sw-cmd-top{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;
  justify-content:space-between}
.sw-cmd-id{min-width:0;flex:1 1 320px}
.sw-cmd-id h2{font-size:20px;margin:0;letter-spacing:-.01em}
.sw-cmd-id p{font-size:11px;color:var(--sw-ink2);margin:4px 0 0}
.sw-cmd-act{display:flex;gap:8px;align-items:center;flex-wrap:wrap;
  flex:0 0 auto}
.sw-cmd-act .sw-select{width:auto}
.sw-cmd-alerts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.sw-alert{font-size:10.5px;font-weight:700;border-radius:8px;padding:6px 10px;
  border:1px solid var(--sw-warn-bd);background:var(--sw-warn-bg);
  color:var(--sw-warn-fg)}
.sw-alert.sw-red{border-color:var(--sw-bad-bd);background:var(--sw-bad-bg);
  color:var(--sw-bad-fg)}
.sw-cmd-facts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;
  margin-top:14px;padding-top:12px;border-top:1px solid var(--sw-line2)}
.sw-cmd-fact span{display:block;font-size:8.5px;letter-spacing:.08em;
  color:var(--sw-ink3);font-weight:800}
.sw-cmd-fact b{display:block;font-size:12px;margin-top:3px;word-break:break-word}
.sw-cmd-fact.is-strong b{color:var(--sw-teal-deep)}
[data-appearance="dark"] .sw-scope .sw-cmd-fact.is-strong b{color:var(--sw-teal2)}

/* a stage, as a section that knows where the deal is */
.sw-sec{background:var(--sw-surface);border:1px solid var(--sw-line);
  border-radius:12px;box-shadow:var(--sw-shadow);margin-bottom:12px;
  overflow:hidden}
.sw-sec-h{display:flex;align-items:center;gap:12px;width:100%;
  background:transparent;border:0;padding:13px 16px;cursor:pointer;
  text-align:left;color:inherit}
.sw-sec-h:hover{background:var(--sw-surface3)}
.sw-sec-n{flex:0 0 auto;width:22px;height:22px;border-radius:50%;
  display:grid;place-items:center;font-size:10px;font-weight:800;
  background:var(--sw-neu-bg);color:var(--sw-neu-fg);
  border:1px solid var(--sw-neu-bd)}
.sw-sec-t{min-width:0;flex:1}
.sw-sec-t b{display:block;font-size:12px;letter-spacing:.03em}
.sw-sec-t small{display:block;font-size:10px;color:var(--sw-ink2);margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sw-sec-caret{flex:0 0 auto;font-size:11px;color:var(--sw-ink3)}
.sw-sec-b{padding:0 14px 14px}
.sw-sec.is-done{border-left:3px solid var(--sw-green)}
.sw-sec.is-done .sw-sec-n{background:var(--sw-ok-bg);color:var(--sw-ok-fg);
  border-color:var(--sw-ok-bd)}
.sw-sec.is-current{border-left:3px solid var(--sw-teal)}
.sw-sec.is-current .sw-sec-n{background:var(--sw-accent-bg);
  color:var(--sw-teal-deep);border-color:var(--sw-accent-bd)}
.sw-sec.is-upcoming{border-left:3px solid var(--sw-line)}
.sw-sec.is-upcoming .sw-sec-h{opacity:.78}
.sw-sec.is-upcoming.is-open .sw-sec-h{opacity:1}
/* A section's own card must not draw a second frame inside this one. */
.sw-sec-b > .sw-card{border:0;box-shadow:none;border-radius:0}
.sw-sec-b > .sw-card > .sw-card-h{padding-left:0;padding-right:0}
.sw-sec-b > .sw-card > .sw-card-b{padding-left:0;padding-right:0;padding-bottom:0}

/* one disclosure, used everywhere something is real but not wanted yet */
.sw-disclose{margin-top:14px;border-top:1px solid var(--sw-line2);padding-top:10px}
.sw-disclose > summary{cursor:pointer;font-size:10px;font-weight:800;
  letter-spacing:.07em;color:var(--sw-ink2);text-transform:uppercase;
  list-style:none}
.sw-disclose > summary::-webkit-details-marker{display:none}
.sw-disclose > summary:before{content:"▸ ";color:var(--sw-ink3)}
.sw-disclose[open] > summary:before{content:"▾ "}
.sw-disclose.is-flush{margin:0;padding:12px 16px;border-top:1px solid var(--sw-line2)}
.sw-disclose.is-flush:first-child{border-top:0}

/* progress on discovery */
.sw-prog{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.sw-prog-bar{flex:1;height:5px;border-radius:99px;background:var(--sw-surface3);
  border:1px solid var(--sw-line2);overflow:hidden;min-width:80px}
.sw-prog-fill{height:100%;background:var(--sw-teal);transition:width .2s}
.sw-prog-t{font-size:10px;color:var(--sw-ink2);flex:1 1 200px;min-width:0}

/* a discovery group, and one question inside it */
.sw-dgroup{margin-top:14px}
.sw-dgroup + .sw-dgroup{border-top:1px solid var(--sw-line2);padding-top:6px}
.sw-dgroup-h{font-size:9px;font-weight:800;letter-spacing:.12em;
  text-transform:uppercase;color:var(--sw-ink3);margin:8px 0 2px}
.sw-dq{padding:9px 0;border-bottom:1px solid var(--sw-line2)}
.sw-dq:last-child{border-bottom:0}
.sw-dq-h{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.sw-dq-h label{font-size:10.5px;font-weight:700;color:var(--sw-ink);margin:0}
.sw-dq-req{font-size:8.5px;font-weight:800;letter-spacing:.08em;
  text-transform:uppercase;color:var(--sw-warn-fg);background:var(--sw-warn-bg);
  border:1px solid var(--sw-warn-bd);border-radius:99px;padding:1px 7px}
.sw-dq-ok{margin-left:auto;color:var(--sw-ok-fg);font-size:11px;font-weight:800}

/* THE CHIP THAT REPLACED A TEXTAREA */
.sw-opts{display:flex;flex-wrap:wrap;gap:6px}
.sw-opt{border:1px solid var(--sw-line);background:var(--sw-btn-bg);
  color:var(--sw-ink2);border-radius:99px;padding:6px 11px;font-size:11px;
  font-weight:600;cursor:pointer;line-height:1.2}
.sw-opt:hover:not(:disabled){border-color:var(--sw-line-strong);color:var(--sw-ink)}
.sw-opt.is-on{background:var(--sw-accent-bg);border-color:var(--sw-teal);
  color:var(--sw-teal-deep);font-weight:700}
[data-appearance="dark"] .sw-scope .sw-opt.is-on{color:var(--sw-teal2)}
.sw-opt:disabled{opacity:.55;cursor:default}
.sw-mini-input{margin-top:7px;max-width:420px}
.sw-addnote{margin-top:7px;background:none;border:0;padding:0;cursor:pointer;
  font-size:10px;font-weight:700;color:var(--sw-ink3)}
.sw-addnote:hover{color:var(--sw-teal-deep);text-decoration:underline}

/* the named parts of a composite question */
.sw-parts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 14px}
.sw-parts .sw-dfield label{display:block;font-size:9px;font-weight:800;
  color:var(--sw-ink2);margin-bottom:4px;letter-spacing:.05em}

/* long-form text that predates the structured form — kept, never in the way */
.sw-legacy{margin-top:10px;border-left:2px solid var(--sw-line);padding-left:10px}
.sw-legacy b{font-size:9px;letter-spacing:.06em;color:var(--sw-ink3);
  text-transform:uppercase;display:block}
.sw-legacy pre{margin:4px 0 0;font:inherit;font-size:11px;color:var(--sw-ink2);
  white-space:pre-wrap;word-break:break-word;line-height:1.5}

@media(max-width:1240px){
  .sw-cmd-facts{grid-template-columns:repeat(2,1fr)}
}
@media(max-width:820px){
  .sw-cmd-facts{grid-template-columns:1fr}
  .sw-cmd-act{width:100%}
  .sw-cmd-act .sw-btn,.sw-cmd-act .sw-select{flex:1 1 auto}
  .sw-parts{grid-template-columns:1fr}
  .sw-sec-t small{white-space:normal}
}

@media(max-width:1100px){
  .sw-tiles{grid-template-columns:repeat(2,1fr)}
  .sw-cc-forecast{grid-template-columns:1fr}
}
@media(max-width:700px){
  .sw-tiles{grid-template-columns:1fr}
  .sw-becoming{grid-template-columns:1fr}
  .sw-att-row{grid-template-columns:3px 1fr}
  .sw-att-side{grid-column:2;padding:0 14px 12px 0}
  .sw-tile-value{font-size:27px}
  .sw-tile.is-lead .sw-tile-value{font-size:30px}
}

`

let injected = false

export default function SalesStyles() {
  useEffect(() => {
    if (injected || document.getElementById('sw-styles')) return
    const el = document.createElement('style')
    el.id = 'sw-styles'
    el.textContent = CSS
    document.head.appendChild(el)
    injected = true
  }, [])
  return null
}
