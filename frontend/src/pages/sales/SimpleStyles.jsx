/**
 * Styles for the simplified Sales Workspace (Lane F, #281).
 *
 * Values are taken from Mike's approved package
 * (evosys_approved_six_screen_prototype.html, "Simplified Sales Workflow –
 * My Day (New Design)"), renamed to `ss-*` and nested under `.sw-scope` so
 * nothing outside the Sales Workspace can be reached. The shell overrides are
 * scoped to `.ss-shell`, the class SalesShell puts on its frame.
 */
import { useEffect } from 'react'

const CSS = `
.sw-scope.ss-shell{
  --ss-nav:#071a2c; --ss-nav-active:#16436c; --ss-mint:#00805f; --ss-mint-dark:#006a4e;
  --ss-blue:#0f6bdc; --ss-blue-dark:#0b58b8; --ss-ink:#071a35; --ss-sub:#66758b;
  --ss-line:#e4eaf2; --ss-bg:#f7fafc; --ss-red:#e44e61; --ss-red-bg:#fff0f1;
  --ss-amber:#fa9d18; --ss-amber-bg:#fff4e5; --ss-shadow:0 3px 18px rgba(25,52,75,.035);
  color:var(--ss-ink); font-size:14px;
}

/* ── frame ──────────────────────────────────────────────────────────── */
.ss-shell .sw-app{grid-template-columns:224px minmax(0,1fr);background:var(--ss-bg)}
.ss-shell .sw-sidebar{background:linear-gradient(165deg,#081d2e,#071728);color:#c8dbec;
  padding:24px 10px 20px;border-right:0;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#27455f transparent}
.ss-shell .sw-brand{gap:11px;padding:0 10px 26px;color:#fff}
.ss-shell .sw-brandmark{width:30px;height:30px;border-radius:7px;background:#09ab88;color:#002620;font-size:16px}
.ss-shell .sw-brand b{font-size:14px;font-weight:800}
.ss-shell .sw-brand small{color:#a1b9c8;font-size:10px;margin-top:2px}
.ss-shell .sw-profile{padding:12px 8px;border:1px solid #27455f;border-radius:10px;background:#102a41;margin:0 2px 23px}
.ss-shell .sw-avatar{width:32px;height:32px;background:#315b7f;color:#d7f0ff;font-size:10px}
.ss-shell .sw-profile b{font-size:11px;color:#fff}
.ss-shell .sw-profile small{color:#8fb0c7;font-size:10px;margin-top:3px}
.ss-shell .sw-nav{gap:5px}
.ss-shell .sw-nav a{color:#bbd0e3;border-radius:6px;gap:11px;padding:12px 13px;font-size:12px;font-weight:500}
.ss-shell .sw-nav a svg{width:16px;height:16px;flex:0 0 auto}
.ss-shell .sw-nav a:hover{background:#112f4b;color:#fff}
.ss-shell .sw-nav a.sw-on{background:var(--ss-nav-active);color:#fff;font-weight:700;box-shadow:none}
.ss-shell .sw-nav .sw-count{color:#cceeff;background:#315877;font-size:10px;padding:2px 7px}
.ss-shell .sw-navtitle{color:#6f8ca3;font-size:9px;padding:14px 13px 7px}
.ss-shell .ss-more{border:0;background:transparent;color:#7f9bb1;font-size:11px;font-weight:600;
  padding:12px 13px 6px;text-align:left;cursor:pointer;width:100%;display:flex;align-items:center;gap:8px;border-radius:6px}
.ss-shell .ss-more:hover{color:#fff;background:#112f4b}
.ss-shell .sw-mini{color:#7295ac;font-size:10px;border-top:1px solid #223d52;padding:13px 10px;line-height:1.6}
.ss-shell .sw-mini b{display:block;color:#9ebacd}
.ss-shell .sw-mini button{color:#9ebacd;font-size:10px}

.ss-shell .sw-topbar{background:#fff;border-bottom:1px solid var(--ss-line);min-height:86px;padding:14px 31px}
.ss-shell .sw-topbar h1{font-size:24px;font-weight:800;letter-spacing:-.8px}
.ss-shell .sw-topbar p{font-size:12px;margin:6px 0 0;color:var(--ss-sub)}
.ss-shell .ss-iconbtn{border:0;background:transparent;color:#203953;padding:8px;border-radius:8px;display:grid;
  place-items:center;cursor:pointer;text-decoration:none}
.ss-shell .ss-iconbtn:hover{background:#edf3f9}
.ss-shell .ss-iconbtn svg{width:20px;height:20px}
.ss-shell .ss-circle{width:31px;height:31px;border-radius:50%;background:#102d4e;color:#fff;display:grid;
  place-items:center;font-size:10px;font-weight:800;flex:0 0 auto}
.ss-shell .sw-body{padding:25px 30px 55px;max-width:1560px;width:100%;margin:0 auto}

/* ── buttons ────────────────────────────────────────────────────────── */
.ss-shell .ss-btn{border:1px solid transparent;border-radius:7px;padding:10px 15px;font-size:12px;font-weight:700;
  background:#fff;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:39px;
  white-space:nowrap;cursor:pointer;text-decoration:none;color:inherit}
.ss-shell .ss-btn svg{width:17px;height:17px;flex:0 0 auto}
.ss-shell .ss-btn:active{transform:translateY(1px)}
.ss-shell .ss-btn:disabled,.ss-shell .ss-btn[aria-disabled="true"]{opacity:.45;cursor:not-allowed;transform:none}
.ss-shell .ss-btn.ss-blue{background:var(--ss-blue);color:#fff}
.ss-shell .ss-btn.ss-blue:hover{background:var(--ss-blue-dark)}
.ss-shell .ss-btn.ss-green{background:var(--ss-mint);color:#fff}
.ss-shell .ss-btn.ss-green:hover{background:var(--ss-mint-dark)}
.ss-shell .ss-btn.ss-soft{background:#f2f6fa;border-color:#e0e8f0;color:#2c4056}
.ss-shell .ss-btn.ss-white{background:#fff;border-color:#dce6f0;color:#3d5167}
.ss-shell .ss-btn.ss-xs{min-height:32px;padding:7px 11px}
.ss-shell .ss-link{background:transparent;border:0;color:#1565c0;font-size:12px;font-weight:700;padding:6px 0;
  cursor:pointer;display:inline-flex;align-items:center;gap:4px;text-decoration:none}
.ss-shell .ss-square{display:grid;place-items:center;width:35px;height:35px;color:#fff;border:0;border-radius:7px;
  cursor:pointer;text-decoration:none;flex:0 0 auto}
.ss-shell .ss-square svg{width:17px;height:17px}
.ss-shell .ss-square.ss-green{background:#00805f}
.ss-shell .ss-square.ss-blue{background:#0f6bdc}
.ss-shell .ss-square[aria-disabled="true"]{background:#ebeff4;color:#9aaabb;cursor:not-allowed}

/* ── My Day ─────────────────────────────────────────────────────────── */
.ss-shell .ss-hero{border:1px solid #deebf8;background:linear-gradient(110deg,#e5f3ff,#eaf7ff);border-radius:9px;
  padding:23px 28px;display:flex;gap:20px;align-items:center;margin-bottom:20px}
.ss-shell .ss-sun{font-size:37px;line-height:1}
.ss-shell .ss-hero h2{font-size:25px;margin:0 0 6px;letter-spacing:-.7px}
.ss-shell .ss-hero p{margin:0;color:#334966;font-size:13px}
.ss-shell .ss-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:22px}
.ss-shell .ss-stat{border:1px solid var(--ss-line);background:#fff;min-height:101px;border-radius:9px;padding:20px;
  box-shadow:var(--ss-shadow);text-align:left;color:inherit;font:inherit;cursor:pointer;width:100%;
  display:flex;flex-direction:column;justify-content:flex-start;align-items:flex-start}
.ss-shell .ss-stat:hover{border-color:#c9d8e8}
.ss-shell .ss-stat .ss-num{font-weight:800;font-size:27px;margin-bottom:4px;letter-spacing:-1px}
.ss-shell .ss-stat .ss-name{font-size:12px;color:#58667c;font-weight:550}
.ss-shell .ss-stat .ss-detail{color:#c4303c;font-size:10px;margin-top:5px}
.ss-shell .ss-stat .ss-detail.ss-calm{color:#5f6e83}
.ss-shell .ss-quick{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:21px}
.ss-shell .ss-quick button{border:0;display:flex;align-items:center;gap:16px;min-height:79px;padding:16px 18px;
  border-radius:9px;color:#fff;text-align:left;cursor:pointer}
.ss-shell .ss-quick .ss-qgreen{background:linear-gradient(120deg,#058a63,#047755)}
.ss-shell .ss-quick .ss-qblue{background:linear-gradient(120deg,#1673e6,#0b5bd0)}
.ss-shell .ss-quick .ss-qgray{background:linear-gradient(120deg,#dce3ea,#cdd7e0);color:#112e46}
.ss-shell .ss-quick svg{width:29px;height:29px;flex:0 0 auto}
.ss-shell .ss-quick strong{display:block;font-size:15px}
.ss-shell .ss-quick small{display:block;margin-top:4px;font-size:11px;opacity:.87}
.ss-shell .ss-columns{display:grid;grid-template-columns:1.4fr .68fr;gap:15px}
.ss-shell .ss-panel{background:#fff;border:1px solid var(--ss-line);border-radius:9px;overflow:hidden;box-shadow:var(--ss-shadow)}
.ss-shell .ss-panel + .ss-panel{margin-top:15px}
.ss-shell .ss-panelhd{padding:17px 19px;border-bottom:1px solid var(--ss-line);display:flex;align-items:center;
  justify-content:space-between;gap:12px}
.ss-shell .ss-panelhd h3{font-size:13px;margin:0;font-weight:800}
.ss-shell .ss-panelhd p{margin:3px 0 0;color:#5f6e83;font-size:11px}
.ss-shell .ss-panelbody{padding:20px}
.ss-shell .ss-prior{display:flex;gap:12px;align-items:center;padding:13px 0;border-bottom:1px solid #f0f3f6;font-size:12px;color:#35475a}
.ss-shell .ss-prior:last-child{border:0}
.ss-shell .ss-prior .ss-sp{flex:1}
.ss-shell .ss-step{background:#ff6981;color:#89192c;width:29px;height:29px;border-radius:50%;display:grid;
  place-items:center;font-weight:800;font-size:12px;flex:0 0 auto}
.ss-shell .ss-step.ss-blue-step{background:#9ac9ff;color:#0d5ac5}
.ss-shell .ss-step.ss-green-step{background:#a9ead2;color:#0b7655}
.ss-shell .ss-step.ss-done{background:#e7f7ee;color:#078154}
.ss-shell .ss-motivate{background:linear-gradient(130deg,#e9fbf4,#dff8ee);border-radius:8px;display:flex;align-items:center;
  justify-content:center;flex-direction:column;min-height:191px;text-align:center;padding:20px;color:#075b42}
.ss-shell .ss-motivate .ss-trophy{font-size:38px;margin-bottom:10px}
.ss-shell .ss-motivate h3{font-size:15px;margin:0 0 8px}
.ss-shell .ss-motivate p{font-size:11px;margin:0;max-width:210px;line-height:1.5;color:#47826e}

/* ── personal vs team (manager My Day) ──────────────────────────────── */
.ss-shell .ss-scopehead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 10px}
.ss-shell .ss-scopehead h2{font-size:14px;margin:0;font-weight:800}
.ss-shell .ss-scopehead span{font-size:11px;color:#5f6e83}
.ss-shell .ss-teamrow{border:1px dashed #b9c8d8;background:#f3f6fa;border-radius:9px;padding:14px 18px;margin-bottom:21px}
.ss-shell .ss-teamrow-h{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.ss-shell .ss-teamrow-h h2{font-size:14px;margin:0;font-weight:800;display:inline}
.ss-shell .ss-teamrow-h span{font-size:11px;color:#5f6e83;margin-left:10px}
.ss-shell .ss-teamstats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
.ss-shell .ss-teamstat{background:#fff;border:1px solid var(--ss-line);border-radius:7px;padding:10px 12px}
.ss-shell .ss-teamstat b{display:block;font-size:19px;font-weight:800}
.ss-shell .ss-teamstat span{font-size:11px;color:#4f5d70}

/* ── sections / toolbar / lists ─────────────────────────────────────── */
.ss-shell .ss-sections{display:flex;gap:14px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:14px}
.ss-shell .ss-sections h2{font-size:19px;margin:0}
.ss-shell .ss-sections p{margin:5px 0 0;color:#5f6e83;font-size:12px}
.ss-shell .ss-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:18px 0 14px;flex-wrap:wrap}
.ss-shell .ss-filters{display:flex;gap:7px;flex-wrap:wrap}
.ss-shell .ss-pill{font-size:11px;padding:8px 13px;border:1px solid #e0eaf5;background:#f4f8fe;color:#305277;
  border-radius:6px;font-weight:700;cursor:pointer}
.ss-shell .ss-pill[aria-pressed="true"]{background:#e7f2ff;color:#0857b8;border-color:#c8e2ff}
.ss-shell .ss-pill.ss-alert{background:#fff0f2;color:#ad2a3c;border-color:#ffd8de}
.ss-shell .ss-pill.ss-alert[aria-pressed="true"]{background:#ffe0e5}
.ss-shell .ss-search{display:flex;align-items:center;background:#fff;border:1px solid #dce7f2;border-radius:7px;gap:7px;
  padding:0 11px;min-height:40px;min-width:260px;max-width:470px;flex:1}
.ss-shell .ss-search input{border:0;outline:none;width:100%;font-size:12px;color:var(--ss-ink);background:transparent;padding:10px 0}
.ss-shell .ss-search svg{width:16px;height:16px;color:#8ca0b8;flex:0 0 auto}
.ss-shell .ss-count-note{color:#5f6e83;font-size:11px}
.ss-shell .ss-listrow{display:flex;align-items:center;gap:13px;padding:13px 15px;border-bottom:1px solid #edf1f6;min-height:69px}
.ss-shell .ss-listrow:last-child{border-bottom:0}
.ss-shell .ss-avatar{display:grid;place-items:center;border-radius:50%;width:37px;height:37px;background:#e6ecf3;
  color:#41546b;font-size:12px;font-weight:800;flex:none}
.ss-shell .ss-personinfo{min-width:0;flex:1;background:none;border:0;padding:0;text-align:left;color:inherit;cursor:pointer}
.ss-shell .ss-personinfo strong{display:block;color:#142d48;font-size:12px;font-weight:800}
.ss-shell .ss-personinfo small{display:block;margin-top:3px;font-size:10px;color:#5f6e83;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.ss-shell .ss-rowmeta{min-width:175px;max-width:240px;font-size:11px;color:#5f6e83}
.ss-shell .ss-rowmeta span{display:block;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ss-shell .ss-status{display:inline-block;border-radius:5px;font-size:10px;padding:5px 7px;font-weight:750;
  background:#f2f6fa;color:#52657a}
.ss-shell .ss-rowmeta .ss-status{display:block}
.ss-shell .ss-status.ss-red{background:var(--ss-red-bg);color:#b02a3e}
.ss-shell .ss-status.ss-amber{background:var(--ss-amber-bg);color:#94550a}
.ss-shell .ss-status.ss-sblue{background:#eaf3ff;color:#246bd5}
.ss-shell .ss-status.ss-sgreen{background:#e7f7ee;color:#078154}
.ss-shell .ss-rowactions{display:flex;gap:8px;align-items:center}
.ss-shell .ss-emptylist{padding:48px;text-align:center;color:#5f6e83;font-size:12px}
.ss-shell .ss-emptylist strong{display:block;color:#405269;font-size:13px;margin-bottom:6px}
.ss-shell .ss-skel{height:12px;border-radius:6px;margin:12px 0;
  background:linear-gradient(90deg,#eef3f8,#e2e9f1,#eef3f8);background-size:200% 100%;animation:ss-sh 1.2s infinite}
.ss-shell .ss-sr{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}
@keyframes ss-sh{0%{background-position:200% 0}100%{background-position:-200% 0}}
.ss-shell .ss-err{background:var(--ss-red-bg);border:1px solid #ffd8de;color:#b23447;border-radius:8px;
  padding:11px 13px;font-size:12px;margin-bottom:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.ss-shell .ss-okbar{background:#eaf9f2;border:1px solid #c8ecdf;color:#167a5d;border-radius:8px;padding:11px 13px;
  font-size:12px;margin-bottom:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}

/* ── Lead Details ───────────────────────────────────────────────────── */
.ss-shell .ss-back{font-weight:750;color:#1565c0;border:0;background:transparent;padding:0 0 16px 0;display:flex;
  gap:6px;align-items:center;font-size:12px;cursor:pointer}
.ss-shell .ss-back svg{width:16px;height:16px}
.ss-shell .ss-detailbar{display:flex;gap:12px;align-items:center;margin:0 0 20px}
.ss-shell .ss-detailbar .ss-avatar{width:61px;height:61px;background:linear-gradient(145deg,#abd3ff,#5ba7fc);color:#133f80;font-size:22px}
.ss-shell .ss-detailbar h2{font-size:19px;margin:0 0 3px}
.ss-shell .ss-detailbar p{margin:0 0 5px;color:#5f6e83;font-size:12px}
.ss-shell .ss-detail-actions{margin-left:auto;display:flex;gap:7px;flex-wrap:wrap}
.ss-shell .ss-tabs{display:flex;border-bottom:1px solid #e3e9f2;gap:16px;padding:0 16px;overflow-x:auto}
.ss-shell .ss-tab{border:0;border-bottom:2px solid transparent;background:transparent;color:#5f6e83;padding:14px 9px;
  font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap}
.ss-shell .ss-tab[aria-selected="true"]{border-color:#1682fa;color:#0b5cc0}
.ss-shell .ss-twobox{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.ss-shell .ss-twobox h3{font-size:15px;margin:0 0 14px}
.ss-shell .ss-field{display:flex;flex-direction:column;gap:7px;margin-bottom:16px}
.ss-shell .ss-field label,.ss-shell .ss-field .ss-label{font-size:12px;font-weight:750;color:#17314b}
.ss-shell .ss-field input,.ss-shell .ss-field select,.ss-shell .ss-field textarea{width:100%;border:1px solid #dce4ee;
  border-radius:7px;background:#fff;padding:10px 12px;color:#283e58;font-size:12px;outline-color:#8abaff}
.ss-shell .ss-field textarea{resize:vertical;min-height:87px}
.ss-shell .ss-field small{color:#5f6e83;font-size:11px}
.ss-shell .ss-info-item{display:flex;align-items:center;gap:11px;padding:9px 0;color:#52657a;font-size:12px;word-break:break-word}
.ss-shell .ss-info-item svg{width:16px;height:16px;color:#6685ac;flex:0 0 auto}
.ss-shell .ss-info-item a{color:#52657a;text-decoration:none}
.ss-shell .ss-info-item a:hover{color:#1565c0;text-decoration:underline}
.ss-shell .ss-activityline{display:flex;align-items:flex-start;gap:10px;margin:15px 0}
.ss-shell .ss-activityline .ss-dot{width:9px;height:9px;border:2px solid #177af0;border-radius:50%;margin-top:4px;flex:none}
.ss-shell .ss-activityline strong{font-size:11px}
.ss-shell .ss-activityline p{margin:3px 0;color:#5f6e83;font-size:11px;white-space:pre-wrap;word-break:break-word}
.ss-shell .ss-divide{border-top:1px solid var(--ss-line)}
.ss-shell .ss-callout{background:#eef8ff;border:1px solid #d7eafb;border-radius:8px;padding:13px;color:#3b6787;font-size:12px;line-height:1.55}
.ss-shell .ss-callout.ss-wonbox{background:#e9fbf3;border-color:#c9f3dc;color:#14684a}

/* ── Book a Demo ────────────────────────────────────────────────────── */
.ss-shell .ss-twoform{display:grid;grid-template-columns:1.18fr .8fr;gap:15px;align-items:start}
.ss-shell .ss-formpanel{padding:20px}
.ss-shell .ss-formside{background:#e9f5ff;border-radius:9px;padding:29px 18px;display:flex;flex-direction:column;
  align-items:center;text-align:center;gap:10px;justify-content:center;color:#1553a2;min-height:390px}
.ss-shell .ss-formside h3{color:#112c50;font-size:21px;margin:0}
.ss-shell .ss-formside p{font-size:12px;color:#5f6e83;line-height:1.5;margin:0}
.ss-shell .ss-formside .ss-icon{font-size:47px}
.ss-shell .ss-formside .ss-btn{margin:10px 0 0;width:100%}
.ss-shell .ss-choice{display:inline-flex;align-items:center;gap:6px;margin:0 22px 6px 0;font-size:12px;color:#385169;cursor:pointer;white-space:nowrap}
.ss-shell .ss-choice input{accent-color:#2587fa}
.ss-shell .ss-times{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.ss-shell .ss-time{border:1px solid #dce6f0;background:#fff;border-radius:6px;padding:6px 9px;font-size:11px;
  font-weight:700;color:#3d5167;cursor:pointer}
.ss-shell .ss-time[aria-pressed="true"]{background:var(--ss-blue);border-color:var(--ss-blue);color:#fff}

/* ── Pipeline ───────────────────────────────────────────────────────── */
.ss-shell .ss-boards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;align-items:start}
.ss-shell .ss-boardcol{background:#f1f7fe;border-radius:8px;padding:10px;min-height:375px;border:1px solid #d8eafc}
.ss-shell .ss-boardcol.ss-c2{background:#fff2f3;border-color:#ffeaed}
.ss-shell .ss-boardcol.ss-c3{background:#f3f0ff;border-color:#e7defd}
.ss-shell .ss-boardcol.ss-c4{background:#e9fbf3;border-color:#dbf5e8}
.ss-shell .ss-colheader{display:flex;align-items:center;justify-content:space-between;gap:5px;margin-bottom:13px}
.ss-shell .ss-colheader strong{font-size:12px}
.ss-shell .ss-colcount{border-radius:20px;padding:3px 7px;font-size:10px;font-weight:800;background:#d3eaff;color:#1d5791}
.ss-shell .ss-c2 .ss-colcount{background:#ffe0e3;color:#9e2c3b}
.ss-shell .ss-c3 .ss-colcount{background:#e2d9ff;color:#57399f}
.ss-shell .ss-c4 .ss-colcount{background:#c9f3dc;color:#0d6040}
.ss-shell .ss-dealcard{background:#fff;border:1px solid #e2e9f3;border-radius:7px;padding:12px 10px;margin-bottom:9px;
  box-shadow:0 3px 7px rgba(31,49,73,.025)}
.ss-shell .ss-dealcard:hover{border-color:#8bb8ef}
.ss-shell .ss-dealcard > button{background:none;border:0;padding:0;text-align:left;width:100%;cursor:pointer;color:inherit}
.ss-shell .ss-dealcard strong{display:block;font-size:11px;line-height:1.4}
.ss-shell .ss-dealcard small{display:block;font-size:10px;color:#5f6e83;margin-top:4px;line-height:1.35}
.ss-shell .ss-dealcard .ss-stagechip{display:inline-block;margin-top:8px;font-size:10px;font-weight:700;color:#3d5167;background:#f2f6fa;border:1px solid #e0e8f0;border-radius:5px;padding:2px 7px}
.ss-shell .ss-dealcard select{margin-top:7px;width:100%;border:1px solid #dce4ee;border-radius:6px;padding:5px 6px;
  font-size:10px;background:#fff;color:#283e58}
.ss-shell .ss-boardempty{text-align:center;color:#5f6e83;font-size:12px;padding:36px 8px}
.ss-shell .ss-boardempty strong{display:block;color:#1d4a3a;font-size:13px;margin:6px 0 4px}
.ss-shell .ss-footnote{margin-top:20px;color:#5f6e83;font-size:11px}

/* ── Appointments ───────────────────────────────────────────────────── */
.ss-shell .ss-calendarbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;
  padding:15px;border-bottom:1px solid #edf0f5}
.ss-shell .ss-calcontrols{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ss-shell .ss-calcontrols strong{font-size:13px;margin:0 10px}
.ss-shell .ss-calcontrols .ss-btn[aria-pressed="true"]{background:#e7f2ff;border-color:#c8e2ff;color:#0569d9}
.ss-shell .ss-calgrid{display:grid;grid-template-columns:80px 1fr}
.ss-shell .ss-timecol{border-right:1px solid #e6eaf0}
.ss-shell .ss-timecell{height:56px;border-bottom:1px solid #e7edf4;font-size:11px;color:#5f6e83;text-align:center;padding-top:8px}
.ss-shell .ss-slotcol{position:relative}
.ss-shell .ss-slotcol .ss-timeslot{height:56px;border-bottom:1px solid #e7edf4}
.ss-shell .ss-event{border-left:3px solid #2285ec;border-radius:5px;background:#e5f1ff;color:#245d97;padding:8px 10px;
  position:absolute;left:9px;right:15%;font-size:11px;text-align:left;cursor:pointer;overflow:hidden;border-top:0;border-right:0;border-bottom:0}
.ss-shell .ss-event.ss-green{border-color:#0ca87b;background:#def9ee;color:#147354}
.ss-shell .ss-event.ss-cancelled{opacity:.55;text-decoration:line-through}
.ss-shell .ss-event strong{display:block}
.ss-shell .ss-event small{display:block;margin-top:3px;font-size:10px}
.ss-shell .ss-week{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
.ss-shell .ss-weekday{border-right:1px solid #edf0f5;min-height:260px;padding:8px}
.ss-shell .ss-weekday:last-child{border-right:0}
.ss-shell .ss-weekday h4{margin:0 0 8px;font-size:11px;color:#5f6e83;font-weight:700}
.ss-shell .ss-weekday h4.ss-today{color:#0569d9}
.ss-shell .ss-chipevent{display:block;width:100%;text-align:left;border:0;border-left:3px solid #2285ec;background:#e5f1ff;
  color:#245d97;border-radius:5px;padding:6px 7px;font-size:10px;margin-bottom:6px;cursor:pointer}
.ss-shell .ss-chipevent.ss-green{border-color:#0ca87b;background:#def9ee;color:#147354}
.ss-shell .ss-chipevent strong{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ss-shell .ss-month{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
.ss-shell .ss-monthhead{font-size:10px;color:#5f6e83;font-weight:700;padding:8px;border-bottom:1px solid #edf0f5;text-align:center}
.ss-shell .ss-monthcell{min-height:92px;border-right:1px solid #edf0f5;border-bottom:1px solid #edf0f5;padding:6px;
  text-align:left;background:#fff;cursor:pointer;font:inherit;color:inherit;border-top:0;border-left:0}
.ss-shell .ss-monthcell.ss-out{background:#fafcfe;color:#a5b2c1}
.ss-shell .ss-monthcell .ss-dnum{font-size:11px;font-weight:700}
.ss-shell .ss-monthcell.ss-today .ss-dnum{color:#0569d9}
.ss-shell .ss-monthcell .ss-dcount{display:block;margin-top:6px;font-size:10px;color:#245d97;background:#e5f1ff;border-radius:4px;padding:3px 5px}
.ss-shell .ss-upnext{padding:6px 20px 14px}
.ss-shell .ss-compact{border-bottom:1px solid #edf0f5;padding:10px 0;font-size:12px;color:#475d73;display:flex;gap:10px;
  align-items:center;flex-wrap:wrap}
.ss-shell .ss-compact:last-child{border-bottom:0}
.ss-shell .ss-compact strong{color:#142e47}
.ss-shell .ss-compact .ss-sp{flex:1}

/* ── modal + toast ──────────────────────────────────────────────────── */
.ss-modalback{position:fixed;inset:0;background:#0a1839a3;display:flex;align-items:center;justify-content:center;padding:16px;z-index:100}
.ss-modal{width:min(550px,100%);max-height:92vh;overflow-y:auto;background:#fff;border-radius:14px;box-shadow:0 25px 90px #0003}
.ss-modalhead{padding:22px 24px 15px;border-bottom:1px solid var(--ss-line);display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.ss-modalhead h2{font-size:20px;margin:0}
.ss-modalhead p{font-size:12px;color:#5f6e83;margin:5px 0 0}
.ss-modalbody{padding:20px 24px}
.ss-modalfoot{display:flex;justify-content:flex-end;gap:9px;border-top:1px solid var(--ss-line);padding:15px 24px;flex-wrap:wrap}
.ss-outcomes{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.ss-outcome{border:1px solid #dce6f0;background:#fff;border-radius:8px;padding:11px 8px;font-size:12px;font-weight:700;
  color:#2c4056;cursor:pointer;min-height:44px}
.ss-outcome:hover{border-color:#b9d8fb}
.ss-outcome[aria-pressed="true"]{background:#e7f2ff;border-color:#1479f4;color:#0569d9;box-shadow:inset 0 0 0 1px #1479f4}
.ss-toast{position:fixed;bottom:23px;right:24px;background:#0c3f36;color:#fff;padding:14px 19px;border-radius:8px;
  box-shadow:0 12px 35px #0003;z-index:200;max-width:min(390px,90vw);font-size:12px}

/* ── responsive (the prototype's breakpoints) ───────────────────────── */
@media(max-width:1020px){
  .ss-shell .sw-app{grid-template-columns:185px minmax(0,1fr)}
  .ss-shell .sw-topbar,.ss-shell .sw-body{padding-left:20px;padding-right:20px}
  .ss-shell .ss-columns{grid-template-columns:1fr}
  .ss-shell .ss-stats{grid-template-columns:repeat(2,1fr)}
  .ss-shell .ss-rowmeta{min-width:100px}
  .ss-shell .ss-boards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .ss-shell .ss-twoform{grid-template-columns:1fr}
  .ss-shell .ss-formside{min-height:0}
  .ss-shell .ss-teamstats{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media(max-width:820px){
  .ss-shell .sw-app{display:block}
  .ss-shell .sw-sidebar{position:sticky;top:0;z-index:20;width:100%;height:auto;padding:10px 12px;flex-direction:row;
    align-items:center;gap:12px;overflow-x:auto;overflow-y:hidden;scrollbar-width:none}
  .ss-shell .sw-sidebar::-webkit-scrollbar{display:none}
  .ss-shell .sw-brand{padding:0;flex:none}
  .ss-shell .sw-brand small,.ss-shell .sw-profile,.ss-shell .sw-mini,.ss-shell .sw-sidefill,
  .ss-shell .sw-navtitle,.ss-shell .ss-more{display:none}
  .ss-shell .sw-nav{flex-direction:row;gap:3px;flex:none}
  .ss-shell .sw-nav a{padding:8px 10px;font-size:11px;white-space:nowrap;width:auto}
  .ss-shell .sw-nav a svg{display:none}
  .ss-shell .sw-topbar{min-height:74px;padding:14px 17px;position:static}
  .ss-shell .sw-topbar h1{font-size:20px}
  .ss-shell .ss-hide-sm{display:none}
  .ss-shell .sw-body{padding:17px 14px 40px}
  .ss-shell .ss-hero{padding:20px 16px}
  .ss-shell .ss-hero h2{font-size:19px}
  .ss-shell .ss-stats{gap:8px}
  .ss-shell .ss-stat{padding:15px}
  .ss-shell .ss-quick{grid-template-columns:1fr;gap:9px}
  .ss-shell .ss-quick button{min-height:62px}
  .ss-shell .ss-rowmeta{display:none}
  .ss-shell .ss-listrow{gap:8px;padding:11px 8px}
  .ss-shell .ss-avatar{height:32px;width:32px;font-size:10px}
  .ss-shell .ss-rowactions{gap:4px}
  .ss-shell .ss-rowactions .ss-square{width:31px;height:31px}
  .ss-shell .ss-rowactions .ss-btn{min-height:30px;padding:6px 8px}
  .ss-shell .ss-detailbar{flex-wrap:wrap}
  .ss-shell .ss-detail-actions{width:100%;margin-left:0}
  .ss-shell .ss-detail-actions .ss-btn{flex:1 1 0}
  .ss-shell .ss-twobox,.ss-shell .ss-twoform,.ss-shell .ss-boards{grid-template-columns:1fr}
  .ss-shell .ss-boardcol{min-height:0}
  .ss-shell .ss-calendarbar{padding:9px}
  .ss-shell .ss-calgrid{grid-template-columns:58px 1fr}
  .ss-shell .ss-timecell{font-size:10px}
  .ss-shell .ss-week{grid-template-columns:1fr}
  .ss-shell .ss-weekday{border-right:0;border-bottom:1px solid #edf0f5;min-height:0}
  .ss-shell .ss-search{min-width:0;max-width:none;flex:1 1 100%}
  .ss-outcomes{grid-template-columns:repeat(2,minmax(0,1fr))}
  .ss-shell .ss-teamstats{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(prefers-reduced-motion:reduce){ .ss-shell .ss-skel{animation:none} }
`

let injected = false

export default function SimpleStyles() {
  useEffect(() => {
    if (injected || document.getElementById('ss-styles')) return
    const el = document.createElement('style')
    el.id = 'ss-styles'
    el.textContent = CSS
    document.head.appendChild(el)
    injected = true
  }, [])
  return null
}
