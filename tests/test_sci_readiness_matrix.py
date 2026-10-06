"""SCI POC readiness matrix, data/routing layer. Stdlib-only (no DB, no Twilio):
every case is deterministic and uses synthetic inputs. SMS/voice/cadence
scenarios live in test_sci_regional_pools.py and test_sci_platinum.py."""
import csv
import os

import pytest

from app.services.programs import regional_pools as rp

_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "sci_campuses.csv")
with open(_CSV, encoding="utf-8") as _f:
    ROWS = list(csv.DictReader(_f))
AREAS = ["205", "334", "850", "251", "706", "318"]


@pytest.mark.parametrize("area", AREAS)
def test_pool_resolves_and_has_members(area):
    pool = rp.pool_for_area_code(area)
    assert pool and rp.sender_pool(area) == pool["pool_id"]
    assert rp.pool_members(ROWS)[pool["pool_id"]]


@pytest.mark.parametrize("area", AREAS)
def test_pool_number_label_round_trips(area):
    pool = rp.POOLS[area]
    rec = type("N", (), {"label": rp.POOL_LABEL_PREFIX + pool["pool_id"]})()
    assert rp.pool_for_phone_number(rec) == pool


@pytest.mark.parametrize("area", AREAS)
def test_known_sender_routes_to_own_entity_not_queue(area):
    ent = next(r["Location"] for r in ROWS if r["Area Code"] == area)
    d = rp.route_inbound(ent, rp.POOLS[area]["pool_id"])
    assert d["location"] == ent and d["queue"] is None


@pytest.mark.parametrize("area", AREAS)
def test_unknown_sender_goes_to_regional_review_without_location(area):
    pid = rp.POOLS[area]["pool_id"]
    d = rp.route_inbound(None, pid)
    assert d["location"] is None and d["queue"] == "regional_review:" + pid


@pytest.mark.parametrize("area", AREAS)
def test_shared_pool_keeps_entities_distinct(area):
    names = rp.pool_members(ROWS)[rp.POOLS[area]["pool_id"]]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("bad", [None, "", "   ", "844", "999", "214"])
def test_unverified_or_foreign_area_code_has_no_sender(bad):
    assert rp.pool_for_area_code(bad) is None
    with pytest.raises(LookupError):
        rp.sender_pool(bad)


def test_oaklawn_unresolved_belongs_to_no_pool():
    r = next(r for r in ROWS if r["Location"].startswith("Oaklawn"))
    assert r["Area Code"] == "" and r["Address Status"] == "unverified"
    assert all(r["Location"] not in v for v in rp.pool_members(ROWS).values())


def test_844_is_overflow_only_never_a_pool():
    assert rp.BACKUP_TOLL_FREE == "+18449172171"
    assert rp.pool_for_area_code("844") is None
    assert all(not p["pool_id"].startswith("pool-844") for p in rp.POOLS.values())


def test_pine_crest_west_verified_in_251():
    r = next(r for r in ROWS if r["Location"] == "Pine Crest Cemetery West")
    assert r["Area Code"] == "251" and r["Address Status"] == "verified"
    assert r["Location"] in rp.pool_members(ROWS)["pool-251-mobile"]


def test_38_entities_pooled_of_39():
    assert len(ROWS) == 39
    assert sum(len(v) for v in rp.pool_members(ROWS).values()) == 38


def test_every_entity_name_unique():
    names = [r["Location"] for r in ROWS]
    assert len(names) == len(set(names))


def test_foreign_pool_label_is_not_a_pool():
    assert rp.pool_for_phone_number(type("N", (), {"label": "pool:pool-999-nowhere"})()) is None
    assert rp.pool_for_phone_number(type("N", (), {"label": None})()) is None
