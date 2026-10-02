"""UTC-explicit datetime serialization for everything leaving the API.

The database stores *naive* UTC datetimes (``datetime.utcnow()``). Serialized
as-is they become ``"2026-09-29T19:41:34"`` — and every browser parses a
zone-less ISO datetime as LOCAL time, so in US/Central every timestamp shown
in the UI was 5 hours off and relative times said "just now" for hours.

This module fixes that at the API boundary:

* :func:`iso_utc` — helper for code that builds JSON by hand
  (``"created_at": iso_utc(row.created_at)``). Naive datetimes are treated as
  UTC and rendered with a trailing ``Z``; aware datetimes keep their offset;
  ``date`` objects and ``None`` pass through untouched (``None`` -> ``None``).
* :func:`install_utc_json` — one-time, idempotent process hook that makes
  FastAPI's two serialization paths mark naive datetimes as UTC:

  1. ``jsonable_encoder`` (routes that return plain dicts/lists with no
     ``response_model`` / return annotation) — via ``ENCODERS_BY_TYPE``.
  2. pydantic v2 ``response_model`` / return-annotation serialization
     (``ModelField.serialize``) — the validated value is copied with naive
     datetimes replaced by UTC-aware ones before pydantic dumps it to JSON,
     so pydantic emits ``...Z``. The route's own objects are never mutated.

Aware datetimes are unchanged; ``date`` / ``time`` values are unchanged.

WALL-CLOCK EXCEPTION: the scheduling code deliberately sends some values as a
naive *local wall clock* already resolved server-side (``starts_at_local``,
``ends_at_local``, ``start_meeting_local``, ``start_visitor_local``,
``now_local`` ...; see frontend/src/pages/sales/calendarTime.js). Those are not
UTC instants, so any dict key or model field whose name ends in ``_local`` is
left zone-less exactly as before.
CSV exports, webhook payloads and anything built with ``json.dumps`` are not
affected (they do not go through these encoders).
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

UTC = _dt.timezone.utc

__all__ = ["iso_utc", "as_utc", "utc_mark", "install_utc_json"]


def as_utc(value: Any) -> Any:
    """Return ``value`` as an aware UTC datetime if it is a naive datetime."""
    if isinstance(value, _dt.datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def iso_utc(value: Any) -> Any:
    """ISO-8601 string for a datetime, explicitly UTC when naive.

    - naive ``datetime`` -> ``"2026-09-29T19:41:34Z"`` (assumed UTC)
    - aware ``datetime`` -> ``value.isoformat()`` (offset preserved)
    - ``date`` -> ``value.isoformat()`` (no zone; a calendar day)
    - ``None`` -> ``None``; strings pass through unchanged
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        return value.isoformat()
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    return value


def _encode_datetime(value: _dt.datetime) -> str:
    return iso_utc(value)


_SCALARS = (str, int, float, bool, bytes, type(None))
WALL_SUFFIX = "_local"


def _is_wall_key(key: Any) -> bool:
    return isinstance(key, str) and key.endswith(WALL_SUFFIX)


def utc_mark(value: Any, _mark: bool = True) -> Any:
    """Return a copy of ``value`` with every naive datetime made UTC-aware.

    Containers/models are only copied when something inside changed, and the
    input is never mutated (it may be a cached object owned by the caller).
    """
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, _dt.datetime):
        if _mark and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, dict):
        changed = False
        out = {}
        for k, v in value.items():
            if _is_wall_key(k):
                # naive local wall clock: keep it zone-less (as a string so the
                # jsonable_encoder datetime hook cannot add a Z afterwards)
                nv = (v.isoformat() if isinstance(v, _dt.datetime) and v.tzinfo is None
                      else v)
            else:
                nv = utc_mark(v, _mark)
            if nv is not v:
                changed = True
            out[k] = nv
        if not changed:
            return value
        if type(value) is not dict:
            try:
                return type(value)(out)
            except Exception:
                return out
        return out
    if isinstance(value, (list, tuple)):
        items = [utc_mark(v, _mark) for v in value]
        if all(a is b for a, b in zip(items, value)):
            return value
        if isinstance(value, tuple):
            try:
                return type(value)(items) if type(value) is tuple else type(value)(*items)
            except Exception:
                return tuple(items)
        return items
    try:
        from pydantic import BaseModel
    except Exception:  # pragma: no cover
        return value
    if isinstance(value, BaseModel):
        updates = {}
        for name, v in value.__dict__.items():
            if _is_wall_key(name):
                continue  # pydantic serializes the naive wall clock as-is
            nv = utc_mark(v, _mark)
            if nv is not v:
                updates[name] = nv
        if not updates:
            return value
        fields_set = set(value.model_fields_set)
        new = value.model_copy(update=updates)
        try:
            object.__setattr__(new, "__pydantic_fields_set__", fields_set)
        except Exception:
            pass
        return new
    return value


_INSTALLED = False


def install_utc_json() -> None:
    """Patch FastAPI response serialization so naive datetimes carry UTC.

    Idempotent; safe to call from app startup and from tests.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    # 1) jsonable_encoder path (dict/list responses without a response field).
    import fastapi.encoders as enc

    enc.ENCODERS_BY_TYPE[_dt.datetime] = _encode_datetime
    # Subclasses (freezegun FakeDatetime, pendulum, ...) are matched through
    # encoders_by_class_tuples, which was built at import time; put ours first.
    try:
        old = dict(enc.encoders_by_class_tuples)
        enc.encoders_by_class_tuples.clear()
        enc.encoders_by_class_tuples[_encode_datetime] = (_dt.datetime,)
        for fn, classes in old.items():
            if fn is _encode_datetime:
                continue
            enc.encoders_by_class_tuples[fn] = classes
    except Exception:  # pragma: no cover - defensive
        pass

    # The routing layer calls jsonable_encoder(response_content) for routes
    # without a response field; pre-walk so *_local wall clocks are protected.
    import fastapi.routing as routing

    orig_je = routing.jsonable_encoder
    if not getattr(orig_je, "_utc_json", False):

        def _je(obj, *args, **kwargs):  # type: ignore[no-untyped-def]
            return orig_je(utc_mark(obj, False), *args, **kwargs)

        _je._utc_json = True  # type: ignore[attr-defined]
        routing.jsonable_encoder = _je  # type: ignore[assignment]

    # 2) pydantic v2 response_model / return-annotation path.
    try:
        from fastapi._compat import ModelField
    except Exception:  # pragma: no cover
        ModelField = None  # type: ignore
    if ModelField is not None and hasattr(ModelField, "serialize"):
        original = ModelField.serialize
        if not getattr(original, "_utc_json", False):

            def serialize(self, value, *, mode="json", **kwargs):  # type: ignore[no-untyped-def]
                if mode == "json":
                    value = utc_mark(value)
                return original(self, value, mode=mode, **kwargs)

            serialize._utc_json = True  # type: ignore[attr-defined]
            serialize.__wrapped__ = original  # type: ignore[attr-defined]
            ModelField.serialize = serialize  # type: ignore[assignment]

    _INSTALLED = True


# For pydantic response models: a datetime field that serialises like
# iso_utc (naive -> "...Z"). FastAPI's dict encoder is patched in app.main;
# response_model fields go through pydantic's own serializer instead.
from typing import Annotated as _Annotated  # noqa: E402
from pydantic import PlainSerializer as _PlainSerializer  # noqa: E402

UtcDateTime = _Annotated[_dt.datetime, _PlainSerializer(iso_utc, return_type=str, when_used="json-unless-none")]
