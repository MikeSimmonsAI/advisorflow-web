/**
 * LaunchStyles — the Launch Pad stylesheet, injected once.
 *
 * ===========================================================================
 * A FOURTH SURFACE, SCOPED THE SAME WAY THE OTHER THREE ARE
 * ===========================================================================
 *
 * The product already separates its operating layers by a data-surface
 * attribute so that a colour changed in one cannot reach another:
 *
 *     data-surface="platform"    the owner's control plane   (.gm-scope)
 *     data-surface="executive"   the brand executive         (.ex-scope)
 *     data-surface="workspace"   the customer's own tools    (tenant CSS)
 *     data-surface="launch"      THIS FILE                   (.lp-scope)
 *
 * Every rule below is nested under [data-surface="launch"] and every token is
 * declared on that attribute rather than on :root, exactly as ExecStyles does.
 * Nothing here can restyle God Mode, the Executive Suite or a tenant screen,
 * and nothing they declare can reach in.
 *
 * ===========================================================================
 * WHY THE LAUNCH PAD LOOKS THE WAY IT DOES
 * ===========================================================================
 *
 * This is the first screen a bought customer ever sees, on the white-label
 * brand's own domain. It is a welcome, not a console. So the direction is
 * DARK CHROME, LIT DOCUMENT:
 *
 *   The frame — rail, top bar, hero, phase tracker — is deep navy on charcoal
 *   with gold as the single accent and a restrained electric blue for
 *   secondary signal. Depth comes from layered fills and saturated hairline
 *   borders plus two large radial gradients, the same mechanism God Mode uses.
 *   Piling on box-shadows breaks it.
 *
 *   The work — the intake form itself — sits on a WHITE card with dark ink.
 *   Somebody is going to type twenty fields of company detail into this. The
 *   premium frame is what makes it feel like an executive onboarding; the
 *   light document is what makes it readable for the half hour that takes.
 *
 * APPEARANCE IS DELIBERATELY NOT AN AXIS HERE. The other surfaces follow
 * data-appearance because they belong to a person who works in them all day.
 * The Launch Pad is a branded first impression the customer visits a handful
 * of times, and the brand decides how it looks — so the palette is fixed and
 * a light-mode user sees the same intended design rather than a second, less
 * finished one. If that ever changes, the tokens are all in one block below.
 *
 * STAGE 1 NOTE: everything this file styles is a visual prototype. No rule
 * here depends on data that does not exist yet.
 */
import { useEffect } from 'react'

const CSS = `
/* ── tokens ───────────────────────────────────────────────────────────────
   Fixed palette; see the header for why appearance is not an axis here. */
[data-surface="launch"]{
  --lp-void:#05090f;
  --lp-navy:#0a1423; --lp-navy2:#0f1c30; --lp-navy3:#152740; --lp-navy4:#1c3350;
  --lp-line:rgba(150,182,220,.14);
  --lp-line2:rgba(150,182,220,.26);
  --lp-line3:rgba(226,192,120,.34);
  --lp-gold:#e2c078; --lp-gold2:#f5e0aa; --lp-gold-bg:rgba(226,192,120,.10);
  --lp-gold-bd:rgba(226,192,120,.30);
  --lp-blue:#5aa9ff; --lp-blue-bg:rgba(90,169,255,.10);
  --lp-blue-bd:rgba(90,169,255,.28);
  --lp-ink:#eaf1fb; --lp-ink2:#a4b7d2; --lp-ink3:#7387a2; --lp-ink4:#54687f;

  /* the light document */
  --lp-card:#ffffff; --lp-card2:#f6f8fc; --lp-card3:#eef2f8;
  --lp-cline:#e2e8f1; --lp-cline2:#eef2f7; --lp-cline-strong:#c3cede;
  --lp-cink:#141d31; --lp-cink2:#55657f; --lp-cink3:#8494ab; --lp-cink4:#a6b3c6;
  --lp-good:#16785d; --lp-good-bg:#e7f5f0; --lp-good-bd:#b7e0d1;
  --lp-warn:#8a5f14; --lp-warn-bg:#fdf4e3; --lp-warn-bd:#eddcb4;
  --lp-bad:#9e2c42;  --lp-bad-bg:#fdedf0;  --lp-bad-bd:#f0c7d1;
  --lp-info:#2b608f; --lp-info-bg:#e9f1fa; --lp-info-bd:#c3dbee;

  --lp-r:14px; --lp-r2:18px;
  --lp-shadow:0 1px 2px rgba(6,12,22,.05),0 8px 26px rgba(6,12,22,.07);
  --lp-shadow-lift:0 2px 6px rgba(6,12,22,.08),0 18px 44px rgba(6,12,22,.14);

  color-scheme:dark;
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
  color:var(--lp-ink);
  background:var(--lp-void);
}
[data-surface="launch"] *{box-sizing:border-box}
[data-surface="launch"] button,
[data-surface="launch"] input,
[data-surface="launch"] select,
[data-surface="launch"] textarea{font:inherit}
[data-surface="launch"] :focus-visible{
  outline:2px solid var(--lp-gold);outline-offset:2px;border-radius:8px}
@media(prefers-reduced-motion:reduce){
  [data-surface="launch"] *{transition:none!important;animation:none!important}
}

/* ── shell ──────────────────────────────────────────────────────────────── */
.lp-shell{display:grid;grid-template-columns:250px minmax(0,1fr);
  min-height:100vh;background:var(--lp-void)}
.lp-body{min-width:0;display:flex;flex-direction:column}

/* ── left rail ──────────────────────────────────────────────────────────── */
.lp-rail{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;
  padding:20px 14px 16px;background:
    radial-gradient(560px 300px at 20% -10%,rgba(226,192,120,.09),transparent 65%),
    linear-gradient(180deg,var(--lp-navy) 0%,var(--lp-void) 100%);
  border-right:1px solid var(--lp-line)}
.lp-railbrand{display:flex;align-items:center;gap:11px;padding:2px 8px 18px;
  border-bottom:1px solid var(--lp-line);margin-bottom:14px}
.lp-railbrand .lp-bn{min-width:0}
.lp-railbrand b{display:block;font-size:14.5px;font-weight:700;
  letter-spacing:-.015em;color:var(--lp-ink);line-height:1.2;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lp-railbrand span{display:block;margin-top:3px;font-size:8.5px;font-weight:800;
  letter-spacing:.17em;text-transform:uppercase;color:var(--lp-gold)}
.lp-navgroup{font-size:8.5px;font-weight:800;letter-spacing:.17em;
  text-transform:uppercase;color:var(--lp-ink4);padding:16px 10px 7px}
.lp-nav{display:flex;flex-direction:column;gap:2px}
.lp-navitem{display:flex;align-items:center;gap:10px;padding:9px 11px;
  border-radius:9px;text-decoration:none;font-size:13px;font-weight:500;
  color:var(--lp-ink2);background:none;border:1px solid transparent;
  width:100%;text-align:left;cursor:pointer;
  transition:background .13s,color .13s,border-color .13s}
.lp-navitem:hover{background:var(--lp-navy2);color:var(--lp-ink)}
.lp-navitem.on{background:linear-gradient(90deg,var(--lp-gold-bg),transparent 78%);
  border-color:var(--lp-gold-bd);color:var(--lp-gold2);font-weight:650;
  box-shadow:inset 2px 0 var(--lp-gold)}
.lp-navitem .lp-ni{flex:0 0 16px;display:grid;place-items:center;opacity:.85}
.lp-navitem.on .lp-ni{opacity:1}
.lp-navitem .lp-soon{margin-left:auto;font-size:8px;font-weight:800;
  letter-spacing:.1em;text-transform:uppercase;color:var(--lp-ink4);
  border:1px solid var(--lp-line);border-radius:999px;padding:2px 6px}
.lp-railfill{flex:1 1 auto;min-height:16px}
.lp-railfoot{border-top:1px solid var(--lp-line);padding:13px 10px 0;
  font-size:10.5px;color:var(--lp-ink4);line-height:1.55}
.lp-railfoot b{display:block;color:var(--lp-ink3);font-weight:600;font-size:11px}

/* ── brand mark ─────────────────────────────────────────────────────────
   A TEMPORARY MARK, NOT A FABRICATED LOGO. Initials on a gold-hairline
   plate. When the real asset arrives it replaces the <img>/initial inside
   this box and nothing around it moves. */
.lp-mark{flex:0 0 auto;display:grid;place-items:center;border-radius:11px;
  background:linear-gradient(150deg,var(--lp-navy3),var(--lp-navy));
  border:1px solid var(--lp-gold-bd);color:var(--lp-gold2);
  font-weight:800;letter-spacing:.02em}
.lp-mark.s{width:34px;height:34px;font-size:13px}
.lp-mark.m{width:46px;height:46px;font-size:16px;border-radius:13px}
.lp-mark.l{width:70px;height:70px;font-size:23px;border-radius:18px}
.lp-mark img{max-width:78%;max-height:78%;display:block}

/* ── top bar ────────────────────────────────────────────────────────────── */
.lp-top{position:sticky;top:0;z-index:20;display:flex;align-items:center;
  gap:14px;padding:12px 26px;background:rgba(10,20,35,.86);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--lp-line)}
.lp-eco{display:inline-flex;align-items:center;gap:9px;padding:6px 12px 6px 8px;
  border:1px solid var(--lp-line2);border-radius:999px;background:var(--lp-navy2);
  color:var(--lp-ink);font-size:12px;font-weight:600;cursor:pointer}
.lp-eco:hover{border-color:var(--lp-gold-bd)}
.lp-eco .lp-ecolabel{font-size:8.5px;font-weight:800;letter-spacing:.14em;
  text-transform:uppercase;color:var(--lp-ink4);display:block;line-height:1.2}
.lp-eco b{display:block;font-size:12.5px;font-weight:650;line-height:1.25}
.lp-search{flex:1 1 auto;min-width:0;max-width:420px;position:relative}
.lp-search input{width:100%;padding:9px 12px 9px 34px;border-radius:10px;
  border:1px solid var(--lp-line);background:var(--lp-navy);color:var(--lp-ink);
  font-size:12.5px}
.lp-search input::placeholder{color:var(--lp-ink4)}
.lp-search input:focus{border-color:var(--lp-line2);outline:none;
  box-shadow:0 0 0 3px rgba(90,169,255,.10)}
.lp-search .lp-si{position:absolute;left:11px;top:50%;transform:translateY(-50%);
  color:var(--lp-ink4);pointer-events:none;display:grid}
.lp-topspace{flex:1 1 auto}
.lp-iconbtn{position:relative;width:34px;height:34px;display:grid;
  place-items:center;border-radius:10px;border:1px solid var(--lp-line);
  background:var(--lp-navy2);color:var(--lp-ink2);cursor:pointer}
.lp-iconbtn:hover{color:var(--lp-ink);border-color:var(--lp-line2)}
.lp-dot{position:absolute;top:7px;right:8px;width:6px;height:6px;
  border-radius:50%;background:var(--lp-gold);
  box-shadow:0 0 0 2px var(--lp-navy2)}
.lp-user{display:flex;align-items:center;gap:10px;padding-left:14px;
  border-left:1px solid var(--lp-line)}
.lp-user .lp-un{text-align:right;min-width:0}
.lp-user b{display:block;font-size:12.5px;font-weight:650;color:var(--lp-ink);
  line-height:1.25;white-space:nowrap}
.lp-user span{display:block;font-size:10.5px;color:var(--lp-ink3);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.lp-avatar{width:34px;height:34px;border-radius:50%;display:grid;
  place-items:center;font-size:12px;font-weight:800;color:var(--lp-navy);
  background:linear-gradient(150deg,var(--lp-gold2),var(--lp-gold));
  flex:0 0 auto}

/* ── hero ───────────────────────────────────────────────────────────────── */
.lp-main{padding:24px 26px 64px;min-width:0}
.lp-hero{position:relative;overflow:hidden;border-radius:var(--lp-r2);
  border:1px solid var(--lp-line2);padding:30px 32px;
  background:
    radial-gradient(760px 320px at 88% -30%,rgba(90,169,255,.14),transparent 62%),
    radial-gradient(680px 340px at 4% 118%,rgba(226,192,120,.13),transparent 60%),
    linear-gradient(140deg,var(--lp-navy3) 0%,var(--lp-navy) 52%,var(--lp-void) 100%)}
.lp-hero:after{content:"";position:absolute;inset:0;pointer-events:none;
  border-radius:inherit;box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
.lp-hero-top{display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap;
  position:relative}
.lp-hero-id{display:flex;align-items:center;gap:16px;min-width:0}
.lp-eyebrow{font-size:9px;font-weight:800;letter-spacing:.19em;
  text-transform:uppercase;color:var(--lp-gold);margin:0 0 8px}
.lp-hero h1{margin:0;font-size:31px;font-weight:700;letter-spacing:-.032em;
  line-height:1.08;color:#fff}
.lp-hero .lp-h2{margin:9px 0 0;font-size:15px;font-weight:600;
  color:var(--lp-gold2);letter-spacing:-.01em}
.lp-hero p{margin:13px 0 0;font-size:13.5px;line-height:1.68;
  color:var(--lp-ink2);max-width:74ch}
.lp-hero-side{margin-left:auto;display:flex;flex-direction:column;gap:9px;
  align-items:flex-end}
.lp-chip{display:inline-flex;align-items:center;gap:7px;font-size:10px;
  font-weight:800;letter-spacing:.12em;text-transform:uppercase;
  border-radius:999px;padding:6px 13px;white-space:nowrap;
  background:var(--lp-gold-bg);border:1px solid var(--lp-gold-bd);
  color:var(--lp-gold2)}
.lp-chip.blue{background:var(--lp-blue-bg);border-color:var(--lp-blue-bd);
  color:var(--lp-blue)}
.lp-chip.quiet{background:transparent;border-color:var(--lp-line);
  color:var(--lp-ink3)}
.lp-poweredby{font-size:10px;color:var(--lp-ink4);letter-spacing:.04em}

/* ── implementation lifecycle tracker ───────────────────────────────────── */
.lp-phases{margin-top:20px;border:1px solid var(--lp-line);
  border-radius:var(--lp-r2);background:linear-gradient(180deg,
    var(--lp-navy2),var(--lp-navy));padding:18px 20px 20px}
.lp-phases-h{display:flex;align-items:baseline;justify-content:space-between;
  gap:14px;flex-wrap:wrap;margin-bottom:16px}
.lp-phases-h h2{margin:0;font-size:9.5px;font-weight:800;letter-spacing:.17em;
  text-transform:uppercase;color:var(--lp-ink3)}
.lp-phases-h span{font-size:11.5px;color:var(--lp-ink3)}
.lp-phaserow{display:flex;align-items:flex-start;gap:0;overflow-x:auto;
  padding-bottom:4px;scrollbar-width:thin}
.lp-phase{flex:1 1 0;min-width:104px;display:flex;flex-direction:column;
  align-items:center;text-align:center;position:relative;padding:0 4px}
.lp-phase:before{content:"";position:absolute;top:15px;left:-50%;width:100%;
  height:2px;background:var(--lp-line);z-index:0}
.lp-phase:first-child:before{display:none}
.lp-phase.done:before,.lp-phase.now:before{background:var(--lp-gold-bd)}
.lp-pnum{position:relative;z-index:1;width:31px;height:31px;border-radius:50%;
  display:grid;place-items:center;font-size:11.5px;font-weight:800;
  background:var(--lp-navy);border:1px solid var(--lp-line2);
  color:var(--lp-ink3)}
.lp-phase.done .lp-pnum{background:linear-gradient(150deg,var(--lp-gold2),
  var(--lp-gold));border-color:var(--lp-gold);color:var(--lp-navy)}
.lp-phase.now .lp-pnum{background:var(--lp-navy3);border-color:var(--lp-gold);
  color:var(--lp-gold2);box-shadow:0 0 0 4px rgba(226,192,120,.12)}
.lp-plabel{margin-top:9px;font-size:11px;font-weight:600;line-height:1.35;
  color:var(--lp-ink3)}
.lp-phase.done .lp-plabel{color:var(--lp-ink2)}
.lp-phase.now .lp-plabel{color:var(--lp-gold2);font-weight:700}
.lp-pstate{margin-top:4px;font-size:9px;font-weight:800;letter-spacing:.11em;
  text-transform:uppercase;color:var(--lp-ink4)}
.lp-phase.now .lp-pstate{color:var(--lp-gold)}

/* ── the two-column work area ───────────────────────────────────────────── */
.lp-work{display:grid;grid-template-columns:minmax(0,1fr) 316px;gap:20px;
  margin-top:20px;align-items:start}
.lp-side{display:flex;flex-direction:column;gap:14px;position:sticky;top:74px}

/* ── the white document ─────────────────────────────────────────────────── */
.lp-doc{background:var(--lp-card);border:1px solid var(--lp-cline);
  border-radius:var(--lp-r2);box-shadow:var(--lp-shadow-lift);overflow:hidden;
  color:var(--lp-cink)}
.lp-doc-h{padding:22px 26px 18px;border-bottom:1px solid var(--lp-cline2);
  background:linear-gradient(180deg,#fff,var(--lp-card2))}
.lp-doc-htop{display:flex;align-items:flex-start;gap:18px;flex-wrap:wrap}
.lp-stepno{font-size:9px;font-weight:800;letter-spacing:.18em;
  text-transform:uppercase;color:var(--lp-cink3);margin:0 0 6px}
.lp-doc-h h2{margin:0;font-size:21px;font-weight:700;letter-spacing:-.025em;
  color:var(--lp-cink);line-height:1.15}
.lp-doc-h p{margin:8px 0 0;font-size:13px;line-height:1.65;
  color:var(--lp-cink2);max-width:76ch}
.lp-doc-b{padding:24px 26px 6px}
.lp-doc-f{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
  padding:18px 26px 22px;border-top:1px solid var(--lp-cline2);
  background:var(--lp-card2)}
.lp-doc-f .lp-fspace{flex:1 1 auto}
.lp-doc-f .lp-fnote{font-size:11.5px;color:var(--lp-cink3)}

/* the per-step completion meter that sits in the document header */
.lp-meter{min-width:190px;margin-left:auto;text-align:right}
.lp-meter b{display:block;font-size:23px;font-weight:700;letter-spacing:-.03em;
  color:var(--lp-cink);font-variant-numeric:tabular-nums;line-height:1}
.lp-meter span{display:block;margin-top:4px;font-size:9.5px;font-weight:800;
  letter-spacing:.14em;text-transform:uppercase;color:var(--lp-cink3)}
.lp-bar{margin-top:10px;height:6px;border-radius:999px;overflow:hidden;
  background:var(--lp-card3);border:1px solid var(--lp-cline)}
.lp-bar i{display:block;height:100%;border-radius:999px;
  background:linear-gradient(90deg,#caa155,var(--lp-gold));
  transition:width .35s ease}

/* ── field groups ───────────────────────────────────────────────────────── */
.lp-group{margin-bottom:26px}
.lp-group > h3{margin:0 0 3px;font-size:10px;font-weight:800;letter-spacing:.15em;
  text-transform:uppercase;color:var(--lp-cink3);display:flex;
  align-items:center;gap:10px}
.lp-group > h3:after{content:"";flex:1 1 auto;height:1px;
  background:var(--lp-cline2)}
.lp-group > .lp-gsub{margin:7px 0 15px;font-size:12.5px;line-height:1.6;
  color:var(--lp-cink2);max-width:78ch}
.lp-group > h3 + .lp-fields{margin-top:15px}
.lp-fields{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
  gap:15px 16px}
.lp-f{grid-column:span 6;min-width:0}
.lp-f.c3{grid-column:span 3}
.lp-f.c4{grid-column:span 4}
.lp-f.c8{grid-column:span 8}
.lp-f.c12{grid-column:span 12}
.lp-f > label{display:block;font-size:11px;font-weight:700;letter-spacing:.03em;
  color:var(--lp-cink2);margin-bottom:6px}
.lp-f > label .lp-req{color:var(--lp-bad);margin-left:3px;font-weight:800}
.lp-f > label .lp-opt{color:var(--lp-cink4);font-weight:600;margin-left:5px;
  letter-spacing:0;text-transform:none;font-size:10.5px}
.lp-in{width:100%;padding:10px 12px;border-radius:10px;
  border:1px solid var(--lp-cline);background:#fff;color:var(--lp-cink);
  font-size:13.5px;transition:border-color .12s,box-shadow .12s}
.lp-in::placeholder{color:var(--lp-cink4)}
.lp-in:hover{border-color:var(--lp-cline-strong)}
.lp-in:focus{outline:none;border-color:#b99a52;
  box-shadow:0 0 0 3px rgba(226,192,120,.22)}
textarea.lp-in{min-height:96px;resize:vertical;line-height:1.6}
select.lp-in{appearance:none;cursor:pointer;
  background-image:linear-gradient(45deg,transparent 50%,var(--lp-cink3) 50%),
    linear-gradient(135deg,var(--lp-cink3) 50%,transparent 50%);
  background-position:calc(100% - 17px) 50%,calc(100% - 12px) 50%;
  background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:34px}
.lp-hint{margin:6px 0 0;font-size:11.5px;line-height:1.55;color:var(--lp-cink3)}

/* ── notes / callouts inside the document ───────────────────────────────── */
.lp-note{display:flex;gap:12px;padding:14px 16px;border-radius:12px;
  border:1px solid var(--lp-info-bd);background:var(--lp-info-bg);
  margin-bottom:20px}
.lp-note .lp-nb{min-width:0}
.lp-note b{display:block;font-size:12.5px;font-weight:700;color:var(--lp-info)}
.lp-note p{margin:5px 0 0;font-size:12.5px;line-height:1.62;
  color:var(--lp-cink2)}
.lp-note.secure{border-color:var(--lp-good-bd);background:var(--lp-good-bg)}
.lp-note.secure b{color:var(--lp-good)}
.lp-note.warn{border-color:var(--lp-warn-bd);background:var(--lp-warn-bg)}
.lp-note.warn b{color:var(--lp-warn)}
.lp-note .lp-nicon{flex:0 0 auto;margin-top:1px;color:currentColor}
.lp-note.secure .lp-nicon{color:var(--lp-good)}
.lp-note.warn .lp-nicon{color:var(--lp-warn)}
.lp-note.info .lp-nicon{color:var(--lp-info)}

/* ── collapsible sections ───────────────────────────────────────────────── */
.lp-collapse{border:1px solid var(--lp-cline);border-radius:12px;
  background:var(--lp-card2);margin-bottom:14px;overflow:hidden}
.lp-collapse > button{display:flex;align-items:center;gap:11px;width:100%;
  padding:13px 16px;background:none;border:0;cursor:pointer;text-align:left;
  color:var(--lp-cink)}
.lp-collapse > button:hover{background:var(--lp-card3)}
.lp-collapse > button b{font-size:11.5px;font-weight:800;letter-spacing:.13em;
  text-transform:uppercase;color:var(--lp-cink2)}
.lp-collapse > button .lp-cmeta{margin-left:auto;font-size:11px;
  color:var(--lp-cink3);font-weight:600}
.lp-caret{flex:0 0 auto;color:var(--lp-cink3);transition:transform .16s ease}
.lp-collapse.open .lp-caret{transform:rotate(90deg)}
.lp-collapse-b{padding:4px 16px 16px;border-top:1px solid var(--lp-cline2);
  background:#fff}

/* ── buttons ────────────────────────────────────────────────────────────── */
.lp-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;
  border:1px solid var(--lp-cline-strong);background:#fff;color:var(--lp-cink);
  border-radius:10px;padding:11px 18px;font-size:13px;font-weight:650;
  cursor:pointer;white-space:nowrap;
  transition:border-color .13s,background .13s,box-shadow .13s,transform .13s}
.lp-btn:hover{background:var(--lp-card3);border-color:#93a3b9}
.lp-btn:active{transform:translateY(1px)}
.lp-btn:disabled{opacity:.45;cursor:default;transform:none}
.lp-btn.primary{border-color:#8d7333;color:#221a06;
  background:linear-gradient(150deg,var(--lp-gold2),var(--lp-gold));
  box-shadow:0 1px 2px rgba(80,60,10,.16),0 8px 22px rgba(180,145,60,.24)}
.lp-btn.primary:hover{filter:brightness(1.05);background:
  linear-gradient(150deg,#fbe9bd,#e8caa0)}
.lp-btn.ghost{background:transparent;border-color:var(--lp-cline)}
.lp-btn.small{padding:8px 13px;font-size:12px;border-radius:9px}
/* on the dark chrome */
.lp-dbtn{display:inline-flex;align-items:center;gap:8px;border-radius:10px;
  padding:9px 15px;font-size:12.5px;font-weight:650;cursor:pointer;
  border:1px solid var(--lp-line2);background:var(--lp-navy2);
  color:var(--lp-ink);white-space:nowrap}
.lp-dbtn:hover{border-color:var(--lp-gold-bd);color:var(--lp-gold2)}

/* ── right sidebar cards ────────────────────────────────────────────────── */
.lp-scard{border:1px solid var(--lp-line);border-radius:var(--lp-r);
  background:linear-gradient(180deg,var(--lp-navy2),var(--lp-navy));
  overflow:hidden}
.lp-scard-h{padding:15px 17px 13px;border-bottom:1px solid var(--lp-line)}
.lp-scard-h h3{margin:0;font-size:9.5px;font-weight:800;letter-spacing:.16em;
  text-transform:uppercase;color:var(--lp-ink3)}
.lp-scard-b{padding:15px 17px 17px}

/* the big ring + list */
.lp-ring{display:flex;align-items:center;gap:15px}
.lp-ring svg{flex:0 0 auto}
.lp-ring .lp-rt b{display:block;font-size:27px;font-weight:700;
  letter-spacing:-.03em;color:var(--lp-gold2);line-height:1;
  font-variant-numeric:tabular-nums}
.lp-ring .lp-rt span{display:block;margin-top:6px;font-size:11.5px;
  color:var(--lp-ink3);line-height:1.5}
.lp-steps{list-style:none;margin:16px 0 0;padding:0;display:flex;
  flex-direction:column;gap:2px}
.lp-steps li{margin:0}
.lp-steplink{display:flex;align-items:center;gap:10px;width:100%;
  padding:9px 10px;border-radius:9px;background:none;border:1px solid transparent;
  cursor:pointer;text-align:left;color:var(--lp-ink2);font-size:12.5px;
  transition:background .13s,color .13s,border-color .13s}
.lp-steplink:hover{background:var(--lp-navy3);color:var(--lp-ink)}
.lp-steplink.on{background:var(--lp-gold-bg);border-color:var(--lp-gold-bd);
  color:var(--lp-gold2);font-weight:650}
.lp-tick{flex:0 0 auto;width:19px;height:19px;border-radius:50%;display:grid;
  place-items:center;font-size:9.5px;font-weight:800;border:1px solid
  var(--lp-line2);color:var(--lp-ink4);background:var(--lp-navy)}
.lp-steplink.done .lp-tick{background:rgba(35,239,178,.14);
  border-color:rgba(35,239,178,.42);color:#4fe6b6}
.lp-steplink.on .lp-tick{border-color:var(--lp-gold);color:var(--lp-gold2);
  background:var(--lp-navy3)}
.lp-steplink .lp-sn{flex:1 1 auto;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.lp-steplink .lp-sp{flex:0 0 auto;font-size:10.5px;font-weight:700;
  color:var(--lp-ink4);font-variant-numeric:tabular-nums}
.lp-steplink.done .lp-sp{color:#4fe6b6}

/* help + guide */
.lp-help p{margin:0;font-size:12.5px;line-height:1.65;color:var(--lp-ink2)}
.lp-help .lp-hrow{display:flex;align-items:center;gap:10px;margin-top:13px;
  font-size:12.5px;color:var(--lp-ink)}
.lp-help .lp-hrow svg{color:var(--lp-gold);flex:0 0 auto}
.lp-guide{display:flex;gap:13px;align-items:flex-start;width:100%;
  padding:15px 17px;background:none;border:0;cursor:pointer;text-align:left}
.lp-guide:hover{background:var(--lp-navy3)}
.lp-guide .lp-gi{flex:0 0 auto;width:38px;height:38px;border-radius:10px;
  display:grid;place-items:center;background:var(--lp-gold-bg);
  border:1px solid var(--lp-gold-bd);color:var(--lp-gold2)}
.lp-guide b{display:block;font-size:12.5px;font-weight:650;color:var(--lp-ink)}
.lp-guide span{display:block;margin-top:4px;font-size:11.5px;
  color:var(--lp-ink3);line-height:1.5}

/* ── upload tiles (VISUAL ONLY in Stage 1) ──────────────────────────────── */
.lp-uploads{display:grid;grid-template-columns:repeat(auto-fill,minmax(216px,1fr));
  gap:13px}
.lp-upload{display:flex;flex-direction:column;gap:9px;padding:16px 16px 15px;
  border:1.5px dashed var(--lp-cline-strong);border-radius:12px;
  background:var(--lp-card2);text-align:left;cursor:pointer;width:100%;
  transition:border-color .13s,background .13s}
.lp-upload:hover{border-color:#b99a52;background:#fffdf7}
.lp-upload .lp-ui{width:34px;height:34px;border-radius:9px;display:grid;
  place-items:center;background:#fff;border:1px solid var(--lp-cline);
  color:var(--lp-cink3)}
.lp-upload:hover .lp-ui{color:#a8853f;border-color:var(--lp-gold-bd)}
.lp-upload b{font-size:12.5px;font-weight:650;color:var(--lp-cink);
  line-height:1.35}
.lp-upload span{font-size:11px;color:var(--lp-cink3);line-height:1.5}
.lp-upload .lp-utag{margin-top:2px;font-size:9px;font-weight:800;
  letter-spacing:.11em;text-transform:uppercase;color:var(--lp-cink4)}
.lp-upload.filled{border-style:solid;border-color:var(--lp-good-bd);
  background:var(--lp-good-bg)}
.lp-upload.filled .lp-ui{color:var(--lp-good);border-color:var(--lp-good-bd)}
.lp-upload.filled .lp-utag{color:var(--lp-good)}

/* swatch row for brand colours */
.lp-swatches{display:flex;gap:10px;flex-wrap:wrap}
.lp-swatch{display:flex;align-items:center;gap:9px;padding:8px 12px 8px 8px;
  border:1px solid var(--lp-cline);border-radius:10px;background:#fff}
.lp-swatch i{width:26px;height:26px;border-radius:7px;display:block;
  border:1px solid rgba(0,0,0,.10)}
.lp-swatch .lp-sw{min-width:0}
.lp-swatch b{display:block;font-size:11.5px;font-weight:650;
  color:var(--lp-cink)}
.lp-swatch span{display:block;font-size:10.5px;color:var(--lp-cink3);
  font-variant-numeric:tabular-nums}

/* ── the customer process flow ──────────────────────────────────────────── */
.lp-flow{display:flex;align-items:stretch;gap:0;overflow-x:auto;
  padding:4px 0 8px;margin-bottom:20px}
.lp-fstep{flex:1 1 0;min-width:150px;display:flex;align-items:center;gap:0}
.lp-fbox{flex:1 1 auto;border:1px solid var(--lp-cline);border-radius:12px;
  background:var(--lp-card2);padding:13px 14px;min-width:0}
.lp-fbox b{display:block;font-size:9px;font-weight:800;letter-spacing:.14em;
  text-transform:uppercase;color:#a8853f}
.lp-fbox span{display:block;margin-top:6px;font-size:12.5px;font-weight:600;
  color:var(--lp-cink);line-height:1.4}
.lp-farrow{flex:0 0 26px;display:grid;place-items:center;color:var(--lp-cink4)}
.lp-fstep:last-child .lp-farrow{display:none}

/* ── what gets launched ─────────────────────────────────────────────────── */
.lp-deliver{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));
  gap:11px}
.lp-ditem{display:flex;gap:11px;align-items:flex-start;padding:13px 14px;
  border:1px solid var(--lp-cline);border-radius:11px;background:var(--lp-card2)}
.lp-ditem .lp-dnum{flex:0 0 auto;width:21px;height:21px;border-radius:50%;
  display:grid;place-items:center;font-size:10px;font-weight:800;
  background:#fff;border:1px solid var(--lp-gold-bd);color:#a8853f}
.lp-ditem b{display:block;font-size:12.5px;font-weight:650;color:var(--lp-cink);
  line-height:1.4}
.lp-ditem span{display:block;margin-top:4px;font-size:11.5px;
  color:var(--lp-cink3);line-height:1.5}

/* ── review & submit ────────────────────────────────────────────────────── */
.lp-review{display:flex;flex-direction:column;gap:9px;margin-bottom:6px}
.lp-rrow{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;
  gap:14px;align-items:center;padding:14px 16px;border-radius:12px;
  border:1px solid var(--lp-cline);background:#fff}
.lp-rrow.ok{border-color:var(--lp-good-bd);background:var(--lp-good-bg)}
.lp-rrow.miss{border-color:var(--lp-warn-bd);background:var(--lp-warn-bg)}
.lp-rmark{width:24px;height:24px;border-radius:50%;display:grid;
  place-items:center;font-size:11px;font-weight:800;background:#fff;
  border:1px solid var(--lp-cline);color:var(--lp-cink3)}
.lp-rrow.ok .lp-rmark{background:var(--lp-good);border-color:var(--lp-good);
  color:#fff}
.lp-rrow.miss .lp-rmark{background:var(--lp-warn);border-color:var(--lp-warn);
  color:#fff}
.lp-rrow .lp-rname{min-width:0}
.lp-rrow .lp-rname b{display:block;font-size:13.5px;font-weight:650;
  color:var(--lp-cink)}
.lp-rrow .lp-rname span{display:block;margin-top:3px;font-size:11.5px;
  color:var(--lp-cink2);line-height:1.5}
.lp-rpct{font-size:12.5px;font-weight:800;color:var(--lp-cink2);
  font-variant-numeric:tabular-nums}

/* ── signature (VISUAL PROTOTYPE ONLY) ──────────────────────────────────── */
.lp-sigtabs{display:inline-flex;gap:4px;padding:4px;border-radius:11px;
  background:var(--lp-card3);border:1px solid var(--lp-cline);margin-bottom:14px}
.lp-sigtab{border:0;background:none;border-radius:8px;padding:7px 15px;
  font-size:12px;font-weight:650;color:var(--lp-cink2);cursor:pointer}
.lp-sigtab.on{background:#fff;color:var(--lp-cink);
  box-shadow:0 1px 2px rgba(20,29,49,.10)}
.lp-sigpad{height:130px;border-radius:12px;border:1.5px dashed
  var(--lp-cline-strong);background:
    repeating-linear-gradient(45deg,#fbfcfe,#fbfcfe 9px,#f6f8fc 9px,#f6f8fc 18px);
  display:grid;place-items:center;position:relative}
.lp-sigpad span{font-size:12px;color:var(--lp-cink3)}
.lp-sigpad:after{content:"";position:absolute;left:24px;right:24px;bottom:30px;
  border-bottom:1px solid var(--lp-cline-strong)}
.lp-sigtype{width:100%;padding:20px 16px;border-radius:12px;
  border:1.5px solid var(--lp-cline);background:#fff;color:var(--lp-cink);
  font-size:29px;font-family:"Segoe Script","Bradley Hand",cursive;
  text-align:center}
.lp-sigtype:focus{outline:none;border-color:#b99a52;
  box-shadow:0 0 0 3px rgba(226,192,120,.22)}
.lp-check{display:flex;gap:11px;align-items:flex-start;padding:15px 16px;
  border:1px solid var(--lp-cline);border-radius:12px;background:var(--lp-card2);
  cursor:pointer;margin:16px 0 4px}
.lp-check input{margin-top:2px;width:17px;height:17px;accent-color:#a8853f;
  flex:0 0 auto;cursor:pointer}
.lp-check span{font-size:12.5px;line-height:1.62;color:var(--lp-cink2)}
.lp-check.on{border-color:var(--lp-good-bd);background:var(--lp-good-bg)}

/* ── stage-1 marker ─────────────────────────────────────────────────────
   The prototype says so, on the screen, in the design's own language. It is
   removed when the backend lands — not left behind to be discovered later. */
.lp-proto{display:flex;align-items:center;gap:11px;flex-wrap:wrap;
  padding:9px 16px;border-bottom:1px solid var(--lp-line);
  background:linear-gradient(90deg,rgba(226,192,120,.10),transparent 70%)}
.lp-proto b{font-size:9px;font-weight:800;letter-spacing:.16em;
  text-transform:uppercase;color:var(--lp-gold2);border:1px solid
  var(--lp-gold-bd);border-radius:999px;padding:3px 9px}
.lp-proto span{font-size:11.5px;color:var(--lp-ink3)}

/* transient confirmation for the prototype's Save actions */
.lp-flash{display:inline-flex;align-items:center;gap:7px;font-size:12px;
  font-weight:650;color:var(--lp-good);background:var(--lp-good-bg);
  border:1px solid var(--lp-good-bd);border-radius:999px;padding:6px 13px}

/* ── mobile rail ────────────────────────────────────────────────────────── */
.lp-railtoggle{display:none}

@media(max-width:1180px){
  .lp-work{grid-template-columns:minmax(0,1fr)}
  /* THE PROGRESS PANEL MOVES, IT DOES NOT DISAPPEAR. Knowing how far through
     you are is the reason somebody finishes a twenty-field form. */
  .lp-side{position:static;flex-direction:row;flex-wrap:wrap}
  .lp-side > *{flex:1 1 280px}
}
@media(max-width:980px){
  .lp-shell{grid-template-columns:minmax(0,1fr)}
  .lp-rail{position:static;height:auto;padding:14px 14px 16px}
  .lp-rail .lp-navwrap{display:none}
  .lp-rail.open .lp-navwrap{display:block}
  .lp-railfill,.lp-railfoot{display:none}
  .lp-railbrand{border-bottom:0;margin-bottom:0;padding-bottom:0}
  .lp-railtoggle{display:inline-flex;align-items:center;gap:8px;margin-left:auto;
    border:1px solid var(--lp-line2);background:var(--lp-navy2);
    color:var(--lp-ink2);border-radius:9px;padding:7px 12px;font-size:12px;
    font-weight:650;cursor:pointer}
  .lp-nav{margin-top:12px}
  .lp-main{padding:18px 16px 56px}
  .lp-top{padding:10px 16px}
  .lp-search{max-width:none}
  .lp-user .lp-un{display:none}
}
@media(max-width:720px){
  .lp-hero{padding:24px 20px}
  .lp-hero h1{font-size:25px}
  .lp-hero-side{margin-left:0;align-items:flex-start;flex-direction:row;
    flex-wrap:wrap}
  .lp-doc-h,.lp-doc-b,.lp-doc-f{padding-left:18px;padding-right:18px}
  .lp-doc-h h2{font-size:19px}
  .lp-meter{margin-left:0;text-align:left;width:100%}
  /* EVERY FIELD FULL WIDTH. A half-width city field beside a half-width
     state field is unusable at this size and looks broken. */
  .lp-f,.lp-f.c3,.lp-f.c4,.lp-f.c8{grid-column:span 12}
  .lp-doc-f{flex-direction:column;align-items:stretch}
  .lp-doc-f .lp-btn{width:100%}
  .lp-doc-f .lp-fspace{display:none}
  .lp-phase{min-width:88px}
  .lp-rrow{grid-template-columns:auto minmax(0,1fr);row-gap:8px}
  .lp-rrow .lp-rpct,.lp-rrow .lp-btn{grid-column:2}
  .lp-search{display:none}
}
`

let injected = false

export default function LaunchStyles() {
  useEffect(() => {
    if (injected || document.getElementById('lp-styles')) return
    const el = document.createElement('style')
    el.id = 'lp-styles'
    el.textContent = CSS
    document.head.appendChild(el)
    injected = true
  }, [])
  return null
}
