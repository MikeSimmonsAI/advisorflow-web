/**
 * THE DEMO SUITE'S VISUAL LANGUAGE.
 *
 * Class prefix is `ds-`, so nothing here can collide with the tenant app,
 * God Mode (`gm-`), the Sales Workspace (`sw-`), the Executive Suite (`ex-`)
 * or the APP_ENV=demo console (`dc-`). Same convention, same reason.
 *
 * WHY THIS SURFACE LOOKS DIFFERENT FROM GOD MODE, DELIBERATELY
 *
 * God Mode is an instrument panel for one person who already knows what
 * everything is. This screen is shown to a PROSPECT — somebody deciding
 * whether to spend thousands of pounds — and it is the only part of the
 * product most of them see before they sign. It has to look like a product,
 * not like an admin console: more air, fewer numbers per square inch, one
 * obvious primary action per panel, and states (loading, empty, error,
 * confirmed) that are designed rather than defaulted.
 *
 * It stays recognisably part of the same family: the same near-black grounds,
 * the same signal palette, the same typeface. A demo that looked like a
 * different product would be a demo of a different product.
 */
let injected = false

const CSS = `
.ds-scope{
  --ds-bg:#040910;--ds-panel:#0a1420;--ds-panel2:#0e1c2c;--ds-raise:#122438;
  --ds-line:rgba(96,170,224,.18);--ds-line2:rgba(110,196,255,.34);
  --ds-blue:#3fb9f5;--ds-teal:#25e6b0;--ds-amber:#ffc35a;--ds-red:#ff5f7d;
  --ds-purple:#a97bff;--ds-gold:#ffd46b;
  --ds-text:#e2edfa;--ds-head:#ffffff;--ds-dim:#8aa2bb;--ds-ghost:#5b7691;
  color:var(--ds-text);font-size:13.5px;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;min-height:100%;position:relative;
  background:
    radial-gradient(1100px 480px at 12% -8%,rgba(63,185,245,.14),transparent 60%),
    radial-gradient(900px 420px at 96% 0%,rgba(169,123,255,.10),transparent 55%),
    linear-gradient(180deg,#050b14,#040910 55%,#050b14);
}
.ds-shell{display:flex;min-height:100vh;align-items:stretch}
.ds-rail{width:216px;flex:0 0 216px;border-right:1px solid var(--ds-line);
  padding:18px 12px;display:flex;flex-direction:column;gap:4px;
  background:linear-gradient(180deg,rgba(10,20,32,.9),rgba(4,9,16,.9))}
.ds-brand{padding:4px 8px 18px}
.ds-brand-name{color:var(--ds-head);font-size:15px;font-weight:600;letter-spacing:-.02em}
.ds-brand-sub{color:var(--ds-ghost);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.12em;margin-top:3px}
.ds-navhead{color:var(--ds-ghost);font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;padding:14px 8px 6px}
.ds-nav{display:flex;align-items:center;gap:9px;padding:9px 11px;border-radius:9px;
  color:var(--ds-dim);font-size:12.5px;cursor:pointer;border:1px solid transparent;
  background:transparent;text-align:left;width:100%;transition:all .15s ease}
.ds-nav:hover{background:rgba(63,185,245,.08);color:var(--ds-text)}
.ds-nav.on{background:rgba(63,185,245,.14);border-color:var(--ds-line2);
  color:var(--ds-head);box-shadow:0 0 0 1px rgba(63,185,245,.08) inset}
.ds-nav-dot{width:6px;height:6px;border-radius:50%;background:var(--ds-blue);
  opacity:0;flex:0 0 6px}
.ds-nav.on .ds-nav-dot{opacity:1}
.ds-main{flex:1;min-width:0;padding:22px 26px 60px;display:flex;flex-direction:column}
.ds-coach{width:340px;flex:0 0 340px;border-left:1px solid var(--ds-line);
  padding:18px 16px;background:linear-gradient(180deg,rgba(14,28,44,.92),rgba(6,12,20,.92))}
.ds-coach.collapsed{width:52px;flex:0 0 52px;padding:18px 8px}

.ds-topbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.ds-title{margin:0;color:var(--ds-head);font-size:23px;letter-spacing:-.035em;line-height:1.1}
.ds-sub{margin:6px 0 0;color:var(--ds-dim);font-size:12px;max-width:720px;line-height:1.6}

.ds-card{background:linear-gradient(180deg,rgba(14,28,44,.72),rgba(8,17,27,.72));
  border:1px solid var(--ds-line);border-radius:14px;padding:18px;
  box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 18px 40px -30px rgba(0,0,0,.9)}
.ds-card + .ds-card{margin-top:14px}
.ds-card-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.ds-card-title{color:var(--ds-head);font-size:14px;font-weight:600;letter-spacing:-.01em;margin:0}
.ds-card-note{color:var(--ds-ghost);font-size:11px}

.ds-grid{display:grid;gap:14px}
.ds-grid.two{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.ds-grid.stats{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}

.ds-stat{background:rgba(18,36,56,.6);border:1px solid var(--ds-line);
  border-radius:12px;padding:14px 16px}
.ds-stat .k{color:var(--ds-ghost);font-size:10px;letter-spacing:.12em;text-transform:uppercase}
.ds-stat .v{color:var(--ds-head);font-size:24px;font-weight:600;margin-top:6px;letter-spacing:-.03em}
.ds-stat .s{color:var(--ds-dim);font-size:11px;margin-top:4px}

.ds-btn{background:rgba(63,185,245,.1);border:1px solid var(--ds-line2);
  color:var(--ds-text);border-radius:9px;padding:8px 14px;font-size:12px;
  cursor:pointer;font-family:inherit;transition:all .15s ease;letter-spacing:.02em}
.ds-btn:hover:not(:disabled){background:rgba(63,185,245,.2);color:#fff}
.ds-btn:disabled{opacity:.4;cursor:not-allowed}
.ds-btn.primary{background:linear-gradient(180deg,#2f9fdc,#1d7fb8);border-color:#3fb9f5;
  color:#04121d;font-weight:600}
.ds-btn.primary:hover:not(:disabled){background:linear-gradient(180deg,#46b4ef,#2790cc)}
.ds-btn.ghost{background:transparent;border-color:var(--ds-line);color:var(--ds-dim)}
.ds-btn.ghost:hover:not(:disabled){color:var(--ds-text);border-color:var(--ds-line2)}
.ds-btn.small{padding:5px 10px;font-size:11px;border-radius:7px}

.ds-pill{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;
  font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;
  border:1px solid var(--ds-line);color:var(--ds-dim);background:rgba(63,185,245,.06)}
.ds-pill.hot{color:#ffd7de;border-color:rgba(255,95,125,.45);background:rgba(255,95,125,.12)}
.ds-pill.warm{color:#ffe6bd;border-color:rgba(255,195,90,.4);background:rgba(255,195,90,.1)}
.ds-pill.cold{color:#cfe1f2;border-color:rgba(120,160,200,.3);background:rgba(120,160,200,.08)}
.ds-pill.ok{color:#c6fff0;border-color:rgba(37,230,176,.4);background:rgba(37,230,176,.1)}
.ds-pill.blocked{color:#ffd7de;border-color:rgba(255,95,125,.5);background:rgba(255,95,125,.16)}
.ds-pill.urgent{color:#04121d;background:#ffd46b;border-color:#ffd46b;font-weight:600}
.ds-pill.high{color:#ffe6bd;border-color:rgba(255,195,90,.5);background:rgba(255,195,90,.14)}
.ds-pill.standard{color:#cfe6f7;border-color:var(--ds-line2);background:rgba(63,185,245,.1)}
.ds-pill.nurture{color:#a9c0d6;border-color:var(--ds-line);background:rgba(90,120,150,.1)}

.ds-row{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:11px;
  border:1px solid transparent;transition:all .15s ease;cursor:pointer;background:transparent;
  width:100%;text-align:left;font-family:inherit}
.ds-row:hover{background:rgba(63,185,245,.06);border-color:var(--ds-line)}
.ds-row.on{background:rgba(63,185,245,.12);border-color:var(--ds-line2)}
.ds-row + .ds-row{margin-top:2px}
.ds-row .name{color:var(--ds-head);font-size:13px;font-weight:500}
.ds-row .meta{color:var(--ds-ghost);font-size:11px;margin-top:2px}
.ds-row .spacer{flex:1}

.ds-thread{display:flex;flex-direction:column;gap:10px;max-height:420px;overflow-y:auto;
  padding-right:6px}
.ds-msg{max-width:78%;padding:11px 14px;border-radius:14px;font-size:12.5px;line-height:1.55}
.ds-msg.out{align-self:flex-end;background:rgba(63,185,245,.14);
  border:1px solid var(--ds-line2);border-bottom-right-radius:5px}
.ds-msg.in{align-self:flex-start;background:rgba(20,38,58,.85);
  border:1px solid var(--ds-line);border-bottom-left-radius:5px}
.ds-msg .who{color:var(--ds-ghost);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;margin-bottom:5px;display:flex;gap:8px;align-items:center}
.ds-draft{border:1px dashed rgba(255,212,107,.5);background:rgba(255,212,107,.07);
  border-radius:12px;padding:14px;margin-top:12px}
.ds-draft .label{color:var(--ds-gold);font-size:10.5px;letter-spacing:.12em;
  text-transform:uppercase;margin-bottom:8px}

.ds-board{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}
.ds-col{flex:0 0 232px;background:rgba(10,20,32,.6);border:1px solid var(--ds-line);
  border-radius:12px;padding:12px}
.ds-col h4{margin:0 0 3px;font-size:11px;color:var(--ds-dim);letter-spacing:.08em;
  text-transform:uppercase}
.ds-col .sum{color:var(--ds-ghost);font-size:10.5px;margin-bottom:10px}
.ds-deal{background:rgba(18,36,56,.75);border:1px solid var(--ds-line);border-radius:10px;
  padding:11px 12px;margin-bottom:8px}
.ds-deal .co{color:var(--ds-head);font-size:12.5px;font-weight:500}
.ds-deal .mm{color:var(--ds-ghost);font-size:10.5px;margin-top:4px}
.ds-deal.stalled{border-color:rgba(255,195,90,.4)}

.ds-empty{color:var(--ds-ghost);font-size:12px;padding:26px 8px;text-align:center;
  border:1px dashed var(--ds-line);border-radius:11px}
.ds-loading{color:var(--ds-dim);font-size:12px;padding:26px 8px;text-align:center}
.ds-error{border:1px solid rgba(255,95,125,.4);background:rgba(255,95,125,.07);
  border-radius:11px;padding:14px;color:#ffd7de;font-size:12px}
.ds-ok{border:1px solid rgba(37,230,176,.4);background:rgba(37,230,176,.07);
  border-radius:11px;padding:13px 15px;color:#c6fff0;font-size:12.5px;line-height:1.5}

.ds-explain{background:transparent;border:1px solid var(--ds-line);color:var(--ds-ghost);
  width:22px;height:22px;border-radius:50%;font-size:11px;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;flex:0 0 22px;
  transition:all .15s ease;font-family:inherit}
.ds-explain:hover{color:var(--ds-blue);border-color:var(--ds-line2);
  background:rgba(63,185,245,.12)}

.ds-drawer{position:fixed;top:0;right:0;bottom:0;width:min(460px,92vw);z-index:60;
  background:linear-gradient(180deg,#0b1725,#060d16);border-left:1px solid var(--ds-line2);
  padding:24px;overflow-y:auto;box-shadow:-30px 0 70px -40px rgba(0,0,0,.95)}
.ds-scrim{position:fixed;inset:0;background:rgba(2,6,12,.6);z-index:55}
.ds-drawer h3{margin:0 0 4px;color:var(--ds-head);font-size:17px;letter-spacing:-.02em}
.ds-drawer .field{margin-top:18px}
.ds-drawer .field .k{color:var(--ds-blue);font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;margin-bottom:6px}
.ds-drawer .field .v{font-size:13px;line-height:1.65;color:var(--ds-text)}
.ds-say{border-left:3px solid var(--ds-gold);padding:10px 0 10px 14px;margin-top:8px;
  color:#fff6e0;font-size:13px;line-height:1.65;font-style:italic}

.ds-step{border:1px solid var(--ds-line);border-radius:11px;padding:12px;
  margin-bottom:8px;cursor:pointer;background:rgba(10,20,32,.5);transition:all .15s ease;
  width:100%;text-align:left;font-family:inherit}
.ds-step:hover{border-color:var(--ds-line2)}
.ds-step.on{border-color:var(--ds-line2);background:rgba(63,185,245,.1)}
.ds-step.done{opacity:.62}
.ds-step .n{color:var(--ds-ghost);font-size:10px;letter-spacing:.1em;text-transform:uppercase}
.ds-step .t{color:var(--ds-head);font-size:12.5px;margin-top:3px}

.ds-progress{height:4px;border-radius:3px;background:rgba(63,185,245,.14);overflow:hidden;
  margin:10px 0 14px}
.ds-progress i{display:block;height:100%;background:linear-gradient(90deg,#3fb9f5,#25e6b0);
  border-radius:3px;transition:width .4s ease}

.ds-select,.ds-input{background:rgba(8,17,27,.9);border:1px solid var(--ds-line);
  color:var(--ds-text);border-radius:9px;padding:8px 11px;font-size:12px;
  font-family:inherit;outline:none}
.ds-select:focus,.ds-input:focus{border-color:var(--ds-line2)}

.ds-banner{display:flex;align-items:center;gap:10px;padding:9px 14px;border-radius:10px;
  border:1px solid rgba(255,212,107,.35);background:rgba(255,212,107,.08);
  color:#ffe9bb;font-size:11.5px;margin-bottom:16px}

@media (max-width:1180px){
  .ds-coach{position:fixed;right:0;top:0;bottom:0;z-index:50}
  .ds-coach.collapsed{width:44px;flex-basis:44px}
}
@media (max-width:860px){
  .ds-shell{flex-direction:column}
  .ds-rail{width:100%;flex:0 0 auto;flex-direction:row;overflow-x:auto;
    border-right:none;border-bottom:1px solid var(--ds-line)}
  .ds-brand{display:none}
  .ds-navhead{display:none}
  .ds-nav{width:auto;white-space:nowrap}
  .ds-main{padding:16px 14px 60px}
}
@media (prefers-reduced-motion:reduce){.ds-scope *{transition:none!important}}
.ds-scope :focus-visible{outline:2px solid var(--ds-blue);outline-offset:2px}
`

export default function DemoStyles () {
  if (typeof document !== 'undefined' && !injected) {
    injected = true
    const el = document.createElement('style')
    el.id = 'demo-suite-styles'
    el.textContent = CSS
    document.head.appendChild(el)
  }
  return null
}
