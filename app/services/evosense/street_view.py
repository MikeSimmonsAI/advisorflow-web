"""Street-level photo of THE property's address (Google Street View Static API).

A real photograph of the location, taken by Google on the date its metadata
reports - never a stock or "representative" image. Only shown when Google says
imagery exists for that exact location; otherwise the page keeps saying
"Property image unavailable".

Cost: the metadata check is free; each image is billed by Google (Street View
Static API). Images are cached in memory per address so re-opening a page does
not buy the same picture twice.

Key: GOOGLE_STREET_VIEW_API_KEY, else GOOGLE_MAPS_API_KEY, else
GOOGLE_PLACES_API_KEY. The key must have the "Street View Static API" enabled.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMG_URL = "https://maps.googleapis.com/maps/api/streetview"
TIMEOUT = 15
_CACHE: "OrderedDict[str, Tuple[bytes, Dict[str, Any]]]" = OrderedDict()
_META: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_LOCK = threading.Lock()
_MAX = 300


def api_key() -> Optional[str]:
    for name in ("GOOGLE_STREET_VIEW_API_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_PLACES_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return None


def location_for(prop) -> Optional[str]:
    """The address as one line (street, city/ZIP, state). Coordinates are used
    only when there is no street address."""
    street = (getattr(prop, "street_address", None) or "").strip()
    if street:
        parts = [street, (getattr(prop, "city", None) or "").strip(),
                 ((getattr(prop, "state", None) or "TX") + " " + (getattr(prop, "zip_code", None) or "")).strip()]
        if not parts[1] and getattr(prop, "county", None):
            parts[1] = "%s County" % prop.county
        return ", ".join(p for p in parts if p)
    lat, lng = getattr(prop, "latitude", None), getattr(prop, "longitude", None)
    if lat is not None and lng is not None:
        return "%s,%s" % (lat, lng)
    return None


def links_for(prop) -> Dict[str, Optional[str]]:
    """Free links (no key, no cost) that open the address in Google Maps."""
    loc = location_for(prop)
    if not loc:
        return {"maps_url": None, "street_view_url": None}
    q = urllib.parse.quote(loc)
    lat, lng = getattr(prop, "latitude", None), getattr(prop, "longitude", None)
    sv = ("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=%s,%s" % (lat, lng)
          if lat is not None and lng is not None else "https://www.google.com/maps/search/?api=1&query=%s" % q)
    return {"maps_url": "https://www.google.com/maps/search/?api=1&query=%s" % q, "street_view_url": sv}


def _get(url: str) -> Tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "EvoSense/1.0 (EvoSys)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - fixed Google host
        return r.status, r.read(), r.headers.get("Content-Type", "")


def metadata(prop) -> Dict[str, Any]:
    """{available, reason, date, location}. Free call; cached."""
    key = api_key()
    loc = location_for(prop)
    out: Dict[str, Any] = {"available": False, "reason": None, "date": None, "location": loc,
                           "source": "Google Street View", **links_for(prop)}
    if not loc:
        out["reason"] = "no_address"
        return out
    if not key:
        out["reason"] = "not_configured"
        return out
    with _LOCK:
        if loc in _META:
            return dict(_META[loc])
    try:
        _, body, _ = _get("%s?%s" % (META_URL, urllib.parse.urlencode(
            {"location": loc, "source": "outdoor", "key": key})))
        m = json.loads(body.decode("utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001 - a failed lookup is "unavailable", never a guess
        out["reason"] = "lookup_failed: %s" % type(exc).__name__
        return out
    status = m.get("status")
    if status == "OK":
        out.update(available=True, date=m.get("date"))
    elif status in ("REQUEST_DENIED",):
        out["reason"] = "key_rejected"   # Street View Static API not enabled for the key
        out["detail"] = (m.get("error_message") or "")[:200]
    else:
        out["reason"] = "no_imagery" if status == "ZERO_RESULTS" else (status or "unknown").lower()
    with _LOCK:
        _META[loc] = dict(out)
        while len(_META) > _MAX * 3:
            _META.popitem(last=False)
    return out


def image(prop, size: str = "640x400") -> Tuple[Optional[bytes], Dict[str, Any]]:
    meta = metadata(prop)
    if not meta.get("available"):
        return None, meta
    w, _, h = (size or "640x400").partition("x")
    try:
        w, h = max(100, min(int(w), 640)), max(100, min(int(h), 640))
    except ValueError:
        w, h = 640, 400
    ck = "%s|%sx%s" % (meta["location"], w, h)
    with _LOCK:
        if ck in _CACHE:
            _CACHE.move_to_end(ck)
            return _CACHE[ck][0], meta
    try:
        status, body, ctype = _get("%s?%s" % (IMG_URL, urllib.parse.urlencode(
            {"location": meta["location"], "size": "%sx%s" % (w, h), "source": "outdoor",
             "return_error_code": "true", "key": api_key()})))
    except Exception as exc:  # noqa: BLE001
        return None, {**meta, "available": False, "reason": "image_failed: %s" % type(exc).__name__}
    if status != 200 or not ctype.startswith("image/"):
        return None, {**meta, "available": False, "reason": "image_failed"}
    with _LOCK:
        _CACHE[ck] = (body, meta)
        while len(_CACHE) > _MAX:
            _CACHE.popitem(last=False)
    return body, meta
