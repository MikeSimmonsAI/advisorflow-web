# Design system — premium light theme

Scoped under `.bk` in `frontend/src/pages/restlandBookit/bookit.css`; nothing leaks into EvoSys.

Tokens (directive s06): canvas `#F5F7F5`, secondary `#EFF4F0`, card `#FFFFFF`, forest `#174735`,
emerald `#176B4A` / `#23885E`, soft emerald `#E8F5EE`, mint `#D6F2E2`, champagne `#C5A66A` /
`#FBF5E8`, ink `#172A22` / `#60736A` / `#85958D`, borders `#E1E9E3` / `#CCDAD0`, success
`#22865A`, warning `#B7791F`, error `#B94B4B`, info `#3B7898`.

* Hero gradients: `#E8F5EE → #FDFEFC`; money hero `#DDF2E6 → #F2FAF5`.
* Radii 10–18px, thin borders, soft shadows, tabular numerals, line icons (inline SVG).
* Desktop: collapsible icon rail (remembers preference, tooltips when collapsed), sticky header
  with search, notifications and profile. Detail work happens in slide-over panels.
* Mobile (< 760px): bottom navigation (My Day, Appointments, Follow-Ups, Evo 6 Block, More),
  panels become bottom sheets, large touch targets.
* Evo 6 Block network: radial SVG, status by color **and** text label; a full list view is the
  accessible alternative. Reduced motion is respected.
* Charts draw only real data; with too little data they say so instead of drawing a line.
