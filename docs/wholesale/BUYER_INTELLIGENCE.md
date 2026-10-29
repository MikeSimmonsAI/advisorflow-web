# Buyer Intelligence Engine (#413)

One buyer network for every acquisition category. It is built on the existing `wholesale_buyers` / `wholesale_buy_boxes`. There is no second buyer table.

## Pieces

| Piece | Where |
|---|---|
| Evidence tables (purchases, legal entities, qualification conversations) | `app/models/wholesale_buyer_engine_models.py` |
| Segments, verification dimensions, readiness, enrichment meter, list, summary | `app/services/wholesale_buyer_engine.py` |
| County deed evidence, exact-name dedupe, related-LLC review (never auto-merged) | `app/services/wholesale_buyer_evidence.py`, `wholesale_buyer_finder.py` |
| Match score v2 (versioned; unknown = missing, not a miss) | `app/services/wholesale_matching.py` |
| Universal property -> buyer match, prepare for disposition, demand summary | `app/services/wholesale_buyer_match.py` |
| AI buyer qualifier (practice / recorded; sends nothing) | `app/services/wholesale_buyer_qualify.py` |
| API | `/wholesale/buyer-intel/*` (`app/routers/wholesale_buyer_engine_router.py`) |
| Screen | `/wholesale/buyers`: `WholesaleBuyers.jsx` + `wsBuyerIntelligence.jsx` |
| Tests | `tests/test_wholesale_buyer_engine.py`, `tests/test_wholesale_cross_tenant.py` |

## Rules

- **Three separate measurements:**
  - Match score: one property against a buy box.
  - Verification: identity, purchases, contact and buy box, each with its own date.
  - Readiness: buying now, documented funding, closing.

  They are never blended.
- **Purchase evidence classes:** RECORDED TITLE TRANSFER (deed; Texas states no price), VERIFIED PURCHASE, VERIFIED CASH PURCHASE (needs a document reference), REPORTED INVESTOR PURCHASE, CLAIMED PURCHASE. A deed is never treated as cash, and a claim never carries a price.
- **Expiry:**
  - A contact verification goes stale after 180 days.
  - A confirmed buy box goes stale after 365 days.
  - "Buying now" goes stale after 90 days.
  - Expired proof of funds is not documented funding.
- **Enrichment** is five stages x 20% of research completeness. A paid lookup is never one click.
- **AI:** through `ai_gateway` (`wholesale_buyer_qualify`) with a rules fallback.
  - Opt-out wins.
  - Hand-offs and follow-ups go to the work queue.
  - Walkthroughs go to Appointments.
  - Live investor messaging is not switched on.
- **Demand summary:** read-only. It never changes a hunt or buys data.
