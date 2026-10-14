"""Oct 14: 850, 478 and others were missing from the area code table, so
approved families were refused texts forever and nothing said so."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services import contact_hours as C


def test_every_area_code_on_the_sci_list_has_a_zone():
    for code in ("850 478 229 404 407 423 617 205 251 334 706 202 574 336 262 702 256 "
                 "310 315 417 610 646 650 678 703 705 304 305 318 361 501 505 510 541 559 "
                 "561 562 206 208 210 254 267").split():
        assert C.AREA_CODE_ZONES.get(code), code


def test_arizona_and_hawaii_and_el_paso():
    assert C.AREA_CODE_ZONES["602"] == [C._MT_NO_DST]
    assert C.AREA_CODE_ZONES["808"] == [C._HT]
    assert C.AREA_CODE_ZONES["915"] == [C._MT]


def test_a_panhandle_number_is_textable_mid_afternoon():
    lead = SimpleNamespace(state=None, phone="18503410022", phone_raw=None, callback_phone=None)
    # 2026-10-14 19:00 UTC = 3 PM Eastern / 2 PM Central
    assert C.check(lead, datetime(2026, 10, 14, 19, 0))["permitted"] is True
