"""Platform-wide UTC timestamp serialization (S17).

Naive datetimes (the DB stores naive UTC) must leave the API marked as UTC so
browsers do not parse them as local time. Aware datetimes and plain dates are
left alone.
"""
import datetime as dt
from typing import List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.utils.time_fmt import install_utc_json, iso_utc, utc_mark

NAIVE = dt.datetime(2026, 9, 29, 19, 41, 34)
NAIVE_US = dt.datetime(2026, 9, 29, 19, 41, 34, 123456)
AWARE = dt.datetime(2026, 9, 29, 14, 41, 34, tzinfo=dt.timezone(dt.timedelta(hours=-5)))
DAY = dt.date(2026, 9, 29)


class Item(BaseModel):
    id: int
    starts_at_local: Optional[dt.datetime] = None
    created_at: dt.datetime
    updated_at: Optional[dt.datetime] = None
    due_on: Optional[dt.date] = None


class Wrapper(BaseModel):
    items: List[Item]
    generated_at: dt.datetime


def _client():
    install_utc_json()
    app = FastAPI()

    @app.get("/dict")
    def dict_route():
        return {"at": NAIVE, "us": NAIVE_US, "aware": AWARE, "day": DAY,
                "nested": [{"at": NAIVE}], "none": None,
                "starts_at_local": NAIVE, "slots": [{"starts_at": NAIVE, "start_meeting_local": NAIVE}], "s": "2026-09-29T19:41:34"}

    @app.get("/annotated")
    def annotated_route() -> dict:
        return {"at": NAIVE, "day": DAY, "aware": AWARE}

    @app.get("/model", response_model=Item)
    def model_route():
        return Item(id=1, created_at=NAIVE, updated_at=AWARE, due_on=DAY, starts_at_local=NAIVE)

    @app.get("/model-from-dict", response_model=Wrapper)
    def model_from_dict():
        return {"items": [{"id": 2, "created_at": NAIVE}], "generated_at": NAIVE_US}

    @app.get("/list", response_model=List[Item])
    def list_route():
        return [Item(id=3, created_at=NAIVE)]

    return TestClient(app)


def test_dict_route_marks_naive_utc():
    body = _client().get("/dict").json()
    assert body["at"] == "2026-09-29T19:41:34Z"
    assert body["us"] == "2026-09-29T19:41:34.123456Z"
    assert body["nested"][0]["at"] == "2026-09-29T19:41:34Z"
    assert body["none"] is None
    # strings are never rewritten, even if they look like timestamps
    assert body["s"] == "2026-09-29T19:41:34"


def test_aware_datetime_untouched():
    c = _client()
    assert c.get("/dict").json()["aware"] == "2026-09-29T14:41:34-05:00"
    assert c.get("/model").json()["updated_at"] == "2026-09-29T14:41:34-05:00"
    assert c.get("/annotated").json()["aware"] == "2026-09-29T14:41:34-05:00"


def test_local_wall_clock_fields_stay_naive():
    """*_local values are server-resolved wall clocks, not UTC instants."""
    c = _client()
    body = c.get("/dict").json()
    assert body["starts_at_local"] == "2026-09-29T19:41:34"
    assert body["slots"][0]["start_meeting_local"] == "2026-09-29T19:41:34"
    assert body["slots"][0]["starts_at"] == "2026-09-29T19:41:34Z"
    assert c.get("/model").json()["starts_at_local"] == "2026-09-29T19:41:34"


def test_date_untouched():
    c = _client()
    assert c.get("/dict").json()["day"] == "2026-09-29"
    assert c.get("/model").json()["due_on"] == "2026-09-29"
    assert c.get("/annotated").json()["day"] == "2026-09-29"


def test_response_model_route_marks_naive_utc():
    c = _client()
    assert c.get("/model").json()["created_at"] == "2026-09-29T19:41:34Z"
    body = c.get("/model-from-dict").json()
    assert body["items"][0]["created_at"] == "2026-09-29T19:41:34Z"
    assert body["generated_at"] == "2026-09-29T19:41:34.123456Z"
    assert c.get("/list").json()[0]["created_at"] == "2026-09-29T19:41:34Z"
    # return-annotation (-> dict) goes through pydantic too
    assert c.get("/annotated").json()["at"] == "2026-09-29T19:41:34Z"


def test_route_objects_not_mutated():
    install_utc_json()
    item = Item(id=9, created_at=NAIVE)
    payload = {"at": NAIVE, "rows": [item]}
    out = utc_mark(payload)
    assert payload["at"].tzinfo is None and item.created_at.tzinfo is None
    assert out["at"].tzinfo is dt.timezone.utc
    assert out["rows"][0].created_at.tzinfo is dt.timezone.utc
    # unchanged structures are returned as-is (no needless copies)
    same = {"a": 1, "b": [DAY, "x"]}
    assert utc_mark(same) is same


def test_iso_utc_helper():
    assert iso_utc(NAIVE) == "2026-09-29T19:41:34Z"
    assert iso_utc(AWARE) == "2026-09-29T14:41:34-05:00"
    assert iso_utc(DAY) == "2026-09-29"
    assert iso_utc(None) is None
    assert iso_utc("already") == "already"
    # never double-suffix
    assert not iso_utc(NAIVE).endswith("ZZ")


def test_install_is_idempotent():
    install_utc_json()
    install_utc_json()
    body = _client().get("/model").json()
    assert body["created_at"] == "2026-09-29T19:41:34Z"

