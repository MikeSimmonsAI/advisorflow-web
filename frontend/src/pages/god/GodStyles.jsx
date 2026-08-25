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
  display:flex;align-items:center;gap:11px;padding:9px 16px;text-decoration:none;
  font-size:12.5px;letter-spacing:.02em;border-left:2px solid transparent;
  color:#5c7a96;transition:color .14s ease,background .14s ease;white-space:nowrap;
}
.gm-nav-item:hover{color:#8ab4cc;background:rgba(47,182,255,.03)}
.gm-nav-item.gm-active{color:var(--gm-blue);background:rgba(47,182,255,.06);border-left-color:var(--gm-blue);font-weight:600}
.gm-nav-item.gm-unbuilt{color:#3f556e}
.gm-nav-item.gm-unbuilt:hover{color:#5c7a96;background:rgba(47,182,255,.02)}
.gm-nav-tag{
  margin-left:auto;font-size:7.5px;letter-spacing:.09em;color:#43607d;
  border:1px solid #23394f;border-radius:3px;padding:1px 4px;flex:none;
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
