/**
 * GodStyles — the God Mode component sheet, injected once per app.
 *
 * Class prefix is `gm-` so nothing here can collide with the tenant app's
 * styles, and every colour is a `--gm-*` token from `godTokens.css`.
 *
 * ───────────────────────────────────────────────────────────────────────────
 * EVERY COLOUR IN THIS SHEET IS A TOKEN. THAT IS LOAD-BEARING.
 * ───────────────────────────────────────────────────────────────────────────
 *
 * This file used to carry about 120 literal hex values and 70 rgba() calls of
 * near-black control-room palette. They are gone. Nothing here decides what
 * colour anything is; it decides what ROLE each element plays. Adding a literal
 * back re-breaks the design system for whatever it paints — put it in
 * `godTokens.css` instead.
 *
 * ───────────────────────────────────────────────────────────────────────────
 * WHAT THE SHAPES ARE NOW
 * ───────────────────────────────────────────────────────────────────────────
 *
 * Built to the approved Organizations mockup: white surfaces on a soft cool
 * canvas, 12px card radius, soft directional shadow, navy type, a premium blue
 * primary and a restrained gold for God actions. Density is preserved — this is
 * an operations platform, not a consumer dashboard — so rows stay compact and
 * the table still carries twelve columns. What changed is that you can read it.
 */
import { useEffect } from 'react'

const CSS = `
.gm-scope{
  color:var(--gm-text);font-size:13px;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;position:relative;
  background:var(--gm-bg);
}
.gm-scope b,.gm-scope strong,.gm-scope .gm-n,.gm-scope td,.gm-scope th{
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1
}
/* The console grid overlay. Retained as an element so no page has to change,
   but --gm-grid is transparent: the texture belonged to the rejected design. */
.gm-grid-overlay{
  position:absolute;inset:0;pointer-events:none;z-index:0;
  background:
    linear-gradient(var(--gm-grid) 1px,transparent 1px),
    linear-gradient(90deg,var(--gm-grid) 1px,transparent 1px);
  background-size:36px 36px;
}

/* ── page furniture ────────────────────────────────────────────────────── */
.gm-h1{margin:0;color:var(--gm-head);font-size:28px;font-weight:700;
  letter-spacing:-.02em;line-height:1.15}
.gm-lede{margin:7px 0 0;color:var(--gm-dim);font-size:13px;max-width:82ch;line-height:1.55}
.gm-crumb{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--gm-dim)}
.gm-crumb b{color:var(--gm-head);font-weight:600}
.gm-pagehead{display:flex;justify-content:space-between;gap:20px;
  align-items:flex-start;flex-wrap:wrap;margin-bottom:18px}

.gm-card{
  background:var(--gm-card);
  border:1px solid var(--gm-card-line);border-radius:var(--gm-radius);
  box-shadow:var(--gm-shadow-card);
}

/* ── metric tiles ──────────────────────────────────────────────────────────
   The mockup's summary row: an icon chip, a large value, a quiet label. Every
   one of them has to be fed by an authoritative count — a tile with no source
   renders "—", never 0, because unknown is not zero. */
.gm-metrics{display:grid;gap:12px;margin-bottom:16px;
  grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.gm-metric{
  display:flex;align-items:center;gap:13px;
  padding:16px 18px;position:relative;overflow:hidden;
  transition:border-color .16s ease,box-shadow .16s ease,transform .16s ease;
}
.gm-metric-ico{
  width:38px;height:38px;border-radius:10px;flex:none;
  display:grid;place-items:center;
  background:var(--gm-pill-blue-bg);color:var(--gm-pill-blue-fg);
}
.gm-metric-ico.ok{background:var(--gm-pill-teal-bg);color:var(--gm-pill-teal-fg)}
.gm-metric-ico.warn{background:var(--gm-pill-gold-bg);color:var(--gm-pill-gold-fg)}
.gm-metric-ico.bad{background:var(--gm-pill-red-bg);color:var(--gm-pill-red-fg)}
.gm-metric-ico.off{background:var(--gm-pill-off-bg);color:var(--gm-pill-off-fg)}
.gm-metric-v{display:block;font-size:22px;font-weight:700;color:var(--gm-head);line-height:1.1;
  letter-spacing:-.02em}
.gm-metric-k{display:block;font-size:12px;color:var(--gm-dim);margin-top:2px}
.gm-metric.gm-click{cursor:pointer;text-align:left;font-family:inherit;width:100%}
.gm-metric.gm-click:hover{border-color:var(--gm-card-line-hover);box-shadow:var(--gm-shadow-lift)}

/* ── hierarchy rows ────────────────────────────────────────────────────── */
.gm-row{
  min-height:44px;border-bottom:1px solid var(--gm-row-line);
  transition:background .16s ease,box-shadow .16s ease;
}
.gm-row.gm-click{cursor:pointer}
.gm-row.gm-click:hover{background:var(--gm-row-hover);box-shadow:inset 3px 0 var(--gm-blue)}
.gm-row.gm-lvl0{background:var(--gm-row-lvl0)}
.gm-row.gm-lvl1{background:var(--gm-row-lvl1)}
.gm-row.gm-lvl2{background:var(--gm-row-lvl2)}
.gm-thead{background:var(--gm-thead)}
.gm-thead:hover{background:var(--gm-thead);box-shadow:none}

/* ── tool tiles ────────────────────────────────────────────────────────── */
.gm-tool{
  min-height:148px;padding:18px;border-radius:var(--gm-radius);position:relative;overflow:hidden;
  text-align:left;font-family:inherit;color:inherit;width:100%;
  background:var(--gm-card);
  border:1px solid var(--gm-card-line);
  box-shadow:var(--gm-shadow-card);
  transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease;
}
.gm-tool:before{
  content:"";position:absolute;left:0;top:0;width:4px;height:100%;
  background:var(--gm-blue);opacity:.85;
}
.gm-tool.gm-gold:before{background:var(--gm-gold)}
.gm-tool.gm-live{cursor:pointer}
.gm-tool.gm-live:hover{transform:translateY(-2px);box-shadow:var(--gm-shadow-lift);border-color:var(--gm-card-line-hover)}
.gm-tool.gm-gold.gm-live:hover{border-color:var(--gm-btn-gold-soft-line)}
/* NOT opacity. A disabled tile at .62 opacity took its label with it, leaving
   an unreadable ghost. The tile recedes by SURFACE and the label stays legible
   — and "disabled" is announced as well as shown, so the state does not rest
   on appearance alone. */
.gm-tool.gm-disabled{cursor:not-allowed;background:var(--gm-panel-2);
  border-style:dashed;color:var(--gm-dim);box-shadow:none}
.gm-tool.gm-disabled:before{opacity:.35}

/* ── exception rows ────────────────────────────────────────────────────── */
.gm-ex{padding:14px 15px;border-bottom:1px solid var(--gm-row-line);display:flex;gap:12px;align-items:center}
.gm-ex:last-child{border-bottom:0}
.gm-ex:hover{background:var(--gm-row-hover-flat)}

/* ── buttons ───────────────────────────────────────────────────────────────
   Three weights and they are never in doubt: filled blue is the primary,
   filled gold is the God/admin action, white-with-a-border is everything else.
   Disabled is a flat grey surface with readable grey ink. */
.gm-btn{
  background:var(--gm-btn);border:1px solid var(--gm-btn-line);color:var(--gm-btn-fg);
  border-radius:var(--gm-radius-sm);
  padding:8px 13px;font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit;flex:none;
  display:inline-flex;align-items:center;gap:7px;line-height:1.2;
  transition:background .14s ease,border-color .14s ease,box-shadow .14s ease;
}
.gm-btn:hover{background:var(--gm-btn-hover);border-color:var(--gm-btn-hover-line)}
.gm-btn:disabled{cursor:not-allowed;background:var(--gm-btn-disabled);
  color:var(--gm-btn-disabled-fg);border-color:var(--gm-btn-disabled-line)}
.gm-btn.gm-primary-btn{background:var(--gm-btn-primary);border-color:var(--gm-btn-primary);
  color:var(--gm-btn-primary-fg);box-shadow:var(--gm-shadow-card)}
.gm-btn.gm-primary-btn:hover{background:var(--gm-btn-primary-hover);border-color:var(--gm-btn-primary-hover)}
.gm-btn.gm-gold-btn{background:var(--gm-btn-gold);border-color:var(--gm-btn-gold-line);
  color:var(--gm-btn-gold-fg);box-shadow:var(--gm-shadow-card)}
.gm-btn.gm-gold-btn:hover{background:var(--gm-btn-gold-hover);border-color:var(--gm-btn-gold-hover)}
.gm-btn.gm-sm{padding:6px 10px;font-size:11.5px}

/* ── nav rail ──────────────────────────────────────────────────────────────
   White rail, navy labels, and a selected state you cannot miss: a tinted pill
   with a blue rule down its left edge, the label in blue and in weight. The
   selection is carried by THREE things — fill, rule and weight — so it does not
   depend on colour perception alone. */
.gm-nav-item{
  display:flex;align-items:center;gap:11px;padding:8px 12px;text-decoration:none;
  font-size:13.5px;letter-spacing:0;border-left:3px solid transparent;
  border-radius:0 var(--gm-radius-sm) var(--gm-radius-sm) 0;
  margin:1px 10px 1px 0;
  color:var(--gm-text);transition:color .14s ease,background .14s ease;white-space:nowrap;
  min-width:0;font-weight:500;
}
.gm-nav-item:hover{color:var(--gm-head);background:var(--gm-panel-2)}
.gm-nav-item.gm-active{
  color:var(--gm-blue);background:var(--gm-pill-blue-bg);
  border-left-color:var(--gm-blue);font-weight:650;
}
/* An unbuilt entry is quieter than a live one but still readable — it was
   #3f556e on near-black, about 2.1:1. The NEEDS BUILD tag beside it is what
   actually carries the distinction. */
.gm-nav-item.gm-unbuilt{color:var(--gm-faint);font-weight:500}
.gm-nav-item.gm-unbuilt:hover{color:var(--gm-head);background:var(--gm-panel-2)}
.gm-nav-label{
  flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.gm-nav-tag{
  margin-left:6px;font-size:8px;letter-spacing:.06em;color:var(--gm-pill-off-fg);
  background:var(--gm-pill-off-bg);
  border:1px solid var(--gm-pill-off-bd);border-radius:4px;padding:1px 4px;flex:0 0 auto;
}
.gm-nav-item.gm-jump{color:var(--gm-dim);font-size:13px}
.gm-nav-item.gm-jump:hover{color:var(--gm-head);background:var(--gm-panel-2)}
.gm-nav-head{
  font-size:10.5px;letter-spacing:.11em;font-weight:700;color:var(--gm-dim);
  padding:16px 14px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  text-transform:uppercase;
}
.gm-nav-head.gm-next{color:var(--gm-dim)}
.gm-nav-rule{height:1px;background:var(--gm-rail-line);margin:8px 14px}

/* The rail's identity block and its footer card. */
.gm-brandmark{
  width:34px;height:34px;border-radius:9px;display:grid;place-items:center;flex:none;
  background:var(--gm-btn-primary);color:var(--gm-btn-primary-fg);
  font-weight:800;letter-spacing:-.04em;font-size:12px;box-shadow:var(--gm-logo-glow);
}
.gm-wordmark{font-size:16px;font-weight:750;color:var(--gm-head);letter-spacing:-.02em;line-height:1.1}
.gm-wordmark-sub{font-size:9.5px;font-weight:700;letter-spacing:.16em;color:var(--gm-gold);margin-top:2px}
.gm-railcard{
  margin:10px;padding:12px 13px;border-radius:var(--gm-radius);
  background:var(--gm-btn-gold-soft);border:1px solid var(--gm-btn-gold-soft-line);
  display:flex;gap:10px;align-items:flex-start;
}
.gm-railcard b{display:block;font-size:13px;color:var(--gm-head);font-weight:700}
.gm-railcard span{display:block;font-size:11px;color:var(--gm-dim);line-height:1.45}

/* ── executive summary tiles ───────────────────────────────────────────── */
.gm-stats{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:10px;margin-bottom:16px}
.gm-stat{
  background:var(--gm-stat);
  border:1px solid var(--gm-card-line);border-radius:var(--gm-radius);padding:14px;
  min-height:88px;text-align:left;font-family:inherit;color:inherit;width:100%;
  display:flex;flex-direction:column;box-shadow:var(--gm-shadow-card);
  transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease;
}
.gm-stat.gm-warn{border-color:var(--gm-pill-gold-bd)}
.gm-stat.gm-crit{border-color:var(--gm-pill-red-bd)}
.gm-stat.gm-click{cursor:pointer}
.gm-stat.gm-click:hover{transform:translateY(-2px);border-color:var(--gm-card-line-hover);box-shadow:var(--gm-shadow-lift)}
.gm-stat .gm-k{font-size:9.5px;letter-spacing:.11em;color:var(--gm-dim);font-weight:700;margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-transform:uppercase}
.gm-stat .gm-v{font-size:22px;font-weight:750;letter-spacing:-.025em;line-height:1;margin-bottom:4px;color:var(--gm-head)}
.gm-stat .gm-s{font-size:11px;color:var(--gm-dim);margin-top:auto;line-height:1.4}

/* ── platform health tiles ─────────────────────────────────────────────── */
.gm-healths{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.gm-health{
  padding:14px;background:var(--gm-health);border:1px solid var(--gm-health-line);
  border-radius:var(--gm-radius);text-align:left;font-family:inherit;color:inherit;width:100%;
  box-shadow:var(--gm-shadow-card);
  transition:border-color .16s ease,background .16s ease;
}
.gm-health.gm-click{cursor:pointer}
.gm-health.gm-click:hover{border-color:var(--gm-card-line-hover);background:var(--gm-health-hover)}
.gm-health-top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}
.gm-health b{font-size:12.5px;color:var(--gm-head);font-weight:650}
.gm-health p{margin:0;color:var(--gm-dim);font-size:11.5px;line-height:1.5}
.gm-dot{width:8px;height:8px;border-radius:50%;flex:none;display:inline-block}
.gm-dot.ok{background:var(--gm-teal)}
.gm-dot.warn{background:var(--gm-amber)}
.gm-dot.bad{background:var(--gm-red)}
.gm-dot.off{background:var(--gm-pill-off-bd)}

/* ── the shared severity vocabulary ────────────────────────────────────────
   The five words come from app/services/severity.py and mean the same thing
   here as on the Compensation Command Center. Two of them — "can't check" and
   "nothing yet" — deliberately get NO colour: an unlit tile is the honest
   rendering of a subsystem we cannot see, and colouring it green is the exact
   green-for-silence mistake the endpoint exists to avoid. The WORD is always
   present beside the colour. */
.gm-sev{display:inline-block;border-radius:999px;padding:2px 9px;margin-bottom:8px;
  font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  border:1px solid var(--gm-pill-off-bd);background:var(--gm-pill-off-bg);color:var(--gm-pill-off-fg)}
.gm-sev.sv-healthy{background:var(--gm-pill-teal-bg);border-color:var(--gm-pill-teal-bd);color:var(--gm-pill-teal-fg)}
.gm-sev.sv-attention{background:var(--gm-pill-gold-bg);border-color:var(--gm-pill-gold-bd);color:var(--gm-pill-gold-fg)}
.gm-sev.sv-action_required{background:var(--gm-pill-red-bg);border-color:var(--gm-pill-red-bd);color:var(--gm-pill-red-fg)}
.gm-sev.sv-unavailable{background:var(--gm-pill-off-bg);border-color:var(--gm-pill-off-bd);color:var(--gm-pill-off-fg)}
.gm-sev.sv-no_data{background:transparent;border-style:dashed;border-color:var(--gm-pill-off-bd);
  color:var(--gm-pill-off-fg)}

.gm-health{border-left-width:3px;border-left-style:solid;
  border-left-color:var(--gm-health-line)}
.gm-health.sv-healthy{border-left-color:var(--gm-teal)}
.gm-health.sv-attention{border-left-color:var(--gm-amber)}
.gm-health.sv-action_required{border-left-color:var(--gm-red)}
.gm-health.sv-unavailable{border-left-color:var(--gm-pill-off-bd)}
.gm-health.sv-no_data{border-left-color:var(--gm-row-line-strong)}

.gm-health p.gm-health-head{color:var(--gm-text);font-size:12px;font-weight:600;margin-bottom:5px}
.gm-health p.gm-health-needs{margin-top:7px;color:var(--gm-dim);font-size:11px;
  line-height:1.55;font-style:italic}
.gm-health p.gm-health-needs span{color:var(--gm-ghost);font-style:normal}
/* The raw diagnostic when a check itself fell over. Quiet, last, monospace —
   present for whoever needs it, never the sentence an owner reads, and still
   held at the readable floor: a diagnostic nobody can read is not one. */
.gm-health p.gm-health-tech{margin-top:6px;color:var(--gm-ghost);font-size:10.5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  line-height:1.45;word-break:break-word}

/* ── owner action queue ────────────────────────────────────────────────── */
.gm-q{display:flex;flex-direction:column;max-height:560px;overflow-y:auto}
.gm-q::-webkit-scrollbar{width:8px}
.gm-q::-webkit-scrollbar-thumb{background:var(--gm-row-line-strong);border-radius:4px}
.gm-q::-webkit-scrollbar-track{background:transparent}
.gm-q-item{
  display:grid;grid-template-columns:9px 1fr auto;gap:12px;align-items:start;
  padding:12px 14px;border-bottom:1px solid var(--gm-row-line);
}
.gm-q-item:last-child{border-bottom:0}
.gm-q-item:hover{background:var(--gm-row-hover-flat)}
.gm-q-item>i{width:9px;height:9px;border-radius:50%;margin-top:4px}
.gm-q-title{display:block;font-size:13px;color:var(--gm-head);font-weight:600;margin-bottom:3px;line-height:1.35}
.gm-q-detail{display:block;font-size:11.5px;color:var(--gm-dim);line-height:1.55}
.gm-q-meta{display:flex;flex-direction:column;align-items:flex-end;gap:6px;text-align:right;flex:none}
.gm-q-sev{font-size:10px;font-weight:700;letter-spacing:.07em}
.gm-q-age{font-size:11px;color:var(--gm-ghost);white-space:nowrap}

/* ── command table ─────────────────────────────────────────────────────────
   Dense on purpose. What changed is legibility: a light header band with real
   letterspaced headings, hairline row rules, a hover state, and a grouping row
   that is visually a band rather than another record. */
.gm-tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.gm-table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1080px}
table.gm-table th{
  text-align:left;padding:11px 12px;color:var(--gm-dim);font-size:10px;letter-spacing:.09em;
  font-weight:700;text-transform:uppercase;
  border-bottom:1px solid var(--gm-row-line-strong);background:var(--gm-thead);
  white-space:nowrap;position:sticky;top:0;z-index:2;
}
table.gm-table td{padding:11px 12px;border-bottom:1px solid var(--gm-row-line);vertical-align:middle;color:var(--gm-text)}
table.gm-table tbody tr:hover td{background:var(--gm-row-hover-flat)}
table.gm-table td.gm-num,table.gm-table th.gm-num{text-align:right;font-variant-numeric:tabular-nums}
/* The name column does not get squeezed. Without a floor it compressed until
   "Restland Cemetery and Funeral Home" wrapped onto three lines, which turns a
   scannable table into a wall - the table scrolls sideways instead, which is
   what .gm-tablewrap is for. */
table.gm-table th:first-child,table.gm-table td:first-child{min-width:214px}
.gm-orgname{color:var(--gm-head);font-weight:650;font-size:13px}
.gm-orgsub{color:var(--gm-dim);font-size:11px;margin-top:2px}
.gm-group td{background:var(--gm-row-lvl0);color:var(--gm-blue);font-weight:700;font-size:13px;
  letter-spacing:0;padding:9px 12px}
table.gm-table tbody tr.gm-group:hover td{background:var(--gm-row-lvl0)}
.gm-groupbtn{background:none;border:0;color:inherit;font:inherit;cursor:pointer;padding:0;display:flex;align-items:center;gap:9px}

/* ── pills ─────────────────────────────────────────────────────────────── */
.gm-pill{display:inline-block;padding:3px 9px;border-radius:999px;font-size:10px;font-weight:700;letter-spacing:.04em;white-space:nowrap;border:1px solid transparent;text-transform:uppercase}
.gm-pill.teal{background:var(--gm-pill-teal-bg);border-color:var(--gm-pill-teal-bd);color:var(--gm-pill-teal-fg)}
.gm-pill.gold{background:var(--gm-pill-gold-bg);border-color:var(--gm-pill-gold-bd);color:var(--gm-pill-gold-fg)}
.gm-pill.blue{background:var(--gm-pill-blue-bg);border-color:var(--gm-pill-blue-bd);color:var(--gm-pill-blue-fg)}
.gm-pill.red{background:var(--gm-pill-red-bg);border-color:var(--gm-pill-red-bd);color:var(--gm-pill-red-fg)}
.gm-pill.purple{background:var(--gm-pill-purple-bg);border-color:var(--gm-pill-purple-bd);color:var(--gm-pill-purple-fg)}
.gm-pill.off{background:var(--gm-pill-off-bg);border-color:var(--gm-pill-off-bd);color:var(--gm-pill-off-fg)}

/* ── row actions ───────────────────────────────────────────────────────────
   ONE obvious primary per row and an overflow for the rest. The row used to
   carry seven equally weighted buttons, which made the one that matters —
   Enter — impossible to find at a glance. Nothing was removed: every action is
   in the menu, and the menu is a real <button> list, keyboard reachable. */
.gm-acts{display:flex;gap:6px;align-items:center;justify-content:flex-end}
.gm-act{
  border:1px solid var(--gm-btn-line);background:var(--gm-btn);color:var(--gm-btn-fg);border-radius:7px;
  padding:6px 10px;font-size:11.5px;font-weight:600;letter-spacing:0;
  cursor:pointer;font-family:inherit;white-space:nowrap;display:inline-flex;align-items:center;gap:6px;
}
.gm-act:hover{color:var(--gm-head);border-color:var(--gm-btn-hover-line);background:var(--gm-btn-hover)}
.gm-act:disabled{cursor:not-allowed;background:var(--gm-btn-disabled);color:var(--gm-btn-disabled-fg);
  border-color:var(--gm-btn-disabled-line)}
.gm-act.gm-primary{background:var(--gm-btn-primary);border-color:var(--gm-btn-primary);color:var(--gm-btn-primary-fg)}
.gm-act.gm-primary:hover{background:var(--gm-btn-primary-hover);border-color:var(--gm-btn-primary-hover);color:var(--gm-btn-primary-fg)}
.gm-act.gm-danger{color:var(--gm-pill-red-fg);border-color:var(--gm-pill-red-bd);background:var(--gm-pill-red-bg)}
.gm-act.gm-danger:hover{background:var(--gm-red-wash);border-color:var(--gm-red);color:var(--gm-red)}
.gm-act.gm-ghost{border-color:transparent;background:none;color:var(--gm-dim);padding:6px 8px;font-size:15px;line-height:1}
.gm-act.gm-ghost:hover{background:var(--gm-panel-2);color:var(--gm-head);border-color:var(--gm-card-line)}

.gm-menuwrap{position:relative;display:inline-block}
.gm-menu{
  position:absolute;right:0;top:calc(100% + 6px);z-index:30;min-width:190px;
  background:var(--gm-panel);border:1px solid var(--gm-card-line);
  border-radius:var(--gm-radius-sm);box-shadow:var(--gm-shadow-lift);padding:5px;
}
.gm-menu button{
  display:flex;align-items:center;gap:9px;width:100%;text-align:left;
  background:none;border:0;border-radius:6px;padding:8px 10px;cursor:pointer;
  font-family:inherit;font-size:12.5px;font-weight:550;color:var(--gm-text);
}
.gm-menu button:hover{background:var(--gm-panel-2);color:var(--gm-head)}
.gm-menu button.gm-danger-item{color:var(--gm-red)}
.gm-menu button.gm-danger-item:hover{background:var(--gm-red-wash)}
.gm-menu-sep{height:1px;background:var(--gm-card-line);margin:5px 4px}

/* ── product status chips ──────────────────────────────────────────────── */
.gm-modules{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.gm-modbox{padding:14px;border-radius:var(--gm-radius);border:1px solid var(--gm-card-line);background:var(--gm-panel);box-shadow:var(--gm-shadow-card)}
.gm-modbox h4{margin:0 0 10px;font-size:10.5px;letter-spacing:.12em;font-weight:700;text-transform:uppercase}
.gm-modbox h4.live{color:var(--gm-teal)}
.gm-modbox h4.next{color:var(--gm-amber)}
.gm-chips{display:flex;gap:6px;flex-wrap:wrap}
.gm-chip{padding:5px 10px;border-radius:999px;font-size:11px;font-weight:600;border:1px solid var(--gm-pill-off-bd);color:var(--gm-pill-off-fg);background:var(--gm-pill-off-bg);font-family:inherit}
.gm-chip.live{border-color:var(--gm-pill-teal-bd);color:var(--gm-pill-teal-fg);background:var(--gm-pill-teal-bg);cursor:pointer}
.gm-chip.live:hover{border-color:var(--gm-teal)}
.gm-chip.next{border-color:var(--gm-pill-gold-bd);color:var(--gm-pill-gold-fg);background:var(--gm-pill-gold-bg);cursor:default}

/* ── search, filters, view toggle ──────────────────────────────────────────
   The mockup's filter bar: one white card carrying a search field, a row of
   filter pills, a grouping control and the result count. */
.gm-filterbar{
  display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  background:var(--gm-panel);border:1px solid var(--gm-card-line);
  border-radius:var(--gm-radius);box-shadow:var(--gm-shadow-card);
  padding:12px 14px;margin-bottom:14px;
}
.gm-search{position:relative;flex:1 1 260px;max-width:380px;display:flex;align-items:center}
.gm-search svg{position:absolute;left:11px;color:var(--gm-dim);pointer-events:none}
.gm-search .gm-input{width:100%;padding-left:34px}
.gm-input{
  background:var(--gm-field);border:1px solid var(--gm-field-line);color:var(--gm-field-fg);
  border-radius:var(--gm-radius-sm);
  padding:9px 12px;font-size:13px;font-family:inherit;outline:none;min-width:0;
}
.gm-input:focus{border-color:var(--gm-field-line-focus);box-shadow:0 0 0 3px var(--gm-focus-halo)}
.gm-input::placeholder{color:var(--gm-placeholder)}
.gm-filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.gm-seg{display:flex;gap:6px;flex-wrap:wrap}
.gm-seg button{
  background:var(--gm-btn);border:1px solid var(--gm-btn-line);border-radius:999px;color:var(--gm-btn-fg);
  cursor:pointer;font-size:12.5px;font-weight:600;letter-spacing:0;padding:7px 14px;font-family:inherit;
  transition:background .14s ease,border-color .14s ease,color .14s ease;
}
.gm-seg button:hover{background:var(--gm-btn-hover);border-color:var(--gm-btn-hover-line)}
/* The selected filter is a filled blue pill AND is marked aria-pressed, so the
   state is exposed to assistive tech rather than being purely visual. */
.gm-seg button.on{background:var(--gm-btn-primary);border-color:var(--gm-btn-primary);color:var(--gm-btn-primary-fg)}
.gm-seg button.on:hover{background:var(--gm-btn-primary-hover);border-color:var(--gm-btn-primary-hover)}
.gm-count{color:var(--gm-dim);font-size:12.5px;white-space:nowrap}
/* Visible to a screen reader, not on screen. An icon-only search field still
   needs a name. */
.gm-sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}

/* ── empty / loading ───────────────────────────────────────────────────── */
.gm-empty{padding:34px 26px;text-align:center;color:var(--gm-dim);font-size:13px}

/* ── keyboard ──────────────────────────────────────────────────────────── */
.gm-scope :focus-visible,
.gm-health:focus-visible,
.gm-stat:focus-visible{outline:2px solid var(--gm-focus-ring);outline-offset:2px;
  border-radius:10px}

@media(prefers-reduced-motion:reduce){
  .gm-health,.gm-stat,.gm-metric,.gm-tool,
  .gm-stat.gm-click:hover,.gm-metric.gm-click:hover{transition:none;transform:none}
}

/* ── IDENTITY TABLE — Users & Identity ─────────────────────────────────────
   THE DEFECT THIS CORRECTS. The shared command table carries min-width:1080px
   and otherwise sizes itself to its contents, which is right for a five-column
   list and wrong for this one. Eight columns, a MEMBERSHIPS cell that rendered
   one full-width pill per access context, and an ACTIONS cell carrying four
   equally weighted buttons pushed the real width well past 1700px, so a god
   admin had to drag a horizontal scrollbar to reach STATUS and DEACTIVATE —
   the two things the screen exists for.

   The correction is layout, not subtraction. Nothing was taken off the page:
   every access context and every action is still here, one click away rather
   than one scroll away, and no type size was reduced to buy the room.

     - table-layout:fixed with a budget per column, so one long customer name
       can no longer widen the table for every other row.
     - ACCESS summarises ("3 ACCESS ROLES"); the full list opens in an
       expandable row underneath. Same data, off the critical path.
     - MANAGE ACCESS and DEACTIVATE / REACTIVATE stay visible on every row at
       every width. Only RESET PASSWORD and OPEN IN CUSTOMER move into the
       overflow, which is a real keyboard-reachable button list.
     - USER freezes to the left edge and ACTIONS to the right, so on the rare
       width that still scrolls the region, the identity and its actions are
       both on screen the whole time.
     - Below the widths where everything fits, columns drop in order of
       importance and what they carried reappears under the name. USER, STATUS
       and ACTIONS never drop. */
table.gm-table.gm-idtable{table-layout:fixed;min-width:1180px}
/* box-sizing:border-box is load-bearing, not tidiness. Without it a column's
   width is its CONTENT box and the 20px of padding lands on top, so every
   budget below would silently be 20px bigger than it reads and the floors that
   the breakpoints are computed from would all be wrong. */
table.gm-table.gm-idtable th,
table.gm-table.gm-idtable td{box-sizing:border-box;padding:10px;
  overflow:hidden;text-overflow:ellipsis}
/* The shared 214px floor on the first column is what this table replaces with
   a real budget — left in place it fights table-layout:fixed. */
table.gm-table.gm-idtable th:first-child,
table.gm-table.gm-idtable td:first-child{min-width:0}
/* Every column is budgeted, and USER is budgeted as EVERYTHING ELSE TAKEN AWAY.
   Two things go wrong without that. An unsized column in a fixed layout is
   handed the leftover, which at 1280 was a column two characters wide; and
   sizing it in px instead leaves the slack stranded after the last column as a
   dead strip the frozen ACTIONS cell no longer reaches the edge of. So the
   non-USER budgets are summed into --gm-idfixed, restated at each breakpoint
   as columns drop, and USER takes the rest. --gm-idfixed and the table's
   min-width are the same number plus 210 — change one, change both. */
.gm-idtable{--gm-idfixed:970px}
.gm-idtable .c-user{width:calc(100% - var(--gm-idfixed))}
.gm-idtable .c-role{width:110px}
.gm-idtable .c-brand{width:118px}
.gm-idtable .c-org{width:150px}
.gm-idtable .c-access{width:140px}
.gm-idtable .c-last{width:100px}
.gm-idtable .c-status{width:112px}
.gm-idtable .c-act{width:240px}

/* Frozen identity and frozen actions. Both need an opaque fill of their own —
   a transparent sticky cell shows the scrolled row through it. */
.gm-idtable th.c-user,.gm-idtable td.c-user{position:sticky;left:0;z-index:1;
  background:var(--gm-card);box-shadow:1px 0 0 var(--gm-row-line)}
.gm-idtable th.c-act,.gm-idtable td.c-act{position:sticky;right:0;z-index:1;
  background:var(--gm-card);box-shadow:-1px 0 0 var(--gm-row-line)}
.gm-idtable th.c-user,.gm-idtable th.c-act{z-index:3;background:var(--gm-thead)}
table.gm-table.gm-idtable tbody tr:hover td.c-user,
table.gm-table.gm-idtable tbody tr:hover td.c-act{background:var(--gm-row-hover-flat)}

.gm-idhead{display:flex;align-items:flex-start;gap:2px;min-width:0}
.gm-idnames{min-width:0;flex:1 1 auto}
.gm-idtable .gm-orgname,
.gm-idtable .gm-orgsub{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gm-idtoggle{flex:none;background:none;border:0;margin:0;padding:2px 6px 0 0;cursor:pointer;
  font:inherit;font-size:11px;line-height:1.25;color:var(--gm-dim)}
.gm-idtoggle:hover{color:var(--gm-blue)}
/* What a dropped column was carrying, restated under the name. Hidden at the
   widths where the columns themselves are present. */
.gm-idmeta{display:none;margin-top:3px;color:var(--gm-dim);font-size:10.5px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* The customer cell is a link to that customer and has to survive a 40-character
   name inside a 132px budget. */
.gm-idtable .c-org .gm-act{display:inline-block;max-width:100%;padding:5px 8px;
  font-size:11.5px;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}

/* ACCESS — the compact summary. Reads as a pill, behaves as a disclosure. */
.gm-idaccess{max-width:100%;border-radius:999px;padding:3px 9px;font:inherit;font-size:10px;
  font-weight:700;letter-spacing:.04em;text-transform:uppercase;white-space:nowrap;cursor:pointer;
  overflow:hidden;text-overflow:ellipsis;
  background:var(--gm-pill-purple-bg);border:1px solid var(--gm-pill-purple-bd);
  color:var(--gm-pill-purple-fg)}
.gm-idaccess:hover{border-color:var(--gm-purple)}
.gm-idaccess.none{cursor:default;background:var(--gm-pill-off-bg);
  border-color:var(--gm-pill-off-bd);color:var(--gm-pill-off-fg)}

.gm-idtable td.c-act .gm-acts{justify-content:flex-start;flex-wrap:nowrap;gap:5px}
.gm-idtable td.c-act .gm-act{padding:6px 9px;font-size:11px}
.gm-idself{flex:none;color:var(--gm-dim);font-size:10px;font-weight:600;letter-spacing:.05em;
  white-space:nowrap}
/* The overflow menu is positioned against the VIEWPORT, not the cell: the table
   region is a scroll container, and an absolutely positioned menu inside it is
   clipped the moment it is taller than the row. */
.gm-menu.gm-menu-fixed{position:fixed}

/* The expanded detail. Everything the compact row summarises, in full, without
   leaving the page — and the full access management is still one button away. */
.gm-idtable tr.gm-iddetail>td{padding:0;background:var(--gm-panel-2);
  border-bottom:1px solid var(--gm-row-line-strong)}
table.gm-table.gm-idtable tbody tr.gm-iddetail:hover>td{background:var(--gm-panel-2)}
.gm-iddetail-in{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
  gap:14px 22px;padding:14px 16px 16px}
.gm-idfield{min-width:0}
.gm-idfield h5{margin:0 0 6px;font-size:9.5px;letter-spacing:.10em;text-transform:uppercase;
  font-weight:700;color:var(--gm-dim)}
.gm-idfield p{margin:0;font-size:12.5px;line-height:1.5;color:var(--gm-text);word-break:break-word}
.gm-idmem{display:flex;flex-direction:column;align-items:flex-start;gap:5px}
.gm-idmem .gm-pill{white-space:normal;text-align:left}

/* Progressive disclosure. Each step drops the least load-bearing column still
   present AND lowers the table's floor by exactly that column's budget, so the
   region keeps fitting instead of merely keeping a scrollbar.

   A DROPPED COLUMN IS COLLAPSED TO ZERO, NOT display:none, AND THAT IS NOT A
   STYLE CHOICE. The expanded detail row spans the table with a colSpan, and a
   table's column count is the widest row in it — so the moment a header cell
   stops generating a box, the colSpan is larger than the header and the table
   grows a phantom, unbudgeted, auto-width column. The spare width then splits
   between USER and the phantoms (at 1366 that made USER a third of its budget
   and opened a 208px dead strip past the frozen ACTIONS cell). Keeping the
   cell in the layout at zero width keeps the column count at eight forever, so
   the colSpan is a constant and no JavaScript has to measure anything.

   The selectors below are written the long way — table.gm-table.gm-idtable
   th.c-x — because the padding they have to beat is set by a three-class
   selector. A two-class ".gm-idtable .c-brand" loses to it, and a
   "collapsed" column then keeps its 20px of padding: five of those is a
   hundred pixels of nothing, and every floor below is wrong by that much. */
@media(max-width:1490px){
  table.gm-table.gm-idtable th.c-brand,table.gm-table.gm-idtable td.c-brand{
    width:0;min-width:0;padding-left:0;padding-right:0;visibility:hidden;overflow:hidden}
  table.gm-table.gm-idtable{min-width:1062px}
  .gm-idtable{--gm-idfixed:852px}
  .gm-idtable .gm-idmeta{display:block}
}
@media(max-width:1370px){
  table.gm-table.gm-idtable th.c-last,table.gm-table.gm-idtable td.c-last{
    width:0;min-width:0;padding-left:0;padding-right:0;visibility:hidden;overflow:hidden}
  table.gm-table.gm-idtable{min-width:962px}
  .gm-idtable{--gm-idfixed:752px}
}
@media(max-width:1270px){
  table.gm-table.gm-idtable th.c-org,table.gm-table.gm-idtable td.c-org{
    width:0;min-width:0;padding-left:0;padding-right:0;visibility:hidden;overflow:hidden}
  table.gm-table.gm-idtable{min-width:812px}
  .gm-idtable{--gm-idfixed:602px}
}
@media(max-width:1120px){
  table.gm-table.gm-idtable th.c-role,table.gm-table.gm-idtable td.c-role{
    width:0;min-width:0;padding-left:0;padding-right:0;visibility:hidden;overflow:hidden}
  table.gm-table.gm-idtable{min-width:702px}
  .gm-idtable{--gm-idfixed:492px}
}
@media(max-width:1010px){
  table.gm-table.gm-idtable th.c-access,table.gm-table.gm-idtable td.c-access{
    width:0;min-width:0;padding-left:0;padding-right:0;visibility:hidden;overflow:hidden}
  table.gm-table.gm-idtable{min-width:562px}
  .gm-idtable{--gm-idfixed:352px}
}

/* ── responsive ────────────────────────────────────────────────────────────
   Desktop density is preserved; narrow widths reflow rather than lose anything.
   No action is hidden at any width. */
@media(max-width:1350px){
  .gm-stats{grid-template-columns:repeat(4,minmax(0,1fr))}
}
@media(max-width:1150px){
  .gm-band2{grid-template-columns:1fr!important}
}
@media(max-width:1000px){
  .gm-healths{grid-template-columns:1fr 1fr}
  .gm-modules{grid-template-columns:1fr}
}
@media(max-width:760px){
  .gm-h1{font-size:23px}
  .gm-filterbar{padding:11px}
  .gm-search{max-width:none;flex:1 1 100%}
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
