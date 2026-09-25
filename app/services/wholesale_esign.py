"""Document lifecycle and electronic signature — the honest version.

Two things live here, and the reason they live together is that the second is
mostly a way of moving the first.

──────────────────────────────────────────────────────────────────────────────
THE LIFECYCLE (A7)

A document on a wholesale deal is in exactly one state, and every state is a
FACT somebody or something put there:

    draft             a slot exists; no file is held
    ready_for_review  a person says the draft is ready to be looked at
    approved          a person approved it internally
    sent              it left here, to a named party, at a recorded time
    viewed            an external party actually opened it through a share link
    signed            every party signed — recorded by a provider callback, or
                      by a person uploading the signed copy
    declined          a party refused
    voided            withdrawn; it no longer counts
    superseded        replaced by a later document, which is named

NOTHING IN THIS MODULE ADVANCES A STATUS ON ITS OWN. There is no scheduler, no
inference from a date, and no "it's probably signed by now". `viewed` is set
only from a real fetch through a share link; `sent` only when a person records
the send; `signed` only from a provider callback or a person's upload. A status
nobody caused is a lie about a legal document, and this module refuses to tell
one.

The legal transitions are declared in TRANSITIONS and enforced in `transition`.
An illegal move raises, so a caller cannot quietly skip approval.

──────────────────────────────────────────────────────────────────────────────
THE SIGNATURE PROVIDER (A8)

Same registry shape as `wholesale_enrichment`: a base class, a manual provider
that is always available and honest about doing nothing automatic, and a
commented worked example showing exactly where a real vendor is wired in.

The manual provider does NOT sign anything. It records that a document was sent
for signature outside this system and that a person later uploaded the executed
copy. That is a true record of an offline process, not a simulated e-signature:
no envelope id is minted, no certificate is produced, no signer identity is
asserted, and `signed` is reached only because a person said "here is the signed
file".

If a request is made that needs a provider and none is connected, the answer is
an explicit refusal naming the environment variable that is missing. It is never
a success.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ── Document status ─────────────────────────────────────────────────────────

STATUS_DRAFT = "draft"
STATUS_READY = "ready_for_review"
STATUS_APPROVED = "approved"
STATUS_SENT = "sent"
STATUS_VIEWED = "viewed"
STATUS_SIGNED = "signed"
STATUS_DECLINED = "declined"
STATUS_VOIDED = "voided"
STATUS_SUPERSEDED = "superseded"

# The legacy vocabulary this module shipped with. Rows written before the
# lifecycle existed carry these, and they are mapped rather than migrated in
# place: a document row is evidence about a legal document, and rewriting its
# recorded state to fit a newer vocabulary is exactly the kind of quiet edit
# this module must not make. `normalise` reads them; nothing writes them.
LEGACY_STATUS = {
    "needed": STATUS_DRAFT,
    "uploaded": STATUS_READY,
    "sent": STATUS_SENT,
    "executed": STATUS_SIGNED,
    "void": STATUS_VOIDED,
}

STATUSES = (STATUS_DRAFT, STATUS_READY, STATUS_APPROVED, STATUS_SENT,
            STATUS_VIEWED, STATUS_SIGNED, STATUS_DECLINED, STATUS_VOIDED,
            STATUS_SUPERSEDED)

# Terminal states. A document here is finished with, one way or another.
TERMINAL = (STATUS_SIGNED, STATUS_DECLINED, STATUS_VOIDED, STATUS_SUPERSEDED)

STATUS_LABEL = {
    STATUS_DRAFT: "Draft",
    STATUS_READY: "Ready for review",
    STATUS_APPROVED: "Approved",
    STATUS_SENT: "Sent",
    STATUS_VIEWED: "Opened by the other party",
    STATUS_SIGNED: "Signed",
    STATUS_DECLINED: "Declined",
    STATUS_VOIDED: "Voided",
    STATUS_SUPERSEDED: "Superseded",
}

# What each state means in a sentence a person can act on. Shown on the row.
STATUS_MEANING = {
    STATUS_DRAFT: "A slot with no file behind it yet.",
    STATUS_READY: "A file is held and somebody should look at it.",
    STATUS_APPROVED: "Approved internally. Nothing has left here.",
    STATUS_SENT: "Recorded as sent. This module does not send it for you.",
    STATUS_VIEWED: "Opened through a share link. Recorded from the fetch.",
    STATUS_SIGNED: "Every party signed. Recorded from a signed copy or a provider.",
    STATUS_DECLINED: "A party refused to sign.",
    STATUS_VOIDED: "Withdrawn. It no longer counts for anything.",
    STATUS_SUPERSEDED: "Replaced by a later document.",
}

# Who or what is allowed to cause each state. Printed, not decorative: it is
# the difference between a record and a guess.
STATUS_SOURCE = {
    STATUS_DRAFT: "person",
    STATUS_READY: "person",
    STATUS_APPROVED: "person",
    STATUS_SENT: "person",
    STATUS_VIEWED: "recipient",
    STATUS_SIGNED: "person or signature provider",
    STATUS_DECLINED: "person or signature provider",
    STATUS_VOIDED: "person",
    STATUS_SUPERSEDED: "person",
}

# The only moves allowed. Anything absent from this map is refused.
#
# Two deliberate shapes:
#   - `viewed` goes back to `sent`'s successors, not backwards. A second view
#     does not un-send anything.
#   - every non-terminal state may be voided or superseded, because a real
#     document can be withdrawn at any point, and a workflow that cannot
#     withdraw one is a workflow people work around.
TRANSITIONS: Dict[str, tuple] = {
    STATUS_DRAFT: (STATUS_READY, STATUS_APPROVED, STATUS_VOIDED, STATUS_SUPERSEDED),
    STATUS_READY: (STATUS_DRAFT, STATUS_APPROVED, STATUS_VOIDED, STATUS_SUPERSEDED),
    STATUS_APPROVED: (STATUS_READY, STATUS_SENT, STATUS_SIGNED, STATUS_VOIDED,
                      STATUS_SUPERSEDED),
    STATUS_SENT: (STATUS_VIEWED, STATUS_SIGNED, STATUS_DECLINED, STATUS_VOIDED,
                  STATUS_SUPERSEDED),
    STATUS_VIEWED: (STATUS_SIGNED, STATUS_DECLINED, STATUS_VOIDED, STATUS_SUPERSEDED),
    STATUS_SIGNED: (STATUS_SUPERSEDED, STATUS_VOIDED),
    STATUS_DECLINED: (STATUS_DRAFT, STATUS_READY, STATUS_SUPERSEDED, STATUS_VOIDED),
    STATUS_VOIDED: (),
    STATUS_SUPERSEDED: (),
}


def normalise(status: Optional[str]) -> str:
    """The lifecycle state of a stored value, old vocabulary or new."""
    if not status:
        return STATUS_DRAFT
    if status in STATUSES:
        return status
    return LEGACY_STATUS.get(status, STATUS_DRAFT)


def allowed_next(status: Optional[str]) -> List[str]:
    return list(TRANSITIONS.get(normalise(status), ()))


def can_transition(current: Optional[str], target: str) -> bool:
    return target in TRANSITIONS.get(normalise(current), ())


class TransitionRefused(ValueError):
    """An illegal lifecycle move. Carries a sentence, not a code."""


def transition(current: Optional[str], target: str) -> str:
    """Validate a lifecycle move and return the new status.

    Raises `TransitionRefused` with a sentence naming what is actually possible
    from here, because "invalid transition" tells the person nothing.
    """
    now = normalise(current)
    if target not in STATUSES:
        raise TransitionRefused(
            "%r is not a document status. The statuses are: %s."
            % (target, ", ".join(STATUSES)))
    if target == now:
        return now
    if not can_transition(now, target):
        options = allowed_next(now)
        raise TransitionRefused(
            "A document that is %s cannot become %s. From here it can only "
            "become: %s." % (STATUS_LABEL[now].lower(), STATUS_LABEL[target].lower(),
                             ", ".join(STATUS_LABEL[s].lower() for s in options)
                             or "nothing — it is finished"))
    return target


def lifecycle_report() -> List[Dict[str, Any]]:
    """The whole lifecycle, for a screen that wants to explain itself."""
    return [{
        "key": s,
        "label": STATUS_LABEL[s],
        "meaning": STATUS_MEANING[s],
        "set_by": STATUS_SOURCE[s],
        "terminal": s in TERMINAL,
        "next": allowed_next(s),
    } for s in STATUSES]


# ── Signature providers ─────────────────────────────────────────────────────

SEND_SENT = "sent"
SEND_MANUAL = "manual"
SEND_NOT_CONFIGURED = "not_configured"
SEND_FAILED = "failed"


@dataclass
class SignatureRequest:
    """Everything a provider needs, and nothing about how it is drafted."""

    document_id: str
    title: str
    # [{"name": ..., "email": ..., "role": ...}]
    parties: List[Dict[str, Any]] = field(default_factory=list)
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    message: Optional[str] = None


@dataclass
class SignatureResult:
    status: str
    provider: str
    message: str
    external_ref: Optional[str] = None
    sent_at: Optional[datetime] = None

    @property
    def left_the_building(self) -> bool:
        """Did a request actually go to a signer?

        The manual provider never returns True. Callers use this to decide
        whether `sent` is a truthful status, so it must not be generous.
        """
        return self.status == SEND_SENT


class SignatureProvider:
    """The interface. Two questions and one verb, same as enrichment.

    `send(...)` must never raise for a vendor-side failure and must never
    return SEND_SENT unless a request genuinely reached a signer.
    """

    key = "base"
    label = "Base"
    # Can this provider actually carry a signature, or does it only record one
    # that happened elsewhere? The UI asks this before offering a button.
    electronic = False
    required_env: tuple = ()

    def is_configured(self) -> bool:
        return all(os.environ.get(name) for name in self.required_env)

    def missing_config(self) -> List[str]:
        return [n for n in self.required_env if not os.environ.get(n)]

    def send(self, req: SignatureRequest) -> SignatureResult:  # pragma: no cover
        raise NotImplementedError


class ManualSignatureProvider(SignatureProvider):
    """Always available. Signs nothing, and says so.

    This is the honest answer to "get this signed" when no e-signature vendor is
    connected: the parties sign it somewhere else — in person, by hand, through
    the customer's own account with some provider — and a person uploads the
    executed copy here. That upload is what moves the document to `signed`.

    It returns SEND_MANUAL rather than SEND_SENT because nothing was sent, and
    it mints no envelope id, because a fabricated reference is indistinguishable
    from a real one the moment it is stored.
    """

    key = "manual"
    label = "Signed outside this system"
    electronic = False

    def is_configured(self) -> bool:
        return True

    def send(self, req: SignatureRequest) -> SignatureResult:
        return SignatureResult(
            status=SEND_MANUAL,
            provider=self.key,
            message=("No e-signature provider is connected, so nothing was sent. "
                     "Send the document however you normally do, then upload the "
                     "signed copy here — that upload is what records it as "
                     "signed, and it is the same record a provider callback "
                     "would write."),
        )


# The registry. A real vendor is added here and nowhere else.
#
# The worked example is a comment rather than dead code, so nothing half-wired
# can be selected in settings:
#
#     class AcmeSignProvider(SignatureProvider):
#         key = "acme"
#         label = "AcmeSign"
#         electronic = True
#         required_env = ("WHOLESALE_ACMESIGN_API_KEY",)
#
#         def send(self, req):
#             if not self.is_configured():
#                 return SignatureResult(status=SEND_NOT_CONFIGURED, provider=self.key,
#                                        message="WHOLESALE_ACMESIGN_API_KEY is not set.")
#             try:
#                 ... one HTTP call, short timeout ...
#             except Exception as exc:
#                 return SignatureResult(status=SEND_FAILED, provider=self.key,
#                                        message=str(exc)[:200])
#             return SignatureResult(status=SEND_SENT, provider=self.key,
#                                    external_ref=envelope_id,
#                                    sent_at=datetime.utcnow(),
#                                    message="Sent to 2 signers.")
#
#     A vendor added here also needs a webhook that moves the document to
#     `signed` or `declined`. Until that webhook exists, the document would sit
#     at `sent` forever, which is a worse lie than not offering the button.
PROVIDERS: Dict[str, SignatureProvider] = {
    ManualSignatureProvider.key: ManualSignatureProvider(),
}


def get_provider(key: Optional[str]) -> SignatureProvider:
    """The provider for a settings key, falling back to manual.

    An unknown key falls back rather than raising, for the same reason
    enrichment does: a provider removed in a future deploy must not make every
    existing customer's documents screen throw. The fallback is logged.
    """
    if key and key in PROVIDERS:
        return PROVIDERS[key]
    if key and key != ManualSignatureProvider.key:
        log.warning("wholesale esign: unknown provider %r, using manual", key)
    return PROVIDERS[ManualSignatureProvider.key]


def provider_report() -> List[Dict[str, Any]]:
    """What the settings screen renders: every provider and whether it can run."""
    out = []
    for key, p in sorted(PROVIDERS.items()):
        out.append({
            "key": key,
            "label": p.label,
            "electronic": p.electronic,
            "billable": False,
            "configured": p.is_configured(),
            "missing_env": p.missing_config(),
            "status": "ready" if p.is_configured() else "not_connected",
        })
    return out


def capability() -> Dict[str, Any]:
    """Can anything here actually carry a signature right now?

    The documents screen asks this before it decides whether to offer a "send
    for signature" button at all. A button that opens nothing is a promise the
    product cannot keep, so when this says False the screen says why instead.
    """
    electronic = [p for p in PROVIDERS.values() if p.electronic and p.is_configured()]
    return {
        "electronic_signature": bool(electronic),
        "providers": provider_report(),
        "reason": None if electronic else (
            "No e-signature provider is connected to this deployment. Documents "
            "are signed outside this system and the signed copy is uploaded "
            "here."),
    }
