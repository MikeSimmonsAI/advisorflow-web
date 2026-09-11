"""Custom commercial agreements.

The arrangement layer that sits BESIDE T2, not over it. T2 stays the authority
on catalogue items, Stripe customers, subscriptions, payments and entitlements;
this package models arrangements a catalogue cannot express — a revenue share,
a hybrid, a quoted deal — and the onboarding behaviour that has to keep working
while their terms are still being negotiated.

Module map:

  questions.py      the configurable question engine: what a structure needs to
                    be asked, per brand, per agreement type.
  agreements.py     create, read, amend, approve, activate, suspend, end — with
                    authority and audit on every material change.
  revenue_share.py  allocation validation and the arithmetic of a split.
  terms.py          term state, what is missing, and WHICH ACTIONS that blocks —
                    never a single global "blocked" flag.
  settlement.py     collections sources, previews, and the refusals that keep an
                    unknown from becoming a zero.
  overrides.py      onboarding milestones satisfied previously, waived, or not
                    applicable — with a named decider and a reason.
  onboarding.py     the guided flow: fifteen independent steps over the launch
                    engine's existing rows.
  authority.py      who may do what. Reuses the platform's existing roles.
  t2_link.py        read-only view of what T2 already holds for this customer.
"""
