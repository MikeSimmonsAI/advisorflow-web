"""Concurrent identical GETs are coalesced - and that must never become a cache.

THE EVIDENCE. Production backend logs, a single client IP, one four-second
window during a dashboard load: 22 GETs, each preceded by its own CORS
preflight, so 44 round trips to render one screen. Two of the GETs were
`/settings/profile` and two were `/settings/my-capabilities` - the same
question asked twice in the same instant, because `useWorkspaceAuthority()` and
`Layout.jsx` each fetch it on mount and neither knows the other exists.

THE FIX AND ITS ONE RULE. `api.get` joins an identical request that is already
in flight. It does NOT remember the answer. The entry is dropped the moment the
promise settles - in a `finally`, so a rejection is dropped too.

WHY THAT LINE MATTERS ENOUGH TO GATE. The tempting "improvement" is to keep the
response for a few seconds. That would turn this into a capability cache, and
`workspaceAuthority.js` and `Layout.jsx` both carry comments explaining that a
capability list rendered from a previous moment shows an org admin a door the
server will refuse. Coalescing cannot do that - every joined caller receives the
answer to a request that was in flight when they asked, which is exactly as
fresh as their own request issued in the same millisecond would have been.
A TTL can, and the difference is one line in a `finally`.

These are source assertions rather than behavioural ones because `client.js`
reads `import.meta.env` and `localStorage` at module scope and so cannot be
imported under plain node, which is how this repo's other frontend tests run
(see tests/frontend/*.test.mjs). The properties asserted are the ones that
would actually regress.
"""

import os
import re

import pytest

FE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "frontend", "src")
CLIENT = os.path.join(FE, "api", "client.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def client_js():
    return _read(CLIENT)


def test_api_get_routes_through_the_dedupe(client_js):
    assert re.search(r"get:\s*\(path,\s*opts\s*=\s*\{\}\)\s*=>\s*dedupedGet\(", client_js), (
        "api.get no longer goes through dedupedGet - the dashboard is back to "
        "firing duplicate concurrent requests")


def test_the_entry_is_dropped_in_a_finally_not_only_on_success(client_js):
    """The single line that keeps this from being a cache."""
    block = client_js[client_js.index("function dedupedGet"):]
    block = block[:block.index("export const api")]
    assert ".finally(" in block, (
        "dedupedGet no longer drops its entry in a finally. If it only drops on "
        "success, a failed request is held and re-handed to every later caller; "
        "if it does not drop at all, this is a cache with no expiry.")
    assert "_inFlightGets.delete(key)" in block


def test_there_is_no_ttl_anywhere_in_the_dedupe(client_js):
    """A timer here is the regression this file exists to catch."""
    block = client_js[client_js.index("const _inFlightGets"):]
    block = block[:block.index("export const api")]
    for forbidden in ("setTimeout", "Date.now", "TTL", "ttl", "expires", "maxAge"):
        assert forbidden not in block, (
            "`%s` appeared in the GET dedupe. Coalescing is safe precisely "
            "because it has no notion of time: a joined caller gets an answer "
            "that was in flight when they asked. Anything that keeps a response "
            "for a duration can serve a capability list from before it changed, "
            "which is the failure workspaceAuthority.js is written to prevent."
            % forbidden)


def test_the_key_carries_every_scoping_header(client_js):
    """Two contexts asking the same path are two different questions.

    If the key ignored the org override, a god_admin who entered a customer
    would join a request issued from platform scope and read the wrong
    customer's data. The key must name everything `request()` puts in a header.
    """
    block = client_js[client_js.index("function _getDedupeKey"):]
    block = block[:block.index("export function resetInFlightGets")]
    for scoping in ("getOrgContext", "getBrandContext", "getWorkspaceContext",
                    "_observationOrgId", "noOrgContext", "skipRedirect"):
        assert scoping in block, (
            "the dedupe key does not include `%s`, which narrows the request. "
            "Two differently-scoped reads of one path would be merged." % scoping)


def test_every_header_request_sends_appears_in_the_key():
    """Asserted against `request()` itself, so the two cannot drift apart.

    A header added to `request()` without being added to the key is invisible
    until somebody reads another customer's dashboard.
    """
    src = _read(CLIENT)
    req = src[src.index("async function request("):src.index("const _inFlightGets")]
    sent = set(re.findall(r"headers\['(X-[A-Za-z-]+)'\]\s*=", req))
    key = src[src.index("function _getDedupeKey"):src.index("export function resetInFlightGets")]
    accessor_for = {
        "X-Org-Override": "getOrgContext",
        "X-Brand-Override": "getBrandContext",
        "X-Workspace-Id": "getWorkspaceContext",
        "X-Executive-Observe": "_observationOrgId",
    }
    assert sent, "no scoping headers found in request() - did it get rewritten?"
    for header in sorted(sent):
        accessor = accessor_for.get(header)
        assert accessor is not None, (
            "request() now sends %s, which the dedupe key knows nothing about. "
            "Add it to _getDedupeKey and to this mapping." % header)
        assert accessor in key, (
            "%s is sent by request() but %s is missing from the dedupe key"
            % (header, accessor))


def test_the_map_is_cleared_on_login_and_on_logout(client_js):
    """A GET in flight was asked as the previous token holder."""
    assert client_js.count("resetInFlightGets()") >= 2
    login = client_js[client_js.index("export async function login("):]
    login = login[:login.index("export function setMustChangePassword")]
    assert "resetInFlightGets()" in login, (
        "a request in flight across a sign-in would hand the new user the "
        "previous user's answer")
    logout = client_js[client_js.index("export async function logout("):]
    logout = logout[:logout.index("export function startKeepAlive")]
    assert "resetInFlightGets()" in logout


def test_only_gets_are_coalesced(client_js):
    """Replaying or merging a write is a different and much worse bug.

    client.js already carries a long comment about why a FormData upload must
    not be retried - replaying `POST /leads/upload/confirm` imports the batch
    twice. Merging two POSTs would be that failure in a new place.
    """
    block = client_js[client_js.index("export const api = {"):]
    block = block[:block.index("}", block.index("upload:"))]
    for verb in ("post:", "put:", "patch:", "delete:", "upload:"):
        line = [l for l in block.splitlines() if l.strip().startswith(verb)]
        assert line, verb
        assert "dedupedGet" not in line[0], (
            "`%s` now goes through the GET dedupe. Coalescing writes merges two "
            "distinct intents into one." % verb)


def test_the_two_known_duplicate_callers_are_still_the_reason_this_exists(client_js):
    """Documents the evidence in an executable form.

    If both callers ever collapse into one, this gate is not wrong - but the
    person who did it should see this test and decide deliberately whether the
    dedupe is still earning its place.
    """
    layout = _read(os.path.join(FE, "components", "Layout.jsx"))
    authority = _read(os.path.join(FE, "auth", "workspaceAuthority.js"))
    assert "/settings/my-capabilities" in layout
    assert "/settings/my-capabilities" in authority, (
        "only one component fetches /settings/my-capabilities now - the "
        "duplicate this dedupe was measured against is gone")
