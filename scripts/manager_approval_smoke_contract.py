"""Fail-closed readers for the manager approval smoke (stdlib only).

The smoke reads ONE queue (`GET /sales/manager/approvals/queue`) and decides
ONLY through `POST /sales/manager/approvals/{id}/decision` with that row's
`expected_version`. These helpers raise `ContractError` rather than guess, so a
changed shape, a missing version, an unavailable count, or a false success
fails the smoke instead of passing it. No network, no DB, no provider.
"""

QUEUE_PATH = "/sales/manager/approvals/queue"
DECISION_PATH = "/sales/manager/approvals/%s/decision"

_QUEUE_KEYS = ("brand_sales_org_id", "authorized", "pending", "history",
               "pending_count", "actionable_count", "blocked_count")
_ITEM_KEYS = ("id", "kind", "status", "money", "version", "actionable", "blocker")


class ContractError(AssertionError):
    pass


def _need(cond, msg):
    if not cond:
        raise ContractError(msg)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check_money(m, label="money"):
    """Every money field is {cents:int, display:str} or None; display matches cents."""
    _need(isinstance(m, dict), "%s is not an object" % label)
    for k, v in m.items():
        if v is None or not isinstance(v, dict) or "cents" not in v:
            continue
        _need(_is_int(v["cents"]), "%s.%s cents is not an integer" % (label, k))
        c = v["cents"]
        want = "%s%s.%02d" % ("-" if c < 0 else "", format(abs(c) // 100, ","), abs(c) % 100)
        _need(v.get("display") == want, "%s.%s display %r != %r" % (label, k, v.get("display"), want))


def read_queue(payload, brand_id):
    """Validate the queue shape for `brand_id`; return its pending items."""
    _need(isinstance(payload, dict), "queue is not an object")
    for k in _QUEUE_KEYS:
        _need(k in payload, "queue missing %r (unexpected shape)" % k)
    _need(payload["authorized"] is True, "queue not authorized")
    _need(payload["brand_sales_org_id"] == brand_id, "queue is for another brand")
    pending = payload["pending"]
    _need(isinstance(pending, list), "pending is not a list")
    for k in ("pending_count", "actionable_count", "blocked_count"):
        _need(_is_int(payload[k]), "%s is unavailable (%r)" % (k, payload[k]))
    _need(payload["pending_count"] == len(pending), "pending_count != len(pending)")
    _need(payload["actionable_count"] + payload["blocked_count"] == payload["pending_count"],
          "actionable + blocked != pending")
    for it in pending:
        _need(isinstance(it, dict), "pending item is not an object")
        for k in _ITEM_KEYS:
            _need(k in it, "pending item missing %r" % k)
        _need(it["status"] == "pending", "non-pending row in pending list")
        _need(isinstance(it["version"], str) and it["version"].startswith("v1."),
              "pending item %s has no usable version" % it["id"])
        check_money(it["money"], "money[%s]" % it["id"])
    return pending


def find_item(pending, request_id):
    hits = [i for i in pending if i["id"] == request_id]
    _need(len(hits) == 1, "request %s not exactly once in queue" % request_id)
    return hits[0]


def decision_request(item, approve, note=None):
    """(path, json body) for one decision, from the queue row, versioned."""
    _need(item.get("version"), "refusing to decide without a version")
    body = {"approve": bool(approve), "expected_version": item["version"]}
    if note:
        body["note"] = note
    return DECISION_PATH % item["id"], body


def read_decision(status_code, body, approve):
    """Classify a decision response. Returns 'applied' or 'replay'; raises otherwise.

    409 is stale and 404 is not found; neither is success.
    """
    _need(status_code != 409, "409 stale: request changed or already decided")
    _need(status_code != 404, "404: request not found in this brand")
    _need(status_code == 200, "decision refused with HTTP %s" % status_code)
    _need(isinstance(body, dict) and body.get("ok") is True, "200 without ok:true (false success)")
    _need(body.get("status") == ("approved" if approve else "denied"),
          "status %r does not match the decision" % body.get("status"))
    if body.get("replay") is True:
        return "replay"
    _need(body.get("replay") is False, "replay flag missing")
    _need(isinstance(body.get("applied"), bool), "applied flag missing")
    return "applied"
