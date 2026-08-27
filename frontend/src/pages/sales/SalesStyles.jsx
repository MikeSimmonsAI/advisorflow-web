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
.sw-scope{
  --sw-nav1:#08121f; --sw-nav2:#0a1726; --sw-navline:#1b2d3e;
  --sw-canvas:#eef2f6; --sw-ink:#182330; --sw-ink2:#5f7182;
  --sw-line:#d7e0e7; --sw-line2:#e6ebef;
  --sw-teal:#1A9B8E; --sw-teal2:#2cc9b8; --sw-teal-deep:#0f7e73;
  --sw-amber:#e7aa50; --sw-green:#55c79a; --sw-blue:#4ea7e8; --sw-red:#e46872;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
  color:var(--sw-ink);
}
.sw-scope b,.sw-scope strong,.sw-scope td,.sw-scope th,.sw-scope time{
  font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1
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
.sw-topbar{min-height:68px;background:#fff;border-bottom:1px solid #d5dde5;display:flex;
  align-items:center;padding:14px 26px;gap:12px;position:sticky;top:0;z-index:5;flex-wrap:wrap}
.sw-topbar h1{font-size:18px;margin:0}
.sw-topbar p{font-size:11px;color:#758797;margin:3px 0 0}
.sw-spacer{flex:1}
.sw-body{padding:22px 26px 60px;flex:1}

/* controls */
.sw-btn{border:1px solid #c7d1db;background:#fff;color:#223341;border-radius:8px;
  padding:8px 12px;font-size:11px;font-weight:700;cursor:pointer;white-space:nowrap}
.sw-btn:hover{border-color:#9fb0bf}
.sw-btn.sw-primary{background:var(--sw-teal);border-color:var(--sw-teal);color:#fff}
.sw-btn.sw-primary:hover{background:var(--sw-teal-deep);border-color:var(--sw-teal-deep)}
.sw-btn:disabled{opacity:.5;cursor:default}
.sw-tiny{padding:5px 8px;border-radius:6px;border:1px solid #ccd7df;background:#fff;
  font-size:9px;font-weight:700;cursor:pointer;color:#28394a;white-space:nowrap}
.sw-tiny.sw-primary{background:var(--sw-teal-deep);color:#fff;border-color:var(--sw-teal-deep)}
.sw-input,.sw-select,.sw-textarea{width:100%;padding:9px;border:1px solid #ccd7df;
  border-radius:7px;background:#fff;font-size:11px;color:var(--sw-ink)}
.sw-textarea{min-height:64px;resize:vertical;line-height:1.5}
.sw-field{margin-top:12px}
.sw-field label{display:block;font-size:9px;font-weight:800;color:#6c7f8f;
  margin-bottom:5px;letter-spacing:.05em}

/* cards */
.sw-card{background:#fff;border:1px solid var(--sw-line);border-radius:12px;
  box-shadow:0 5px 18px rgba(26,45,65,.045)}
.sw-card-h{padding:14px 16px;border-bottom:1px solid #e4e9ee;display:flex;
  align-items:center;gap:10px;flex-wrap:wrap}
.sw-card-h h3{margin:0;font-size:12px;letter-spacing:.03em}
.sw-card-h small{color:#7d8d9c;font-size:10px;display:block;margin-top:2px}
.sw-card-b{padding:16px}
.sw-mt{margin-top:16px}

.sw-chip{border-radius:999px;padding:4px 8px;font-size:9px;font-weight:700;
  border:1px solid #cfd9e0;background:#f7f9fb;color:#536677;display:inline-block;white-space:nowrap}
.sw-chip.sw-green{color:#207e5d;background:#eaf9f2;border-color:#b9ead5}
.sw-chip.sw-amber{color:#9e6722;background:#fff6e9;border-color:#f2d5aa}
.sw-chip.sw-red{color:#9d3f4b;background:#ffeff1;border-color:#efc0c6}
.sw-chip.sw-blue{color:#276c9f;background:#edf7ff;border-color:#c2dff3}
.sw-chips{display:flex;gap:6px;flex-wrap:wrap}

.sw-metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:0 0 16px}
.sw-metric{background:#fff;border:1px solid var(--sw-line);border-radius:10px;padding:13px}
.sw-metric span{display:block;font-size:9px;color:#7d8d9b;letter-spacing:.05em}
.sw-metric b{display:block;font-size:22px;margin-top:5px}
.sw-metric small{font-size:9px;color:#8a9aa8}
.sw-metric.sw-attn{border-left:3px solid var(--sw-amber)}

.sw-grid2{display:grid;grid-template-columns:1.25fr .75fr;gap:16px}
.sw-grid-even{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.sw-row{display:grid;grid-template-columns:1fr auto;gap:10px;padding:12px 14px;
  border-bottom:1px solid var(--sw-line2);align-items:center}
.sw-row:last-child{border-bottom:0}
.sw-row b{font-size:11px;display:block}
.sw-row p{font-size:9px;color:#7c8d9a;margin:3px 0 0}
.sw-rowlink{cursor:pointer;background:none;border:0;text-align:left;padding:0;width:100%}
.sw-rowlink:hover b{color:var(--sw-teal-deep);text-decoration:underline}
.sw-actions{display:flex;gap:5px;align-items:center;flex-wrap:wrap}

/* pipeline board */
.sw-pipeline{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(200px,1fr);
  gap:10px;overflow-x:auto;padding-bottom:10px;align-items:start}
.sw-stage{min-width:200px}
.sw-stage-h{display:flex;justify-content:space-between;align-items:center;padding:9px 10px;
  font-size:10px;font-weight:800;color:#536779;letter-spacing:.04em}
.sw-stage-h span{background:#dfe6ec;border-radius:20px;padding:2px 7px}
.sw-deal{background:#fff;border:1px solid #d5dfe7;border-radius:10px;padding:11px;
  margin-bottom:9px;box-shadow:0 3px 12px rgba(31,50,70,.04);cursor:pointer;
  width:100%;text-align:left;display:block}
.sw-deal:hover{border-color:#a9bccb;box-shadow:0 5px 16px rgba(31,50,70,.09)}
.sw-deal .sw-company{font-size:11px;font-weight:800}
.sw-deal .sw-contact{font-size:9px;color:#7c8c99;margin-top:3px}
.sw-deal .sw-value{font-size:10px;font-weight:800;margin-top:9px}
.sw-deal footer{display:flex;justify-content:space-between;align-items:center;
  margin-top:9px;border-top:1px solid #edf0f2;padding-top:8px;gap:6px}
.sw-deal footer small{font-size:8px;color:#8696a3}
.sw-deal.sw-hot{border-top:3px solid var(--sw-teal)}
.sw-deal.sw-warn{border-top:3px solid var(--sw-amber)}
.sw-stage-empty{border:1px dashed #cdd8e0;border-radius:10px;padding:14px;text-align:center;
  font-size:9px;color:#93a3b0;background:#f6f8fa}

/* record */
.sw-head{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:start}
.sw-head h2{font-size:22px;margin:0}
.sw-head p{font-size:11px;color:#718391;margin:5px 0 0}
.sw-infogrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.sw-info{padding:11px;border:1px solid #dfe6eb;background:#fbfcfd;border-radius:8px}
.sw-info span{display:block;font-size:8px;color:#80909d;letter-spacing:.06em}
.sw-info b{display:block;font-size:11px;margin-top:4px;word-break:break-word}

.sw-timeline{position:relative;padding-left:18px}
.sw-timeline:before{content:"";position:absolute;top:4px;bottom:4px;left:5px;width:1px;background:#ced9e1}
.sw-event{position:relative;margin-bottom:14px}
.sw-event:last-child{margin-bottom:0}
.sw-event:before{content:"";position:absolute;left:-17px;top:3px;width:8px;height:8px;
  background:#fff;border:2px solid var(--sw-teal);border-radius:50%}
.sw-event b{font-size:10px}
.sw-event p{font-size:9px;color:#7d8d9a;margin:3px 0 0}

.sw-life{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.sw-life-step{border:1px solid #d9e2e8;border-radius:9px;padding:10px;background:#f8fafb}
.sw-life-step b{font-size:9px;display:block;letter-spacing:.04em}
.sw-life-step small{display:block;font-size:8px;color:#82919d;margin-top:4px}
.sw-life-step.sw-done{border-top:3px solid var(--sw-green);background:#f4fbf8}
.sw-life-step.sw-current{border-top:3px solid var(--sw-teal);background:#f1fbf9}
.sw-life-step.sw-pending{border-top:3px solid #c9d3db}

/* states */
.sw-empty{padding:34px 20px;text-align:center;color:#8496a4}
.sw-empty b{display:block;font-size:12px;color:#5d707f}
.sw-empty p{font-size:10px;margin:6px auto 0;max-width:420px;line-height:1.6}
.sw-notbuilt{border:1px dashed #c3cedb;background:#f7f9fc;border-radius:10px;padding:16px}
.sw-notbuilt b{font-size:10px;color:#5f7182;letter-spacing:.08em;display:block}
.sw-notbuilt p{font-size:10px;color:#8496a4;margin:6px 0 0;line-height:1.6}
.sw-err{border:1px solid #efc0c6;background:#fff5f6;color:#9d3f4b;border-radius:9px;
  padding:12px 14px;font-size:11px;margin-bottom:14px}
.sw-subtle{font-size:9px;color:#80909d}
.sw-flex{display:flex;align-items:center;gap:8px}
.sw-between{justify-content:space-between}

.sw-modal-back{position:fixed;inset:0;background:rgba(10,20,32,.55);z-index:60;
  display:flex;align-items:flex-start;justify-content:center;padding:60px 20px;overflow-y:auto}
.sw-modal{background:#fff;border-radius:14px;width:100%;max-width:560px;
  box-shadow:0 24px 60px rgba(10,22,36,.35)}

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
.sw-scope .sw-note{background:#e8f7f4;border:1px solid #b7e4dc;color:#0f7e73;
  padding:10px 12px;border-radius:8px;margin-bottom:14px;font-size:13px}
.sw-scope .sw-btn.sw-ghost{background:transparent;border-color:transparent;
  color:var(--sw-ink2)}
.sw-scope .sw-btn.sw-ghost:hover{border-color:var(--sw-line);color:var(--sw-ink)}
.sw-scope .sw-two{display:grid;grid-template-columns:1.35fr 1fr;gap:16px;
  align-items:start}

.sw-scope .sw-appr{border:1px solid var(--sw-line);border-radius:10px;
  padding:12px;margin-bottom:10px;background:#fffdf7}
.sw-scope .sw-appr-head{display:flex;justify-content:space-between;
  align-items:center;gap:10px;margin-bottom:8px}
.sw-scope .sw-appr-money{display:grid;grid-template-columns:repeat(3,1fr);
  gap:8px;margin-bottom:8px}
.sw-scope .sw-quote{margin:0 0 10px;padding:8px 12px;border-left:3px solid var(--sw-teal);
  background:#f6fafb;color:var(--sw-ink);font-size:13px}
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
.sw-scope .sw-drillrow:hover{background:#f6fafb}
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
.sw-scope .sw-daybtn{border:1px solid var(--sw-line);background:#fff;border-radius:9px;
  padding:8px 10px;min-width:74px;cursor:pointer;text-align:center;flex:0 0 auto}
.sw-scope .sw-daybtn:hover{border-color:#9fb0bf}
.sw-scope .sw-daybtn.sw-on{border-color:var(--sw-teal);background:#f1fbf9;
  box-shadow:inset 0 -3px var(--sw-teal)}
.sw-scope .sw-daybtn b{display:block;font-size:13px}
.sw-scope .sw-daybtn small{display:block;font-size:9px;color:#7d8d9b;margin-top:2px}
.sw-scope .sw-daybtn .sw-dot{display:inline-block;width:5px;height:5px;border-radius:50%;
  background:var(--sw-teal);margin-top:4px}
.sw-scope .sw-daybtn .sw-dot.sw-warn{background:var(--sw-amber)}

/* one meeting row on Team Calendar */
.sw-scope .sw-cal{display:grid;grid-template-columns:84px 1fr auto;gap:12px;
  padding:11px 0;border-bottom:1px solid var(--sw-line2);align-items:start}
.sw-scope .sw-cal:last-child{border-bottom:none}
.sw-scope .sw-cal-when b{display:block;font-size:12px;font-variant-numeric:tabular-nums}
.sw-scope .sw-cal-when small{display:block;font-size:9px;color:#8696a3;margin-top:2px}
.sw-scope .sw-cal-main{min-width:0}
.sw-scope .sw-cal-main b{font-size:12px}
.sw-scope .sw-cal-meta{font-size:11px;color:var(--sw-ink2);margin-top:3px}
.sw-scope .sw-cal-parts{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}
.sw-scope .sw-part{border:1px solid var(--sw-line);border-radius:20px;padding:2px 8px;
  font-size:9px;background:#f8fafb;color:#4d6072;white-space:nowrap}
.sw-scope .sw-part.sw-req{border-color:#b9d8f0;background:#eff7fd;color:#276c9f}
.sw-scope .sw-part.sw-bad{border-color:#efc0c6;background:#fff2f4;color:#9d3f4b}
.sw-scope .sw-cal-act{display:flex;gap:6px;align-items:center;flex-shrink:0;flex-wrap:wrap}

/* queue columns — Demos / Proposals */
.sw-scope .sw-queues{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;
  align-items:start}
.sw-scope .sw-qrow{display:flex;justify-content:space-between;gap:8px;
  align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--sw-line2);
  cursor:pointer;width:100%;background:none;border-left:0;border-right:0;
  border-top:0;text-align:left}
.sw-scope .sw-qrow:last-child{border-bottom:none}
.sw-scope .sw-qrow:hover{background:#f6fafb}
.sw-scope .sw-qrow b{font-size:11px;display:block}
.sw-scope .sw-qrow .sw-why{font-size:10px;color:var(--sw-ink2);margin-top:3px}
.sw-scope .sw-qrow .sw-who{font-size:9px;color:#8696a3;margin-top:3px;display:block}

/* people grid — Salespeople */
.sw-scope .sw-people{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.sw-scope .sw-pcard{border:1px solid var(--sw-line);border-radius:11px;background:#fff;
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
  color:#8496a4;font-weight:700}
.sw-billing-card{flex:1;min-width:200px;text-align:left;cursor:pointer;
  border:1.5px solid #d7e0ea;background:#fff;border-radius:12px;padding:14px 16px;
  display:flex;flex-direction:column;gap:6px;transition:border-color .12s,box-shadow .12s}
.sw-billing-card:hover:not(:disabled){border-color:#9db4cc}
.sw-billing-card:disabled{opacity:.6;cursor:default}
.sw-billing-card.is-active{border-color:#2f6fb0;box-shadow:0 0 0 3px rgba(47,111,176,.12)}
.sw-billing-name{font-size:9px;letter-spacing:.1em;color:#5f7182;font-weight:700;
  text-transform:uppercase}
.sw-billing-rate{font-size:22px;font-weight:700;color:#16324f;line-height:1.1}
.sw-billing-rate em{font-style:normal;font-size:11px;font-weight:500;color:#6c7f90;
  margin-left:3px}
.sw-billing-save{align-self:flex-start;font-size:9px;font-weight:700;letter-spacing:.08em;
  color:#1c6b4a;background:#e7f6ee;border:1px solid #bde3ce;border-radius:20px;
  padding:3px 9px}
.sw-billing-terms{font-size:9px;color:#80909d;line-height:1.5}
.sw-billing-setup{display:flex;justify-content:space-between;align-items:center;
  margin-top:12px;padding:10px 14px;border:1px dashed #c3cedb;border-radius:10px;
  background:#f7f9fc;font-size:10px;color:#5f7182}
.sw-billing-setup b{font-size:12px;color:#16324f}
.sw-billing-setup em{font-style:normal;color:#8496a4}
.sw-billing-summary{margin-top:12px;border:1px solid #e6ecf2;border-radius:10px;
  padding:4px 14px;background:#fbfdff}
.sw-billing-row{display:flex;justify-content:space-between;align-items:center;
  padding:8px 0;font-size:10px;color:#5f7182;border-bottom:1px solid #f0f4f8}
.sw-billing-row:last-child{border-bottom:none}
.sw-billing-row b{font-size:11px;color:#16324f}
.sw-billing-row.is-primary{padding:12px 0}
.sw-billing-row.is-primary span{font-size:9px;letter-spacing:.1em;font-weight:700;
  color:#16324f}
.sw-billing-row.is-primary b{font-size:18px}

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
