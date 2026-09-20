# Workspace view configurations

A file here is the value of one organization's `workspace_views` column: the
workflow screens that customer's own people recognise, expressed as questions
over records the platform already holds. See
`app/services/workspace_views.py` for the shape and
`scripts/configure_customer_workspace.py` for how to apply one.

## The layer rule

The column **replaces** the industry default; it does not merge with it. So a
file here lists every screen that customer should see, including the ones its
industry would have supplied anyway.

That is deliberate. A merge would mean nobody can read a customer's
configuration and know what they will see, which is the property that matters
when a screen is wrong at eight in the morning.

**Only write a file here for a screen the industry template cannot supply.**
Anything the next customer in the same vertical will also want belongs in
`industry_templates.TEMPLATES[...]["workspace_views"]` instead. Configuration
that only one customer can use is the thing this whole design exists to avoid.

## What is here

* `energy-retail-with-move-concierge.json` — the `energy` template's three
  screens plus a move-concierge queue, for an energy retailer that also runs
  move services. The concierge screen selects on `source_detail`, so it shows
  the requests that arrived through the concierge path and nothing else.

A commercial cleaning customer needs **no file here**: `cleaning` is its own
template, and it already supplies Prospects, Follow-Up and Walkthroughs in
that trade's own vocabulary. Leave `workspace_views` NULL and the customer
inherits them — which is also what keeps every cleaning company on one
configuration instead of a file each.

(`cleaning` used to be an alias of `home_services`. It was promoted to a
template of its own so the vertical could carry its own appointment types,
board stages and tier labels; the screens it supplies are the same three.)
