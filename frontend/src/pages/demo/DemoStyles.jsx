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
 * It stays recognisably part of the same family: since 2026-09-28 that means
 * the platform's ONE light system (styles/appearance.css) - white cards on a
 * cool page, navy type, the signal palette tuned for a light ground. A demo
 * that looked like a different product would be a demo of a different product.
 */
let injected = false

const CSS = `
.ds-scope{
  --ds-bg:#f4f6fa;--ds-panel:#ffffff;--ds-panel2:#f5f7fb;--ds-raise:#eef3fb;
  --ds-line:#dde4ee;--ds-line2:#b9c7da;
  --ds-blue:#1f5eff;--ds-teal:#0b8a5f;--ds-amber:#a8650f;--ds-red:#d31f4b;
  --ds-purple:#6d43cc;--ds-gold:#93601c;
  --ds-text:#46586f;--ds-head:#0e1726;--ds-dim:#5b6d84;--ds-ghost:#6b7d94;
  color:var(--ds-text);font-size:13.5px;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased;min-height:100%;position:relative;
  background:var(--ds-bg);
}
.ds-shell{display:flex;min-height:100vh;align-items:stretch}
.ds-rail{width:216px;flex:0 0 216px;border-right:1px solid var(--ds-line);
  padding:18px 12px;display:flex;flex-direction:column;gap:4px;
  background:var(--ds-panel)}
.ds-brand{padding:4px 8px 18px}
.ds-brand-name{color:var(--ds-head);font-size:15px;font-weight:600;letter-spacing:-.02em}
.ds-brand-sub{color:var(--ds-ghost);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.12em;margin-top:3px}
.ds-navhead{color:var(--ds-ghost);font-size:9.5px;letter-spacing:.16em;
  text-transform:uppercase;padding:14px 8px 6px}
.ds-nav{display:flex;align-items:center;gap:9px;padding:9px 11px;border-radius:9px;
  color:var(--ds-dim);font-size:12.5px;cursor:pointer;border:1px solid transparent;
  background:transparent;text-align:left;width:100%;transition:all .15s ease}
.ds-nav:hover{background:rgba(31,94,255,.06);color:var(--ds-head)}
.ds-nav.on{background:rgba(31,94,255,.10);border-color:rgba(31,94,255,.25);
  color:var(--ds-blue);font-weight:600}
.ds-nav-dot{width:6px;height:6px;border-radius:50%;background:var(--ds-blue);
  opacity:0;flex:0 0 6px}
.ds-nav.on .ds-nav-dot{opacity:1}
.ds-main{flex:1;min-width:0;padding:22px 26px 60px;display:flex;flex-direction:column}
.ds-coach{width:340px;flex:0 0 340px;border-left:1px solid var(--ds-line);
  padding:18px 16px;background:var(--ds-panel)}
.ds-coach.collapsed{width:52px;flex:0 0 52px;padding:18px 8px}

.ds-topbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
.ds-title{margin:0;color:var(--ds-head);font-size:23px;letter-spacing:-.035em;line-height:1.1}
.ds-sub{margin:6px 0 0;color:var(--ds-dim);font-size:12px;max-width:720px;line-height:1.6}

.ds-card{background:var(--ds-panel);
  border:1px solid var(--ds-line);border-radius:14px;padding:18px;
  box-shadow:0 1px 2px rgba(16,32,64,.05),0 4px 16px rgba(16,32,64,.06)}
.ds-card + .ds-card{margin-top:14px}
.ds-card-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.ds-card-title{color:var(--ds-head);font-size:14px;font-weight:600;letter-spacing:-.01em;margin:0}
.ds-card-note{color:var(--ds-ghost);font-size:11px}

.ds-grid{display:grid;gap:14px}
.ds-grid.two{grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.ds-grid.stats{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}

.ds-stat{background:var(--ds-panel2);border:1px solid var(--ds-line);
  border-radius:12px;padding:14px 16px}
.ds-stat .k{color:var(--ds-ghost);font-size:10px;letter-spacing:.12em;text-transform:uppercase}
.ds-stat .v{color:var(--ds-head);font-size:24px;font-weight:600;margin-top:6px;letter-spacing:-.03em}
.ds-stat .s{color:var(--ds-dim);font-size:11px;margin-top:4px}

.ds-btn{background:var(--ds-panel);border:1px solid var(--ds-line2);
  color:var(--ds-text);border-radius:9px;padding:8px 14px;font-size:12px;
  cursor:pointer;font-family:inherit;transition:all .15s ease;letter-spacing:.02em}
.ds-btn:hover:not(:disabled){background:var(--ds-raise);color:var(--ds-head)}
.ds-btn:disabled{opacity:.4;cursor:not-allowed}
.ds-btn.primary{background:var(--ds-blue);border-color:var(--ds-blue);
  color:#ffffff;font-weight:600}
.ds-btn.primary:hover:not(:disabled){background:#1a4fd6;color:#ffffff}
.ds-btn.ghost{background:transparent;border-color:var(--ds-line);color:var(--ds-dim)}
.ds-btn.ghost:hover:not(:disabled){color:var(--ds-text);border-color:var(--ds-line2)}
.ds-btn.small{padding:5px 10px;font-size:11px;border-radius:7px}

.ds-pill{display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:999px;
  font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;
  border:1px solid #d8e0ea;color:#46586f;background:#eef2f7}
.ds-pill.hot{color:#a8173c;border-color:#f3c5d0;background:#fdecef}
.ds-pill.warm{color:#8a520a;border-color:#efd9ad;background:#fdf3e1}
.ds-pill.cold{color:#1f6fb8;border-color:#c6d7ff;background:#e8f0ff}
.ds-pill.ok{color:#0a6e4c;border-color:#bfe6d4;background:#e7f6ef}
.ds-pill.blocked{color:#a8173c;border-color:#f3c5d0;background:#fdecef}
.ds-pill.urgent{color:#5c3a07;background:#f8d98f;border-color:#efc766;font-weight:600}
.ds-pill.high{color:#8a520a;border-color:#efd9ad;background:#fdf3e1}
.ds-pill.standard{color:#1a4fd6;border-color:#c6d7ff;background:#e8f0ff}
.ds-pill.nurture{color:#46586f;border-color:#d8e0ea;background:#eef2f7}

.ds-row{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:11px;
  border:1px solid transparent;transition:all .15s ease;cursor:pointer;background:transparent;
  width:100%;text-align:left;font-family:inherit}
.ds-row:hover{background:var(--ds-raise);border-color:var(--ds-line)}
.ds-row.on{background:rgba(31,94,255,.08);border-color:rgba(31,94,255,.3)}
.ds-row + .ds-row{margin-top:2px}
.ds-row .name{color:var(--ds-head);font-size:13px;font-weight:500}
.ds-row .meta{color:var(--ds-ghost);font-size:11px;margin-top:2px}
.ds-row .spacer{flex:1}

.ds-thread{display:flex;flex-direction:column;gap:10px;max-height:420px;overflow-y:auto;
  padding-right:6px}
.ds-msg{max-width:78%;padding:11px 14px;border-radius:14px;font-size:12.5px;line-height:1.55}
.ds-msg.out{align-self:flex-end;background:#e8f0ff;color:var(--ds-head);
  border:1px solid var(--ds-line2);border-bottom-right-radius:5px}
.ds-msg.in{align-self:flex-start;background:var(--ds-panel2);color:var(--ds-head);
  border:1px solid var(--ds-line);border-bottom-left-radius:5px}
.ds-msg .who{color:var(--ds-ghost);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;margin-bottom:5px;display:flex;gap:8px;align-items:center}
.ds-draft{border:1px dashed #efd9ad;background:#fdf8ec;
  border-radius:12px;padding:14px;margin-top:12px}
.ds-draft .label{color:var(--ds-gold);font-size:10.5px;letter-spacing:.12em;
  text-transform:uppercase;margin-bottom:8px}

.ds-board{display:flex;gap:12px;overflow-x:auto;padding-bottom:8px}
.ds-col{flex:0 0 232px;background:var(--ds-panel2);border:1px solid var(--ds-line);
  border-radius:12px;padding:12px}
.ds-col h4{margin:0 0 3px;font-size:11px;color:var(--ds-dim);letter-spacing:.08em;
  text-transform:uppercase}
.ds-col .sum{color:var(--ds-ghost);font-size:10.5px;margin-bottom:10px}
.ds-deal{background:var(--ds-panel);border:1px solid var(--ds-line);border-radius:10px;
  padding:11px 12px;margin-bottom:8px}
.ds-deal .co{color:var(--ds-head);font-size:12.5px;font-weight:500}
.ds-deal .mm{color:var(--ds-ghost);font-size:10.5px;margin-top:4px}
.ds-deal.stalled{border-color:#efd9ad}

.ds-empty{color:var(--ds-ghost);font-size:12px;padding:26px 8px;text-align:center;
  border:1px dashed var(--ds-line);border-radius:11px}
.ds-loading{color:var(--ds-dim);font-size:12px;padding:26px 8px;text-align:center}
.ds-error{border:1px solid #f3c5d0;background:#fdecef;
  border-radius:11px;padding:14px;color:#a8173c;font-size:12px}
.ds-ok{border:1px solid #bfe6d4;background:#e7f6ef;
  border-radius:11px;padding:13px 15px;color:#0a6e4c;font-size:12.5px;line-height:1.5}

.ds-explain{background:transparent;border:1px solid var(--ds-line);color:var(--ds-ghost);
  width:22px;height:22px;border-radius:50%;font-size:11px;cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center;flex:0 0 22px;
  transition:all .15s ease;font-family:inherit}
.ds-explain:hover{color:var(--ds-blue);border-color:var(--ds-line2);
  background:rgba(31,94,255,.08)}

.ds-drawer{position:fixed;top:0;right:0;bottom:0;width:min(460px,92vw);z-index:60;
  background:var(--ds-panel);border-left:1px solid var(--ds-line2);
  padding:24px;overflow-y:auto;box-shadow:-12px 0 40px -20px rgba(16,32,64,.25)}
.ds-scrim{position:fixed;inset:0;background:rgba(14,23,38,.4);z-index:55}
.ds-drawer h3{margin:0 0 4px;color:var(--ds-head);font-size:17px;letter-spacing:-.02em}
.ds-drawer .field{margin-top:18px}
.ds-drawer .field .k{color:var(--ds-blue);font-size:10px;letter-spacing:.14em;
  text-transform:uppercase;margin-bottom:6px}
.ds-drawer .field .v{font-size:13px;line-height:1.65;color:var(--ds-text)}
.ds-say{border-left:3px solid var(--ds-gold);padding:10px 0 10px 14px;margin-top:8px;
  color:var(--ds-head);font-size:13px;line-height:1.65;font-style:italic}

.ds-step{border:1px solid var(--ds-line);border-radius:11px;padding:12px;
  margin-bottom:8px;cursor:pointer;background:var(--ds-panel);transition:all .15s ease;
  width:100%;text-align:left;font-family:inherit}
.ds-step:hover{border-color:var(--ds-line2)}
.ds-step.on{border-color:rgba(31,94,255,.35);background:rgba(31,94,255,.06)}
.ds-step.done{opacity:.62}
.ds-step .n{color:var(--ds-ghost);font-size:10px;letter-spacing:.1em;text-transform:uppercase}
.ds-step .t{color:var(--ds-head);font-size:12.5px;margin-top:3px}

.ds-progress{height:4px;border-radius:3px;background:#e2e8f1;overflow:hidden;
  margin:10px 0 14px}
.ds-progress i{display:block;height:100%;background:linear-gradient(90deg,#1f5eff,#0b8a5f);
  border-radius:3px;transition:width .4s ease}

.ds-select,.ds-input{background:#ffffff;border:1px solid var(--ds-line);
  color:var(--ds-text);border-radius:9px;padding:8px 11px;font-size:12px;
  font-family:inherit;outline:none}
.ds-select:focus,.ds-input:focus{border-color:var(--ds-line2)}

.ds-banner{display:flex;align-items:center;gap:10px;padding:9px 14px;border-radius:10px;
  border:1px solid #efd9ad;background:#fdf3e1;
  color:#8a520a;font-size:11.5px;margin-bottom:16px}

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
