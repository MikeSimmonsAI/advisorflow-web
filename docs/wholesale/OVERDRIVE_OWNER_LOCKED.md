# PROJECT OVERDRIVE — OWNER LOCKED DESIGN + IMPLEMENTATION CHECKLIST

Tracking: [Issue #439](https://github.com/MikeSimmonsAI/advisorflow-web/issues/439) | Design approval: 2026-10-29
Branch: `feature/overdrive-owner-approved-ui`

## Locked visual targets
- **Property-detail screen** approved in conversation: navy blue EvoSys Pro Wholesale sidebar; white content; global property/owner/buyer search; DFW market switch; property address, distress score; property image, map, owner detail; six short metric cards (distress score, evidence-backed motivation signals, equity, ARV, repair estimate, maximum wholesale offer); tabs Overview, Ownership, Distress Signals, Comps & Value, Underwriting, Buyers, Contacts, AI Assistant, Activity. Three sections above fold: source-backed distress signals, selectable Wholesale/Investment/Both financial scenarios, comparable sales. Lower fold: buyer matches table, contact enrichment, next actions. Top-right Launch Deal. Real record values ONLY; *never* use the illustrative property owner, contacts, dollar numbers, images, sale records, or buyer statistics shown in the mockup.
- **Wholesale navigation** approved: Acquisition Command top pinned; expandable Acquisition, Buyers & Deals, Operations, Team & Field, Tools & Settings; collapsed desktop rail and touch-friendly mobile drawer. Preserve every old destination, route, deep link and authorization. Nest Buy Boxes and Dispositions with Buyers & Deals. Dedicated Multifamily when real functional route exists—no dead links.
- **Design principle:** reuse existing signed-in UI components/typography/colors; no change to approved owner/investor Deal Room design. Show truthful data states (live / sandbox / stale / unverified / conflicting / unknown), not mock KPIs.

## First engineering pass — required order
1. Audit current main and source for actual Wholesale routes, sidebar route config, property detail and underlying API. Compare against approved targets and prepare precise route preservation matrix.
2. Implement sidebar groups, persisted preference only if existing mechanism permits it, keyboard-accessible toggles, collapsed rail, mobile drawer, active route highlighting, overflow support. Keep existing permission filtering, workspace isolation, branding.
3. Implement real one-property overview/detail by composing existing `frontend/src/pages/wholesale/*` and `evosense/*` components, APIs and shared services. Do not create parallel property, buyer, matching, offer or CRM models. Where a feature is not configured, render an honest missing/provider-not-connected explanation with useful next steps; don't create pretend buttons.
4. Connect the property-specific Buyers action to `wholesale_buyer_match.py`, preserve separate match, verification and readiness. Evidence pane uses `wholesale_intel.py`; underwriting reuses `wholesale_underwriting.py`; outreach/Launch Deal reuses existing publication/Deal Room/disposition checks and must not bypass approvals.
5. Run a complete route/role matrix test (owner, wholesale staff, investor, other tenant), visual snapshots 1440/1024/mobile, zero synthetic or personal data leaked, loading/error/empty states and current regression tests.
6. Open scoped PR referencing #439; screenshots and honest test output; no auto-merge/deploy. Do not change unrelated tenant UI.

## Parallel requirements and dependencies
- **Blocker:** open PR #437 backend OOM; do not amplify background jobs before stability gate passes; separate owner approval needed for merge.
- **Upcoming sources:** ATTOM, PropStream, Tracerfy planned for 2026-10-30; do not claim connected until licensed live checks pass. Reuse source-neutral adapters, `evosense.propstream_csv`, `wholesale_data_layer`, source precedence, cost ledger, and skip-tracing queue. Rate limits, idempotency, provider failure, budget ceilings, caching, independent-record provenance.
- **Wholster-inspired functionality:** real recent-buyer evidence search and one guided Launch Deal to approved private portal and allowed external channels; report views/inquiries/offers. Wholster integration only if terms/API permit.
- **Tranchi-inspired functionality:** existing AI Workforce coordinates bounded research, underwriting and follow-ups; clear start/stop/practice, audit trail and authentic AI status. No phantom agents.

## Acceptance tests for release
- All old routes preserved and discoverable; no workspace permission widening.
- Property overview derives from a real saved record; 404 and missing-data truthful.
- Each key value displays source, as-of and confidence; conflicting sources are not silently overwritten.
- Three underwriting modes operate using shared math, not duplicated formulas.
- Buyers/Launch Deal use existing buyer/Deal Room IDs and approval workflow.
- ATTOM, PropStream and Tracerfy display `not connected` until genuinely configured.
- UI remains usable on phone/tablet/desktop.
- Signed-in production journey only after approved deploy, not substituted by synthetic tests.

**Status:** plan/spec established on branch; implementation and acceptance tests have not yet been performed.
