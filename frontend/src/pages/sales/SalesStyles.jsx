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
