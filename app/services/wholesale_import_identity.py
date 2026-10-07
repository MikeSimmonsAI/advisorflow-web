"""Buyer-import identity rules. Pure functions, no database, no framework.

A retried CSV import must not create a second copy of every buyer. The strongest
stable identity a buyer row carries is a contact point: a normalised email, else
a normalised phone. Names are NOT an identity — two "Smith Holdings" rows can be
two different companies — so a name alone never dedupes.

Fail closed: when a row's email and phone point at two different existing
buyers, or one contact point points at several, the row is REJECTED rather than
guessed at, and the reason says why. Everything is scoped by the caller to one
organization; this module only ever sees the index it is handed.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

CREATE = "create"
SKIP = "skip"
REJECT = "reject"


def norm_email(value: Any) -> Optional[str]:
    v = str(value or "").strip().lower()
    return v if v and "@" in v else None


def norm_phone(value: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 10 else None


def identity_keys(email: Any, phone: Any) -> List[Tuple[str, str]]:
    keys = []
    e, p = norm_email(email), norm_phone(phone)
    if e:
        keys.append(("email", e))
    if p:
        keys.append(("phone", p))
    return keys


def build_index(buyers: Iterable[Any]) -> Dict[Tuple[str, str], List[str]]:
    """key -> ids of the organization's existing buyers carrying it."""
    index: Dict[Tuple[str, str], List[str]] = {}
    for b in buyers:
        for key in identity_keys(getattr(b, "email", None), getattr(b, "phone", None)):
            ids = index.setdefault(key, [])
            if b.id not in ids:
                ids.append(b.id)
    return index


def decide(email: Any, phone: Any, index: Dict[Tuple[str, str], List[str]],
           seen_in_file: Dict[Tuple[str, str], int], row: int) -> Dict[str, Any]:
    """CREATE, SKIP (already exists) or REJECT (ambiguous) for one row.

    `seen_in_file` maps a key to the row that first claimed it; the caller
    records a row's keys with `claim` only after a CREATE.
    """
    keys = identity_keys(email, phone)
    if not keys:
        # An un-normalisable contact point (e.g. a 7-digit phone) is still a
        # contact point the importer accepted before; it cannot be matched, so
        # it creates. The caller's own "no email and no phone" rule runs first.
        return {"action": CREATE}
    existing = set()
    for key in keys:
        existing.update(index.get(key, []))
    if len(existing) > 1:
        return {"action": REJECT,
                "reason": "ambiguous — this row's email/phone match %d different "
                          "existing buyers; resolve the duplicates before "
                          "importing it" % len(existing)}
    if len(existing) == 1:
        return {"action": SKIP, "buyer_id": next(iter(existing)),
                "reason": "already exists — a buyer in this organization has the "
                          "same %s" % "/".join(k for k, _ in keys
                                               if index.get((k, dict(keys)[k])))}
    prior = {seen_in_file[k] for k in keys if k in seen_in_file}
    if len(prior) > 1:
        return {"action": REJECT,
                "reason": "ambiguous — email and phone match different earlier "
                          "rows (%s) in this file" % ", ".join(
                              str(r) for r in sorted(prior))}
    if prior:
        return {"action": SKIP,
                "reason": "duplicate of row %d in this file" % next(iter(prior))}
    return {"action": CREATE}


def claim(email: Any, phone: Any, seen_in_file: Dict[Tuple[str, str], int],
          row: int) -> None:
    for key in identity_keys(email, phone):
        seen_in_file.setdefault(key, row)
