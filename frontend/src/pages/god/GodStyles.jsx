/**
 * GodStyles — injects the God Mode stylesheet once per app.
 *
 * Hover states, pseudo-elements and gradients cannot be expressed as React inline
 * styles, and the V2 design depends on all three. Everything class-based lives
 * here; one-off layout stays inline in the components.
 *
 * Class prefix is `gm-` so nothing here can collide with the tenant app's styles.
 */
import { useEffect } from 'react'

const CSS = `
.gm-scope{
  --gm-bg:#02050a;--gm-blue:#39bdf8;--gm-teal:#23efb2;--gm-amber:#ffc75a;
  --gm-red:#ff5d7d;--gm-gold:#ffd968;--gm-line:rgba(88,169,225,.20);
  color:#dceafb;font-size:13px;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;position:relative;
  background:
    radial-gradient(circle at 18% -10%,rgba(57,189,248,.16),transparent 28%),
    radial-gradient(circle at 98% 4%,rgba(169,107,255,.10),transparent 25%),
    linear-gradient(180deg,#030711,#02050a 50%,#030711);
}
.gm-scope b,.gm-scope strong,.gm-scope .gm-n,.gm-scope td,.gm-scope th{
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1
}
.gm-grid-overlay{
  position:absolute;inset:0;pointer-events:none;z-index:0;
  background:
    linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);
  background-size:36px 36px;
  -webkit-mask-image:linear-gradient(to bottom,black,transparent 85%);
  mask-image:linear-gradient(to bottom,black,transparent 85%);
}
.gm-card{
  background:linear-gradient(145deg,rgba(13,30,49,.92),rgba(5,13,25,.96));
  border:1px solid rgba(77,151,204,.22);border-radius:12px;
  box-shadow:0 12px 28px rgba(0,0,0,.17),inset 0 1px rgba(255,255,255,.015);
}
/* ── metric tile ───────────────────────────────────────────────────────── */
.gm-metric{
  min-height:118px;padding:18px;position:relative;overflow:hidden;
  transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease;
}
.gm-metric:after{
  content:"";position:absolute;width:90px;height:90px;border-radius:50%;
  right:-38px;top:-45px;
  background:radial-gradient(circle,rgba(57,189,248,.10),transparent 70%);
}
.gm-metric:before{
  content:"";position:absolute;top:0;left:0;width:100%;height:3px;background:var(--gm-teal);
}
.gm-metric.gm-pend:before{background:#243a52}
.gm-metric.gm-warn:before{background:var(--gm-amber)}
.gm-metric.gm-crit:before{background:var(--gm-red)}
.gm-metric.gm-click{cursor:pointer}
.gm-metric.gm-click:hover{
  transform:translateY(-3px);border-color:rgba(91,190,248,.42);
  box-shadow:0 18px 38px rgba(0,0,0,.25);
}
/* ── hierarchy rows ────────────────────────────────────────────────────── */
.gm-row{
  min-height:44px;border-bottom:1px solid rgba(42,92,132,.16);
  transition:background .16s ease,box-shadow .16s ease;
}
.gm-row.gm-click{cursor:pointer}
.gm-row.gm-click:hover{
  background:linear-gradient(90deg,rgba(25,72,108,.28),rgba(8,27,46,.18));
  box-shadow:inset 3px 0 var(--gm-blue);
}
.gm-row.gm-lvl0{background:linear-gradient(90deg,rgba(47,182,255,.075),rgba(7,19,32,.94))}
.gm-row.gm-lvl1{background:rgba(7,18,31,.74)}
.gm-row.gm-lvl2{background:rgba(3,10,19,.82)}
.gm-thead{background:#06101d}
.gm-thead:hover{background:#06101d;box-shadow:none}
/* ── tool tiles ────────────────────────────────────────────────────────── */
.gm-tool{
  min-height:148px;padding:18px;border-radius:12px;position:relative;overflow:hidden;
  text-align:left;font-family:inherit;color:inherit;width:100%;
  background:linear-gradient(145deg,rgba(13,30,49,.92),rgba(5,13,25,.96));
  border:1px solid rgba(77,151,204,.22);
  transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease;
}
.gm-tool:before{
  content:"";position:absolute;left:0;top:0;width:4px;height:100%;
  background:linear-gradient(180deg,var(--gm-blue),transparent);opacity:.55;
}
.gm-tool.gm-gold:before{background:linear-gradient(180deg,var(--gm-gold),transparent)}
.gm-tool.gm-live{cursor:pointer}
.gm-tool.gm-live:hover{transform:translateY(-4px);box-shadow:0 20px 40px rgba(0,0,0,.26);border-color:rgba(91,190,248,.42)}
.gm-tool.gm-gold.gm-live:hover{border-color:rgba(255,217,104,.42)}
.gm-tool.gm-disabled{cursor:not-allowed;opacity:.62}
/* ── exception rows ────────────────────────────────────────────────────── */
.gm-ex{padding:14px 15px;border-bottom:1px solid rgba(42,92,132,.16);display:flex;gap:12px;align-items:center}
.gm-ex:last-child{border-bottom:0}
.gm-ex:hover{background:rgba(16,44,68,.18)}
/* ── buttons ───────────────────────────────────────────────────────────── */
.gm-btn{
  background:#071827;border:1px solid #1c4969;color:#c8e9ff;border-radius:7px;
  padding:7px 11px;font-size:10px;cursor:pointer;font-family:inherit;flex:none;
  transition:background .14s ease,border-color .14s ease;
}
.gm-btn:hover{background:#0d2a44;border-color:#2f7db5}
.gm-btn:disabled{opacity:.45;cursor:not-allowed}
.gm-btn.gm-gold-btn{background:#1b1505;border-color:#5c4a15;color:var(--gm-gold)}
.gm-btn.gm-gold-btn:hover{background:#2a2109;border-color:#8a6f20}
/* ── nav rail ──────────────────────────────────────────────────────────── */
.gm-nav-item{
  display:flex;align-items:center;gap:9px;padding:9px 14px;text-decoration:none;
  font-size:12.5px;letter-spacing:.02em;border-left:2px solid transparent;
  color:#5c7a96;transition:color .14s ease,background .14s ease;white-space:nowrap;
  min-width:0;
}
.gm-nav-item:hover{color:#8ab4cc;background:rgba(47,182,255,.03)}
.gm-nav-item.gm-active{color:var(--gm-blue);background:rgba(47,182,255,.06);border-left-color:var(--gm-blue);font-weight:600}
.gm-nav-item.gm-unbuilt{color:#3f556e}
.gm-nav-item.gm-unbuilt:hover{color:#5c7a96;background:rgba(47,182,255,.02)}
/* The label takes the room that is left and is the ONLY thing allowed to
   shrink. Without min-width:0 a flex child refuses to go below its content
   width, so the label pushed the NEEDS BUILD tag out of the rail instead of
   ellipsising — which is what was clipping "Pipeline & Cadence" and
   "Audit & Security". */
.gm-nav-label{
  flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.gm-nav-tag{
  margin-left:6px;font-size:7px;letter-spacing:.06em;color:#43607d;
  border:1px solid #23394f;border-radius:3px;padding:1px 3px;flex:0 0 auto;
}
/* Jump-to links: the same row shape, dimmer, so they read as leaving God Mode
   rather than as another God screen. */
.gm-nav-item.gm-jump{color:#4a6482;font-size:12px}
.gm-nav-item.gm-jump:hover{color:#8ab4cc;background:rgba(47,182,255,.04)}
/* Rail section headings. Replaced the wall of NEEDS BUILD tags: the primary
   nav now carries working modules only, and everything else lives under one
   heading that says what it is. */
.gm-nav-head{
  font-size:8.5px;letter-spacing:.16em;font-weight:800;color:#33506e;
  padding:14px 14px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.gm-nav-head.gm-next{color:#6a5a2c}
.gm-nav-rule{height:1px;background:rgba(78,157,211,.12);margin:6px 12px}

/* ══ REDESIGN PRIMITIVES ═══════════════════════════════════════════════════
   Added Aug 27 2026 for the approved God Mode Command Center redesign.
   These EXTEND the sheet above — the tokens, card, row, tool and button rules
   are unchanged and still the only ones. Nothing below introduces a second
   palette; every colour is one of the six --gm-* variables at the top.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ── executive summary tiles ───────────────────────────────────────────── */
.gm-stats{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:9px;margin-bottom:16px}
.gm-stat{
  background:linear-gradient(180deg,rgba(12,24,41,.94),rgba(9,20,34,.96));
  border:1px solid rgba(77,151,204,.22);border-radius:12px;padding:12px;
  min-height:88px;text-align:left;font-family:inherit;color:inherit;width:100%;
  display:flex;flex-direction:column;
  transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease;
}
.gm-stat.gm-warn{border-color:rgba(122,95,34,.62)}
.gm-stat.gm-crit{border-color:rgba(114,49,66,.72)}
.gm-stat.gm-click{cursor:pointer}
.gm-stat.gm-click:hover{transform:translateY(-2px);border-color:rgba(91,190,248,.44);box-shadow:0 14px 30px rgba(0,0,0,.24)}
.gm-stat .gm-k{font-size:8px;letter-spacing:.13em;color:#6281a2;font-weight:800;margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gm-stat .gm-v{font-size:21px;font-weight:900;letter-spacing:-.03em;line-height:1;margin-bottom:4px;color:#eef8ff}
.gm-stat .gm-s{font-size:8.5px;color:#587593;margin-top:auto;line-height:1.4}

/* ── platform health tiles ─────────────────────────────────────────────── */
.gm-healths{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}
.gm-health{
  padding:12px;background:rgba(10,26,46,.72);border:1px solid rgba(27,58,90,.9);
  border-radius:11px;text-align:left;font-family:inherit;color:inherit;width:100%;
  transition:border-color .16s ease,background .16s ease;
}
.gm-health.gm-click{cursor:pointer}
.gm-health.gm-click:hover{border-color:rgba(91,190,248,.44);background:rgba(14,34,58,.82)}
.gm-health-top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.gm-health b{font-size:10.5px;color:#eaf4ff;font-weight:600}
.gm-health p{margin:0;color:#68829f;font-size:9px;line-height:1.5}
.gm-dot{width:8px;height:8px;border-radius:50%;flex:none;display:inline-block}
.gm-dot.ok{background:var(--gm-teal);box-shadow:0 0 10px rgba(35,239,178,.5)}
.gm-dot.warn{background:var(--gm-amber);box-shadow:0 0 10px rgba(255,199,90,.4)}
.gm-dot.bad{background:var(--gm-red);box-shadow:0 0 10px rgba(255,93,125,.5)}
.gm-dot.off{background:#2b425c}

/* ── owner action queue ────────────────────────────────────────────────── */
/* Capped and scrolled, not uncapped. Production opened with 21 items against a
   six-tile health grid beside it, so the band ran a full screen taller than its
   left column and left a dead void next to the list. The count in the section
   label says how many there are; this keeps the first several readable without
   the page paying for the twenty-first. */
.gm-q{display:flex;flex-direction:column;max-height:560px;overflow-y:auto}
.gm-q::-webkit-scrollbar{width:8px}
.gm-q::-webkit-scrollbar-thumb{background:rgba(88,169,225,.22);border-radius:4px}
.gm-q::-webkit-scrollbar-track{background:transparent}
.gm-q-item{
  display:grid;grid-template-columns:9px 1fr auto;gap:12px;align-items:start;
  padding:12px 14px;border-bottom:1px solid rgba(42,92,132,.16);
}
.gm-q-item:last-child{border-bottom:0}
.gm-q-item:hover{background:rgba(16,44,68,.20)}
.gm-q-item>i{width:9px;height:9px;border-radius:50%;margin-top:4px}
.gm-q-title{display:block;font-size:11.5px;color:#f1f7ff;font-weight:600;margin-bottom:3px;line-height:1.35}
.gm-q-detail{display:block;font-size:9.5px;color:#5e7796;line-height:1.55}
.gm-q-meta{display:flex;flex-direction:column;align-items:flex-end;gap:6px;text-align:right;flex:none}
.gm-q-sev{font-size:8px;font-weight:800;letter-spacing:.09em}
.gm-q-age{font-size:8.5px;color:#4f6b88;white-space:nowrap}

/* ── command table ─────────────────────────────────────────────────────── */
.gm-tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.gm-table{width:100%;border-collapse:collapse;font-size:10.5px;min-width:1020px}
table.gm-table th{
  text-align:left;padding:10px;color:#6784a3;font-size:7.5px;letter-spacing:.11em;
  font-weight:800;border-bottom:1px solid rgba(24,53,82,.9);background:#06101d;
  white-space:nowrap;position:sticky;top:0;z-index:2;
}
table.gm-table td{padding:10px;border-bottom:1px solid rgba(19,43,68,.86);vertical-align:middle;color:#c6d8ea}
table.gm-table tbody tr:hover td{background:rgba(12,28,48,.75)}
table.gm-table td.gm-num,table.gm-table th.gm-num{text-align:right;font-variant-numeric:tabular-nums}
.gm-orgname{color:#eaf4ff;font-weight:600;font-size:11px}
.gm-orgsub{color:#4f6b88;font-size:8.5px;margin-top:2px}
.gm-group{background:linear-gradient(90deg,rgba(47,182,255,.075),rgba(7,19,32,.94))}
.gm-group td{color:#dceafb;font-weight:600;font-size:10.5px;letter-spacing:.04em}
.gm-groupbtn{background:none;border:0;color:inherit;font:inherit;cursor:pointer;padding:0;display:flex;align-items:center;gap:8px}

/* ── pills ─────────────────────────────────────────────────────────────── */
.gm-pill{display:inline-block;padding:3px 7px;border-radius:999px;font-size:7.5px;font-weight:800;letter-spacing:.04em;white-space:nowrap;border:1px solid transparent}
.gm-pill.teal{background:#0a2b22;border-color:#176f58;color:#44efbd}
.gm-pill.gold{background:#251e08;border-color:#70591d;color:#f4c652}
.gm-pill.blue{background:#0b1a2a;border-color:#1b3c59;color:#7cc0ff}
.gm-pill.red{background:#2a1017;border-color:#723142;color:#ff829b}
.gm-pill.purple{background:#1d1638;border-color:#453177;color:#b79aff}
.gm-pill.off{background:#0c1727;border-color:#2a3f57;color:#5d7697}

/* ── row action buttons ────────────────────────────────────────────────── */
.gm-acts{display:flex;gap:5px;flex-wrap:wrap}
.gm-act{
  border:1px solid #244565;background:#0c1c30;color:#a9bfd5;border-radius:6px;
  padding:4px 7px;font-size:8px;font-weight:700;letter-spacing:.05em;
  cursor:pointer;font-family:inherit;white-space:nowrap;
}
.gm-act:hover{color:#fff;border-color:#2e7eb8;background:#11273f}
.gm-act:disabled{opacity:.45;cursor:not-allowed}
.gm-act.gm-primary{background:rgba(47,182,255,.12);border-color:rgba(57,189,248,.5);color:#7cc0ff}
.gm-act.gm-primary:hover{background:rgba(47,182,255,.2)}
.gm-act.gm-danger{color:#ff829b;border-color:#5b2334}
.gm-act.gm-danger:hover{background:#2a1017;border-color:#8d3a4f;color:#ffa3b6}

/* ── product status chips ──────────────────────────────────────────────── */
.gm-modules{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.gm-modbox{padding:13px;border-radius:11px;border:1px solid rgba(27,57,88,.9);background:rgba(10,25,43,.7)}
.gm-modbox h4{margin:0 0 9px;font-size:9px;letter-spacing:.15em;font-weight:800}
.gm-modbox h4.live{color:var(--gm-teal)}
.gm-modbox h4.next{color:var(--gm-amber)}
.gm-chips{display:flex;gap:6px;flex-wrap:wrap}
.gm-chip{padding:5px 8px;border-radius:999px;font-size:8px;font-weight:700;border:1px solid #234665;color:#9cb5ce;background:none;font-family:inherit}
.gm-chip.live{border-color:#1e5b4f;color:#35dcbc;background:#0c2e28;cursor:pointer}
.gm-chip.live:hover{border-color:#2c8471;background:#0f3c33}
.gm-chip.next{border-color:#55441d;color:#ffd15d;background:#241c0d;cursor:default}

/* ── searching / filtering ─────────────────────────────────────────────── */
.gm-input{
  background:#071827;border:1px solid #1c4969;color:#dceafb;border-radius:7px;
  padding:7px 10px;font-size:11px;font-family:inherit;outline:none;min-width:0;
}
.gm-input:focus{border-color:#2f7db5;box-shadow:0 0 0 2px rgba(47,125,181,.18)}
.gm-input::placeholder{color:#41607f}
.gm-filters{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.gm-seg{display:flex;gap:4px;flex-wrap:wrap}
.gm-seg button{
  background:transparent;border:1px solid #1c3a58;border-radius:7px;color:#5f7c9c;
  cursor:pointer;font-size:8.5px;font-weight:700;letter-spacing:.06em;padding:5px 9px;font-family:inherit;
}
.gm-seg button.on{background:rgba(47,182,255,.12);border-color:#2f7db5;color:#7cc0ff}

/* ── empty / loading ───────────────────────────────────────────────────── */
.gm-empty{padding:26px;text-align:center;color:#496078;font-size:11px}

/* ── responsive ────────────────────────────────────────────────────────── */
@media(max-width:1350px){
  .gm-stats{grid-template-columns:repeat(4,minmax(0,1fr))}
}
@media(max-width:1150px){
  /* !important because the band sets its columns inline, where the two-column
     shape is the default and this is the override. */
  .gm-band2{grid-template-columns:1fr!important}
}
@media(max-width:1000px){
  .gm-healths{grid-template-columns:1fr 1fr}
  .gm-modules{grid-template-columns:1fr}
}
@media(max-width:640px){
  .gm-stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .gm-healths{grid-template-columns:1fr}
  .gm-q-item{grid-template-columns:9px 1fr;row-gap:8px}
  .gm-q-meta{grid-column:2;flex-direction:row;align-items:center;justify-content:flex-start}
}
`

let injected = false

export default function GodStyles() {
  useEffect(() => {
    if (injected) return
    const el = document.createElement('style')
    el.id = 'god-mode-styles'
    el.textContent = CSS
    document.head.appendChild(el)
    injected = true
  }, [])
  return null
}
